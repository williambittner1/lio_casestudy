"""Conservative text matches for an annotated copy of an uploaded quote.

The matches are review aids, not AI provenance. Ambiguous matches and unsupported
page geometry are deliberately left unmarked.
"""
import csv
import io
import logging
import re
import subprocess
import unicodedata
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Highlight
from pypdf.generic import ArrayObject, FloatObject
from PIL import Image

from app.services.pdf import MAX_OCR_PAGES, _needs_ocr

COLORS = {"vendor": "55b5cb", "item": "efbb52"}
MAX_PREVIEW_PAGES = 20
logger = logging.getLogger(__name__)


def _normalise(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _values(data: dict) -> list[tuple[str, str, str]]:
    result = []
    for label, value in (("Vendor name", data.get("vendorName")), ("VAT ID", data.get("vatId"))):
        if isinstance(value, str) and len(_normalise(value)) >= 5:
            result.append((label, value[:200], "vendor"))
    try:
        total = Decimal(str(data.get("totalCost")))
        if total.is_finite() and Decimal("0") < total < Decimal("1000000000"):
            result.append(("Total cost", f"{total:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."), "item"))
    except (InvalidOperation, ValueError):
        pass
    lines = [(f"Line {i}", line) for i, line in enumerate(data.get("orderLines", [])[:50], 1)]
    lines += [(f"Alternative {i}", line) for i, line in enumerate(data.get("alternativeOrderLines", [])[:50], 1)]
    for prefix, line in lines:
        if not isinstance(line, dict):
            continue
        description = line.get("positionDescription")
        if isinstance(description, str) and len(_normalise(description)) >= 6:
            result.append((f"{prefix} description", " ".join(description.split())[:500], "item"))
        unit = line.get("unit")
        if isinstance(unit, str) and unit.strip():
            result.append((f"{prefix} unit", unit.strip()[:30], "item"))
        for label, key in (("unit price", "unitPrice"), ("amount", "amount"), ("line total", "totalPrice")):
            try:
                price = Decimal(str(line.get(key)))
                if price.is_finite() and Decimal("0") < price < Decimal("1000000000"):
                    value = f"{price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
                    result.append((f"{prefix} {label}", value, "item"))
            except (InvalidOperation, ValueError):
                pass
    return result


def _find_matches(pages, value: str):
    needle = _normalise(value)
    matches = []
    if not needle:
        return matches
    for page_number, page in enumerate(pages):
        for line in page.findall(".//{*}line"):
            words = line.findall(".//{*}word")
            for start in range(len(words)):
                if not _normalise(words[start].text or ''):
                    continue
                text = ""
                for end in range(start, min(start + 12, len(words))):
                    text += _normalise(words[end].text or "")
                    if text == needle:
                        matches.append((page_number, words[start:end + 1]))
                        break
                    if not needle.startswith(text) or len(text) > len(needle):
                        break
    return matches


def _parse_number(value: str):
    """Read common quote number formats, refusing ambiguous thousands separators."""
    value = re.sub(r"\s|€|EUR", "", value, flags=re.IGNORECASE)
    if not re.fullmatch(r"[+-]?\d[\d.,]*", value):
        return None
    if "," in value and "." in value:
        decimal = "," if value.rfind(",") > value.rfind(".") else "."
        value = value.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in value or "." in value:
        separator = "," if "," in value else "."
        if value.count(separator) != 1:
            return None
        before, after = value.split(separator)
        # 1.000 may mean either one or one thousand: do not guess.
        if len(after) == 3:
            return None
        value = before + "." + after
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _numeric_matches(pages, value: str):
    number = _parse_number(value)
    if number is None:
        return []
    found = []
    for page_number, page in enumerate(pages):
        for word in page.findall(".//{*}word"):
            if _parse_number(word.text or "") == number:
                found.append((page_number, [word]))
    return found


