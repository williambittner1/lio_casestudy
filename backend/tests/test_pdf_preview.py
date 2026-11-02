import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from PIL import Image

from app.core.deps import get_current_user
from app.routers.pdf import router
from app.services import pdf_preview


def pdf_page(rotate=0):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=300)
    if rotate:
        page.rotate(rotate)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_preview_marks_matching_vendor_and_item_without_modifying_original(monkeypatch):
    original = pdf_page()
    xml = b'''<html xmlns="http://www.w3.org/1999/xhtml"><body><page width="200" height="300"><line>
      <word xMin="10" xMax="36" yMin="20" yMax="30">Acme</word>
      <word xMin="40" xMax="78" yMin="20" yMax="30">GmbH</word>
      </line><line><word xMin="10" xMax="50" yMin="40" yMax="50">Machine</word>
      <word xMin="55" xMax="65" yMin="40" yMax="50">2,00</word>
      <word xMin="70" xMax="100" yMin="40" yMax="50">120,00</word>
      <word xMin="110" xMax="140" yMin="40" yMax="50">120,00</word></line>
      <line><word xMin="10" xMax="40" yMin="80" yMax="90">2,00</word></line>
      </page></body></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, "_ocr_pages", lambda *args: [])
    result, matched, unmatched, regions = pdf_preview.make_highlighted_preview(original, {
        "vendorName": "Acme GmbH", "vatId": "DE123456789",
        "orderLines": [{"positionDescription": "Machine", "unitPrice": 120, "amount": 2, "totalPrice": 120}],
    })
    assert matched == ["Vendor name", "Line 1 description", "Line 1 unit price", "Line 1 amount", "Line 1 line total"]
    assert unmatched == ["VAT ID"]
    assert regions[0] == {"field": "Vendor name", "page": 1, "category": "vendor",
                          "x": 0.05, "y": 20 / 300, "width": 68 / 200, "height": 10 / 300}
    assert {r["field"] for r in regions} == set(matched)
    annotations = PdfReader(io.BytesIO(result)).pages[0]["/Annots"]
    assert len(annotations) == 5
    assert annotations[0].get_object()["/Subtype"] == "/Highlight"
    assert all("/Contents" not in annotation.get_object() for annotation in annotations)
    assert "/Annots" not in PdfReader(io.BytesIO(original)).pages[0]


def test_rotated_page_is_left_unmarked(monkeypatch):
    xml = b'<html><page width="200" height="300"><line><word xMin="10" xMax="80" yMin="20" yMax="30">SupplierGmbH</word></line></page></html>'
    monkeypatch.setattr(pdf_preview.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, "_ocr_pages", lambda *args: [])
    result, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(90), {"vendorName": "SupplierGmbH"})
    assert not matched
    assert unmatched == ["Vendor name"]
    assert not regions
    assert "/Annots" not in PdfReader(io.BytesIO(result)).pages[0]


def test_identical_amounts_are_linked_only_to_their_own_item(monkeypatch):
    xml = b'''<html><page width="200" height="300">
      <line><word xMin="10" xMax="45" yMin="20" yMax="30">Picture</word><word xMin="80" xMax="90" yMin="20" yMax="30">1,00</word></line>
      <line><word xMin="10" xMax="50" yMin="80" yMax="90">Shipping</word><word xMin="80" xMax="90" yMin="80" yMax="90">1,00</word></line>
      <line><word xMin="10" xMax="20" yMin="150" yMax="160">1,00</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, _, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Picture', 'amount': 1}, {'positionDescription': 'Shipping', 'amount': 1}]})
    amounts = {r['field']: r for r in regions if r['field'].endswith('amount')}
    assert amounts['Line 1 amount']['y'] == 20 / 300
    assert amounts['Line 2 amount']['y'] == 80 / 300
    assert len(amounts) == 2


def test_ambiguous_description_does_not_claim_a_numeric_source(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line><word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word><word xMin="80" xMax="90" yMin="20" yMax="30">1,00</word></line><line><word xMin="10" xMax="50" yMin="80" yMax="90">Machine</word><word xMin="80" xMax="90" yMin="80" yMax="90">1,00</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [{'positionDescription': 'Machine', 'amount': 1}]})
    assert not matched and not regions
    assert 'Line 1 amount' in unmatched