def _unit_matches(pages, value: str):
    aliases = (
        {"piece", "pieces", "pc", "pcs", "stk", "stck", "stueck", "stück"},
        {"sqm", "qm", "m2", "squaremeter", "squaremeters"},
        {"lfm", "linearmeter", "linearmeters", "runningmeter", "runningmeters"},
        {"m", "meter", "metre"},
        {"flatfee", "flatrate", "lumpsum", "pauschale", "pauschal"},
    )
    unit = _normalise(value)
    accepted = next((group for group in aliases if unit in group), {unit})
    return [(page_number, [word])
            for page_number, page in enumerate(pages)
            for word in page.findall(".//{*}word")
            if _normalise(word.text or "") in accepted]


def _ocr_pages(pdf_bytes: bytes, reader: PdfReader):
    """Build searchable OCR word boxes in PDF coordinates for image-heavy pages."""
    pages = [ElementTree.Element('page', width=str(float(p.mediabox.width)), height=str(float(p.mediabox.height))) for p in reader.pages]
    candidates = []
    for index, page in enumerate(reader.pages):
        try:
            if _needs_ocr(page, page.extract_text() or ''):
                candidates.append(index)
        except Exception:
            logger.info('Could not inspect preview page for OCR', exc_info=True)
    for index in candidates[:MAX_OCR_PAGES]:
        page = reader.pages[index]
        if int(page.get('/Rotate', 0)) % 360 or page.cropbox != page.mediabox:
            continue
        try:
            image_bytes = subprocess.run(['pdftoppm', '-f', str(index + 1), '-l', str(index + 1), '-scale-to', '3000', '-png', '-singlefile'], input=pdf_bytes, capture_output=True, check=True, timeout=30).stdout
            with Image.open(io.BytesIO(image_bytes)) as image:
                pixel_width, pixel_height = image.size
                gray = image.convert('L').point(lambda value: 255 if value > 245 else 0)
                processed = io.BytesIO()
                gray.save(processed, format='PNG')
            tsv = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'deu+eng', 'tsv'], input=processed.getvalue(), capture_output=True, check=True, timeout=30).stdout.decode('utf-8', errors='replace')
            lines = {}
            for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
                word = (row.get('text') or '').strip()
                if not word or float(row.get('conf', '-1')) < 60:
                    continue
                key = (row['block_num'], row['par_num'], row['line_num'])
                line = lines.setdefault(key, ElementTree.SubElement(pages[index], 'line')) if key not in lines else lines[key]
                left, top, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
                x_scale = float(page.mediabox.width) / pixel_width
                y_scale = float(page.mediabox.height) / pixel_height
                element = ElementTree.SubElement(line, 'word', xMin=str(left*x_scale), xMax=str((left+w)*x_scale), yMin=str(top*y_scale), yMax=str((top+h)*y_scale))
                element.text = word
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            logger.info('OCR preview unavailable for page %s', index + 1, exc_info=True)
    return pages


def _anchor_matches(pages, ocr_pages, value):
    # A supplier name or VAT ID may be repeated in headers and footers. Each
    # exact occurrence has the same text, so show the first, not every copy.
    found = _find_matches(pages, value)
    if not found:
        found = _find_matches(ocr_pages, value)
    return sorted(found, key=lambda match: (match[0], float(match[1][0].get('yMin')),
                                             float(match[1][0].get('xMin'))))[:1]


def _description_anchor(pages, ocr_pages, value, line):
    words = value.split()[:35]

    def select(candidate):
        if len(_normalise(candidate)) < 6:
            return []
        found = _find_matches(pages, candidate)
        if not found:
            found = _find_matches(ocr_pages, candidate)
        if len(found) == 1:
            return found
        # Identical titles are common (a heading followed by the priced item,
        # or alternative items). Use an item price on the same row to choose.
        supported = []
        for anchor in found:
            for key in ('unitPrice', 'totalPrice'):
                try:
                    price = Decimal(str(line.get(key)))
                    if not price.is_finite() or price <= 0:
                        continue
                    rendered = f"{price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
                except (InvalidOperation, ValueError):
                    continue
                try:
                    if price == Decimal(str(line.get('amount'))):
                        # Otherwise a repeated quantity could masquerade as a price.
                        continue
                except (InvalidOperation, ValueError):
                    pass
                candidates = _numeric_matches(pages, rendered) + _numeric_matches(ocr_pages, rendered)
                if _same_row(candidates, anchor):
                    supported.append(anchor)
                    break
        return supported if len(supported) == 1 else []

    # Prefixes can be split over PDF lines; try shorter prefixes before looking
    # for a literal phrase further into a generated description.
    for size in range(min(4, len(words)), 0, -1):
        selected = select(' '.join(words[:size]))
        if selected:
            return selected
    for start in range(1, len(words) - 1):
        if all(len(_normalise(word)) >= 3 for word in words[start:start + 2]):
            selected = select(' '.join(words[start:start + 2]))
            if selected:
                return selected
    return []


def _same_row(matches, anchor):
    page_number, anchor_words = anchor
    anchor_y = (float(anchor_words[0].get('yMin')) + float(anchor_words[0].get('yMax'))) / 2
    anchor_height = float(anchor_words[0].get('yMax')) - float(anchor_words[0].get('yMin'))
    return [(page, words) for page, words in matches if page == page_number and
            abs((float(words[0].get('yMin')) + float(words[0].get('yMax'))) / 2 - anchor_y) <= max(6, anchor_height * .75)]


def _price_row_anchor(pages, line, all_lines):
    """Infer a unique item row from quantity and both printed prices.

    This does not establish that a translated description means the same thing.
    Require a numbered row with its description before quantity, and refuse
    repeated signatures or several candidate rows instead of guessing.
    """
    def signature(item):
        try:
            values = tuple(Decimal(str(item.get(key))) for key in ('unitPrice', 'totalPrice', 'amount'))
            return values if all(x.is_finite() and x > 0 for x in values) else None
        except (InvalidOperation, ValueError):
            return None

    expected = signature(line)
    if not expected or expected[2] in expected[:2] or sum(signature(item) == expected for item in all_lines) != 1:
        return []
    unit_price, line_total, quantity = expected
    def fmt(value):
        return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    unit_candidates = _numeric_matches(pages, fmt(unit_price))
    total_candidates = _numeric_matches(pages, fmt(line_total))
    quantity_candidates = _numeric_matches(pages, fmt(quantity))
    rows = []
    for candidate in unit_candidates:
        same_unit = _same_row(unit_candidates, candidate)
        same_total = _same_row(total_candidates, candidate)
        if unit_price == line_total:
            if len(same_unit) != 2:
                continue
            same_unit.sort(key=lambda match: float(match[1][0].get('xMin')))
            if candidate != same_unit[0]:
                continue
            first_price = float(same_unit[0][1][0].get('xMin'))
        else:
            if len(same_unit) != 1 or len(same_total) != 1:
                continue
            first_price = min(float(same_unit[0][1][0].get('xMin')), float(same_total[0][1][0].get('xMin')))
        page_number = candidate[0]
        row = _same_row([(page_number, [word]) for word in pages[page_number].findall('.//{*}word')], candidate)
        row.sort(key=lambda match: float(match[1][0].get('xMin')))
        if not row or _parse_number(row[0][1][0].text or '') is None:
            continue
        marker = row[0][1][0]
        quantities = [match for match in _same_row(quantity_candidates, candidate)
                      if float(marker.get('xMax')) < float(match[1][0].get('xMin')) < first_price]
        if not quantities:
            continue
        quantity_word = max(quantities, key=lambda match: float(match[1][0].get('xMin')))[1][0]
        description = [match[1][0] for match in row
                       if float(marker.get('xMax')) <= float(match[1][0].get('xMin')) < float(quantity_word.get('xMin'))]
        if not description or not any(any(c.isalpha() for c in (word.text or '')) for word in description):
            continue
        rows.append((page_number, description))
    return rows if len(rows) == 1 else []