def test_translated_description_uses_unique_numbered_price_row_and_final_total(monkeypatch):
    xml = b'''<html><page width="600" height="300">
      <line><word xMin="5" xMax="10" yMin="20" yMax="30">1</word><word xMin="20" xMax="75" yMin="20" yMax="30">Machine</word><word xMin="80" xMax="95" yMin="20" yMax="30">450</word><word xMin="200" xMax="205" yMin="20" yMax="30">1</word><word xMin="215" xMax="230" yMin="20" yMax="30">Stk.</word><word xMin="300" xMax="340" yMin="20" yMax="30">15.000,00</word><word xMin="400" xMax="440" yMin="20" yMax="30">15.000,00</word></line>
      <line><word xMin="5" xMax="10" yMin="80" yMax="90">2</word><word xMin="20" xMax="70" yMin="80" yMax="90">Lieferung</word><word xMin="74" xMax="90" yMin="80" yMax="90">und</word><word xMin="94" xMax="125" yMin="80" yMax="90">Montage</word><word xMin="200" xMax="205" yMin="80" yMax="90">1</word><word xMin="215" xMax="255" yMin="80" yMax="90">Pauschale</word><word xMin="300" xMax="340" yMin="80" yMax="90">2.000,00</word><word xMin="400" xMax="440" yMin="80" yMax="90">2.000,00</word></line>
      <line><word xMin="280" xMax="345" yMin="150" yMax="160">Gesamtbetrag</word><word xMin="350" xMax="380" yMin="150" yMax="160">brutto</word><word xMin="400" xMax="450" yMin="150" yMax="160">20.230,00</word></line>
      </page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=300)
    output = io.BytesIO()
    writer.write(output)
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(output.getvalue(), {
        'totalCost': 20230, 'orderLines': [
            {'positionDescription': 'Machine 450', 'unitPrice': 15000, 'amount': 1, 'unit': 'pieces', 'totalPrice': 15000},
            {'positionDescription': 'Delivery and installation', 'unitPrice': 2000, 'amount': 1, 'unit': 'flat fee', 'totalPrice': 2000},
        ],
    })
    assert not unmatched
    assert len(matched) == 11
    amounts = {r['field']: r for r in regions if r['field'].endswith('amount')}
    assert amounts['Line 1 amount']['x'] == 200 / 600
    assert amounts['Line 2 amount']['x'] == 200 / 600
    assert next(r for r in regions if r['field'] == 'Line 2 description')['matchKind'] == 'price_row'
    assert next(r for r in regions if r['field'] == 'Total cost')['y'] == 150 / 300


def test_price_row_fallback_refuses_duplicate_pricing_signatures(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line>
      <word xMin="5" xMax="9" yMin="20" yMax="30">1</word><word xMin="15" xMax="50" yMin="20" yMax="30">Montage</word>
      <word xMin="60" xMax="65" yMin="20" yMax="30">1</word><word xMin="80" xMax="100" yMin="20" yMax="30">200,00</word>
      <word xMin="120" xMax="140" yMin="20" yMax="30">200,00</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    line = {'positionDescription': 'Installation service', 'amount': 1, 'unitPrice': 200, 'totalPrice': 200}
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [line, dict(line)]})
    assert not matched and not regions
    assert 'Line 1 description' in unmatched and 'Line 2 description' in unmatched


def test_total_cost_is_unmatched_without_an_explicit_final_total_label(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line>
      <word xMin="10" xMax="90" yMin="20" yMax="30">Zwischensumme</word>
      <word xMin="120" xMax="170" yMin="20" yMax="30">200,00</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, _ = pdf_preview.make_highlighted_preview(pdf_page(), {'totalCost': 200})
    assert not matched
    assert unmatched == ['Total cost']


def test_total_cost_does_not_treat_a_labelled_net_subtotal_as_gross(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line>
      <word xMin="10" xMax="70" yMin="20" yMax="30">Gesamtsumme</word>
      <word xMin="75" xMax="100" yMin="20" yMax="30">netto</word>
      <word xMin="120" xMax="170" yMin="20" yMax="30">200,00</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, _ = pdf_preview.make_highlighted_preview(pdf_page(), {'totalCost': 200})
    assert not matched
    assert unmatched == ['Total cost']


def test_repeated_item_titles_use_prices_to_find_the_right_row(monkeypatch):
    xml = b'''<html><page width="200" height="300">
      <line><word xMin="10" xMax="60" yMin="20" yMax="30">Machine</word><word xMin="61" xMax="85" yMin="20" yMax="30">deluxe</word></line>
      <line><word xMin="10" xMax="60" yMin="70" yMax="80">Machine</word><word xMin="61" xMax="85" yMin="70" yMax="80">deluxe</word><word xMin="90" xMax="95" yMin="70" yMax="80">1</word><word xMin="97" xMax="104" yMin="70" yMax="80">Stk.</word><word xMin="110" xMax="140" yMin="70" yMax="80">120.00</word><word xMin="150" xMax="180" yMin="70" yMax="80">120,00</word></line>
      <line><word xMin="10" xMax="60" yMin="130" yMax="140">Machine</word><word xMin="61" xMax="85" yMin="130" yMax="140">deluxe</word><word xMin="90" xMax="95" yMin="130" yMax="140">1,00</word><word xMin="97" xMax="104" yMin="130" yMax="140">Stk.</word><word xMin="110" xMax="140" yMin="130" yMax="140">150,00</word><word xMin="150" xMax="180" yMin="130" yMax="140">150,00</word></line>
      <line><word xMin="5" xMax="15" yMin="200" yMax="210">1</word></line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'The Machine deluxe, in blue', 'amount': 1, 'unit': 'pieces', 'unitPrice': 120, 'totalPrice': 120},
        {'positionDescription': 'Machine deluxe in red', 'amount': 1, 'unit': 'pieces', 'unitPrice': 150, 'totalPrice': 150},
    ]})
    assert not unmatched
    assert len(matched) == 10
    for index, y in [(1, 70 / 300), (2, 130 / 300)]:
        for field in ('description', 'amount', 'unit', 'unit price', 'line total'):
            region = next(r for r in regions if r['field'] == f'Line {index} {field}')
            assert region['y'] == y