def _final_total_matches(pages, value):
    """Match the amount only alongside an explicit final-total label."""
    amounts = _numeric_matches(pages, value)
    found = []
    for label in ('Gesamtbetrag brutto', 'Gesamtsumme', 'Endsumme', 'Grand total', 'Total amount due'):
        for anchor in _find_matches(pages, label):
            row_words = _same_row([(anchor[0], [word]) for word in pages[anchor[0]].findall('.//{*}word')], anchor)
            if any(_normalise(words[0].text or '') in {'netto', 'net', 'excl', 'excluding'} for _, words in row_words):
                continue
            after_label = [match for match in _same_row(amounts, anchor)
                           if float(match[1][0].get('xMin')) >= float(anchor[1][-1].get('xMax'))]
            if len(after_label) == 1:
                found.append(after_label[0])
    return found if len(found) == 1 else []


def _near_anchor(matches, anchor, field, same_price, collision_count, prices=()):
    nearby = _same_row(matches, anchor)
    if len(nearby) == 1 and collision_count == 1:
        return nearby
    if field == 'amount' and collision_count == 1 and len(nearby) > 1:
        row_prices = _same_row(prices, anchor)
        if row_prices:
            description_end = max(float(word.get('xMax')) for word in anchor[1])
            first_price = min(float(match[1][0].get('xMin')) for match in row_prices)
            between = [match for match in nearby if description_end <= float(match[1][0].get('xMin')) < first_price]
            if len(between) == 1:
                return between
    if len(nearby) == 2 and same_price and field in ('unit price', 'line total'):
        nearby.sort(key=lambda match: float(match[1][0].get('xMin')))
        return [nearby[0] if field == 'unit price' else nearby[-1]]
    return []


def make_highlighted_preview(pdf_bytes: bytes, data: dict) -> tuple[bytes, list[str], list[str], list[dict]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    # Poppler's coordinates are only mapped for ordinary, uncropped, unrotated pages.
    if len(reader.pages) > MAX_PREVIEW_PAGES:
        raise ValueError("The PDF has too many pages for highlighted preview")
    xml = subprocess.run(
        ["pdftotext", "-bbox-layout", "-", "-"], input=pdf_bytes,
        capture_output=True, check=True, timeout=30,
    ).stdout
    root = ElementTree.fromstring(xml)
    pages = root.findall(".//{*}page")
    ocr_pages = _ocr_pages(pdf_bytes, reader)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    matched, unmatched, regions = [], [], []
    marked_rectangles = set()
    values = _values(data)
    source_lines = {f"Line {i}": line for i, line in enumerate(data.get("orderLines", [])[:50], 1) if isinstance(line, dict)}
    source_lines.update({f"Alternative {i}": line for i, line in enumerate(data.get("alternativeOrderLines", [])[:50], 1) if isinstance(line, dict)})
    anchors, inferred = {}, set()
    for label, value, category in values:
        if category != 'item' or not label.endswith(' description'):
            continue
        prefix = label.rsplit(' ', 1)[0]
        anchors[prefix] = _description_anchor(pages, ocr_pages, value, source_lines[prefix])
        if not anchors[prefix]:
            anchors[prefix] = (_price_row_anchor(pages, source_lines[prefix], source_lines.values()) or
                               _price_row_anchor(ocr_pages, source_lines[prefix], source_lines.values()))
            if anchors[prefix]:
                inferred.add(prefix)
    for label, value, category in values:
        if category == 'vendor':
            matches = _anchor_matches(pages, ocr_pages, value)
        elif label == 'Total cost':
            matches = _final_total_matches(pages, value) or _final_total_matches(ocr_pages, value)
        elif label.endswith(' description'):
            matches = anchors.get(label.rsplit(' ', 1)[0], [])
        else:
            prefix, field = label.split(' ', 2)[:2], label.split(' ', 2)[2]
            prefix = ' '.join(prefix)
            anchor = anchors.get(prefix, [])
            same_price = (any(l == f'{prefix} unit price' and v == value for l, v, _ in values)
                          and any(l == f'{prefix} line total' and v == value for l, v, _ in values)
                          and not any(l == f'{prefix} amount' and v == value for l, v, _ in values))
            collision_count = sum(1 for l, v, _ in values if l.startswith(f'{prefix} ') and l.rsplit(' ', 1)[-1] != 'unit' and not l.endswith(' description') and v == value)
            if field == 'unit':
                collision_count = 1
            finder = _unit_matches if field == 'unit' else _numeric_matches
            candidates = finder(pages, value)
            line = source_lines.get(prefix, {})
            price_candidates = []
            for key in ('unitPrice', 'totalPrice'):
                try:
                    price = Decimal(str(line.get(key)))
                    if price.is_finite() and price > 0:
                        rendered = f"{price:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
                        price_candidates += _numeric_matches(pages, rendered) + _numeric_matches(ocr_pages, rendered)
                except (InvalidOperation, ValueError):
                    pass
            matches = _near_anchor(candidates, anchor[0], field, same_price, collision_count, price_candidates) if anchor else []
            if not matches and anchor:
                matches = _near_anchor(finder(ocr_pages, value), anchor[0], field, same_price, collision_count, price_candidates)
            if not matches and anchor and field == 'unit':
                amount_value = next((v for l, v, _ in values if l == f'{prefix} amount'), None)
                if amount_value:
                    amount_collision = sum(1 for l, v, _ in values if l.startswith(f'{prefix} ')
                                           and (l.endswith('unit price') or l.endswith('amount') or l.endswith('line total'))
                                           and v == amount_value)
                    for source in (pages, ocr_pages):
                        units = _same_row(_unit_matches(source, value), anchor[0])
                        quantities = _near_anchor(_numeric_matches(source, amount_value), anchor[0], 'amount', False, amount_collision)
                        if len(units) > 1 and len(quantities) == 1:
                            quantity_x = float(quantities[0][1][0].get('xMin'))
                            ranked = sorted(units, key=lambda match: abs(float(match[1][0].get('xMin')) - quantity_x))
                            distances = [abs(float(match[1][0].get('xMin')) - quantity_x) for match in ranked]
                            if distances[0] < distances[1]:
                                matches = ranked[:1]
                                break
            # "E.P." explicitly marks a single printed unit price, even when
            # the extracted total has the same numerical value (e.g. qty 1).
            if not matches and anchor and field == 'unit price' and same_price and collision_count == 2:
                for source in (pages, ocr_pages):
                    nearby = _same_row(_numeric_matches(source, value), anchor[0])
                    if len(nearby) == 1 and _same_row(_find_matches(source, 'E.P.'), anchor[0]):
                        matches = nearby
                        break
        if not matches:
            unmatched.append(label)
            continue
        placed = False
        for page_number, words in matches:
            page = reader.pages[page_number]
            if int(page.get("/Rotate", 0)) % 360 or page.cropbox != page.mediabox:
                continue
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            poppler_width, poppler_height = float(pages[page_number].get("width")), float(pages[page_number].get("height"))
            if abs(width - poppler_width) > 1 or abs(height - poppler_height) > 1:
                continue
            x0 = min(float(w.get("xMin")) for w in words) + float(page.mediabox.left)
            x1 = max(float(w.get("xMax")) for w in words) + float(page.mediabox.left)
            y0 = height - max(float(w.get("yMax")) for w in words) + float(page.mediabox.bottom)
            y1 = height - min(float(w.get("yMin")) for w in words) + float(page.mediabox.bottom)
            rect = (x0, y0, x1, y1)
            region = {"field": label, "page": page_number + 1, "category": category,
                            "x": (x0 - float(page.mediabox.left)) / width,
                            "y": (height - (y1 - float(page.mediabox.bottom))) / height,
                            "width": (x1 - x0) / width, "height": (y1 - y0) / height}
            if any(label.startswith(f'{prefix} ') for prefix in inferred):
                region["matchKind"] = "price_row"
            regions.append(region)
            key = (page_number, *(round(n, 2) for n in rect))
            if key not in marked_rectangles:
                quad = ArrayObject([FloatObject(n) for n in (x0, y1, x1, y1, x0, y0, x1, y0)])
                annotation = Highlight(rect=rect, quad_points=quad, highlight_color=COLORS[category])
                writer.add_annotation(page_number, annotation)
                marked_rectangles.add(key)
            placed = True
        (matched if placed else unmatched).append(label)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue(), matched, unmatched, regions