def test_ambiguous_number_format_and_absent_unit_are_not_guessed(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line>
      <word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word>
      <word xMin="80" xMax="90" yMin="20" yMax="30">1.000</word>
      </line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, _ = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Machine', 'amount': 1, 'unit': 'pieces'},
    ]})
    assert matched == ['Line 1 description']
    assert set(unmatched) == {'Line 1 amount', 'Line 1 unit'}


def test_single_unit_price_marker_does_not_claim_a_printed_line_total(monkeypatch):
    xml = b'''<html><page width="200" height="300"><line>
      <word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word>
      <word xMin="60" xMax="65" yMin="20" yMax="30">1</word>
      <word xMin="80" xMax="110" yMin="20" yMax="30">120,00</word>
      <word xMin="120" xMax="140" yMin="20" yMax="30">E.P.</word>
      </line></page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, _ = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Machine', 'amount': 1, 'unitPrice': 120, 'totalPrice': 120},
    ]})
    assert set(matched) == {'Line 1 description', 'Line 1 amount', 'Line 1 unit price'}
    assert unmatched == ['Line 1 line total']


def test_unit_column_is_preferred_over_unit_in_description(monkeypatch):
    xml = b'''<html><page width="200" height="300">
      <line><word xMin="10" xMax="50" yMin="20" yMax="30">Picture</word>
      <word xMin="55" xMax="65" yMin="20" yMax="30">m2</word>
      <word xMin="110" xMax="120" yMin="20" yMax="30">1,28</word>
      <word xMin="130" xMax="140" yMin="20" yMax="30">qm</word>
      <word xMin="155" xMax="175" yMin="20" yMax="30">559,00</word></line>
      <line><word xMin="10" xMax="50" yMin="80" yMax="90">Border</word>
      <word xMin="110" xMax="120" yMin="80" yMax="90">4,80</word>
      <word xMin="130" xMax="140" yMin="80" yMax="90">Lfm</word></line>
      </page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Picture m2', 'amount': 1.28, 'unit': 'square meters', 'unitPrice': 559},
        {'positionDescription': 'Border', 'amount': 4.8, 'unit': 'linear meters'},
    ]})
    assert not unmatched
    assert 'Line 1 unit' in matched and 'Line 2 unit' in matched
    unit = next(r for r in regions if r['field'] == 'Line 1 unit')
    assert unit['x'] == 130 / 200


def test_repeated_title_and_price_remain_ambiguous(monkeypatch):
    xml = b'''<html><page width="200" height="300">
      <line><word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word><word xMin="80" xMax="100" yMin="20" yMax="30">120,00</word></line>
      <line><word xMin="10" xMax="50" yMin="80" yMax="90">Machine</word><word xMin="80" xMax="100" yMin="80" yMax="90">120,00</word></line>
      </page></html>'''
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Machine', 'unitPrice': 120},
    ]})
    assert matched == [] and regions == []
    assert set(unmatched) == {'Line 1 description', 'Line 1 unit price'}


def test_repeated_ocr_item_titles_are_scoped_by_price(monkeypatch):
    native = b'<html><page width="200" height="300" /></html>'
    ocr = pdf_preview.ElementTree.fromstring('''<root><page width="200" height="300">
      <line><word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word><word xMin="60" xMax="90" yMin="20" yMax="30">120,00</word></line>
      <line><word xMin="10" xMax="50" yMin="80" yMax="90">Machine</word><word xMin="60" xMax="90" yMin="80" yMax="90">150,00</word></line>
      </page></root>''')
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=native))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: list(ocr))
    _, matched, unmatched, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [
        {'positionDescription': 'Machine', 'unitPrice': 150},
    ]})
    assert matched == ['Line 1 description', 'Line 1 unit price']
    assert not unmatched
    assert all(r['y'] == 80 / 300 for r in regions)


def test_single_occurrence_is_not_assigned_to_multiple_equal_fields(monkeypatch):
    xml = b'<html><page width="200" height="300"><line><word xMin="10" xMax="50" yMin="20" yMax="30">Machine</word><word xMin="80" xMax="90" yMin="20" yMax="30">1,00</word></line></page></html>'
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: [])
    _, matched, unmatched, _ = pdf_preview.make_highlighted_preview(pdf_page(), {'orderLines': [{'positionDescription': 'Machine', 'amount': 1, 'unitPrice': 1, 'totalPrice': 1}]})
    assert matched == ['Line 1 description']
    assert set(unmatched) == {'Line 1 amount', 'Line 1 unit price', 'Line 1 line total'}


def test_ocr_word_boxes_can_locate_image_text(monkeypatch):
    native = b'<html><page width="200" height="300" /></html>'
    ocr = pdf_preview.ElementTree.fromstring('<root><page width="200" height="300"><line><word xMin="10" xMax="80" yMin="20" yMax="30">SupplierGmbH</word></line></page></root>')
    monkeypatch.setattr(pdf_preview.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=native))
    monkeypatch.setattr(pdf_preview, '_ocr_pages', lambda *args: list(ocr))
    _, matched, _, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'vendorName': 'SupplierGmbH'})
    assert matched == ['Vendor name']
    assert regions[0]['y'] == 20 / 300


def test_ocr_tsv_coordinates_are_converted_to_pdf_regions(monkeypatch):
    image = Image.new('RGB', (200, 300), 'white')
    png = io.BytesIO()
    image.save(png, format='PNG')
    tsv = b'level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t10\t20\t70\t10\t95\tSupplierGmbH\n'
    def run(command, **kwargs):
        if command[0] == 'pdftotext':
            return SimpleNamespace(stdout=b'<html><page width="200" height="300" /></html>')
        if command[0] == 'pdftoppm':
            return SimpleNamespace(stdout=png.getvalue())
        assert command[0] == 'tesseract'
        return SimpleNamespace(stdout=tsv)
    monkeypatch.setattr(pdf_preview.subprocess, 'run', run)
    monkeypatch.setattr(pdf_preview, '_needs_ocr', lambda *args: True)
    _, matched, _, regions = pdf_preview.make_highlighted_preview(pdf_page(), {'vendorName': 'SupplierGmbH'})
    assert matched == ['Vendor name']
    assert regions[0]['x'] == 10 / 200
    assert regions[0]['y'] == 20 / 300
    assert regions[0]['width'] == 70 / 200


def test_preview_endpoint_requires_auth_and_returns_field_summary(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr("app.routers.pdf.make_highlighted_preview", lambda pdf, data: (pdf, ["Vendor name"], [], []))
    client = TestClient(app)
    files = {"file": ("quote.pdf", pdf_page(), "application/pdf")}
    assert client.post("/pdf/highlight-preview", files=files, data={"extracted": "{}"}).status_code == 401
    app.dependency_overrides[get_current_user] = lambda: object()
    response = client.post("/pdf/highlight-preview", files=files, data={"extracted": "{}"})
    assert response.status_code == 200
    assert response.json()["matchedFields"] == ["Vendor name"]
    assert response.json()["pdfBase64"]
    assert client.post("/pdf/highlight-preview", files=files, data={"extracted": "invalid"}).status_code == 422
