import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup
from app.schemas.extraction import ExtractedVendorData, ExtractionResponse

logger = logging.getLogger(__name__)

FINAL_TOTAL_LINE = re.compile(
    r"^\s*(?:endsumme|gesamtsumme(?:\s*\([^)]*\))?|gesamtbetrag\s+brutto)"
    r"\s*[:\-]?\s*(?:€|EUR)?\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
    r"\s*(?:€|EUR)?\s*$",
    re.IGNORECASE,
)
ALTERNATIVE_BLOCK = re.compile(r"\balternativ(?:e|en)?\b.{0,350}?\(alt\.\)", re.IGNORECASE | re.DOTALL)
GERMAN_AMOUNT = re.compile(r"(?<!\d)(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)")
OCR_FOOTER_IDENTITY = re.compile(
    r"^[ \t]*([^|\n:]{2,80}?\b(?:GmbH|AG|UG|KG|Ltd))\s*\|[^\n]*\n"
    r"[^\n]*\bUSt[-\s]*IdNr\.?\s*:\s*(DE(?:[ \t]*\d){9})\b",
    re.IGNORECASE | re.MULTILINE,
)
MONEY = r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
NET_TOTAL_LINE = re.compile(
    rf"^[ \t]*(?:Nettosumme|Zwischensumme[ \t]+netto)[ \t]*:?[ \t]*(?:€|EUR)?[ \t]*{MONEY}[ \t]*(?:€|EUR)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
TAX_AMOUNT_LINE = re.compile(
    rf"^[ \t]*(?:Umsatzsteuer|(?:zzgl\.[ \t]*)?(?:gesetzl\.[ \t]*)?MwSt\.?)"
    rf"[ \t]*\(?[ \t]*(\d+(?:[,.]\d+)?)[ \t]*%[ \t]*\)?[ \t]*"
    rf"(?:€|EUR)?[ \t]*{MONEY}[ \t]*(?:€|EUR)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
TAX_RATE = re.compile(
    r"(?:Umsatzsteuer|USt\.?|MwSt\.?)[^\n%]{0,80}?(\d+(?:[,.]\d+)?)[ \t]*%",
    re.IGNORECASE,
)


def _printed_final_total(pdf_text: str) -> Decimal | None:
    """Return an unambiguous explicitly labelled German-format final amount.

    Ignore unlabelled totals and conflicting values: those need human review.
    """
    amounts = set()
    for line in pdf_text.splitlines():
        match = FINAL_TOTAL_LINE.fullmatch(line)
        if match:
            amounts.add(Decimal(match.group(1).replace(".", "").replace(",", ".")))
    return amounts.pop() if len(amounts) == 1 else None


def _verified_tax_rate(pdf_text: str, extracted: ExtractedVendorData) -> float | None:
    """Return a single rate only when the printed net, tax, gross and lines reconcile.

    OCR and mixed-rate/partial quotes need manual review rather than a guessed total.
    """
    native_text = pdf_text.split("Supplementary OCR text; verify against the original document:", 1)[0]
    net_matches = {Decimal(x.replace(".", "").replace(",", ".")) for x in NET_TOTAL_LINE.findall(native_text)}
    tax_matches = {
        (Decimal(rate.replace(",", ".")), Decimal(amount.replace(".", "").replace(",", ".")))
        for rate, amount in TAX_AMOUNT_LINE.findall(native_text)
    }
    all_rates = {Decimal(rate.replace(",", ".")) for rate in TAX_RATE.findall(native_text)}
    gross = _printed_final_total(native_text)
    if len(net_matches) != 1 or len(tax_matches) != 1 or len(all_rates) != 1 or gross is None:
        return None
    net = next(iter(net_matches))
    rate, tax = next(iter(tax_matches))
    if rate not in all_rates or rate < 0 or rate > 100 or net <= 0 or not extracted.order_lines:
        return None
    if any(line.amount <= 0 or line.total_price < 0 for line in extracted.order_lines):
        return None
    line_total = sum((Decimal(str(line.total_price)) for line in extracted.order_lines), Decimal(0))
    expected_tax = (net * rate / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if line_total != net or tax != expected_tax or net + tax != gross:
        return None
    if Decimal(str(extracted.total_cost)) != gross:
        return None
    return float(rate)


def _separate_marked_alternatives(pdf_text: str, extracted: ExtractedVendorData) -> None:
    """Move a uniquely price-matched item from an explicitly marked alternative block.

    This is only a narrow fallback for a model that missed a clear `(Alt.)` marker.
    If the price matches multiple base lines, leave the decision for human review.
    """
    for block in ALTERNATIVE_BLOCK.finditer(pdf_text):
        amounts = {
            Decimal(amount.replace(".", "").replace(",", "."))
            for amount in GERMAN_AMOUNT.findall(block.group())
        }
        matches = [line for line in extracted.order_lines
                   if line.unit_price > 0 and Decimal(str(line.unit_price)) in amounts]
        if len(matches) == 1:
            line = matches[0]
            extracted.order_lines.remove(line)
            extracted.alternative_order_lines.append(line)


def _ocr_footer_identity(pdf_text: str) -> tuple[str, str] | None:
    """Use only one unambiguous company and VAT pair from an OCR footer."""
    marker = "Supplementary OCR text; verify against the original document:"
    if marker not in pdf_text:
        return None
    ocr_text = pdf_text.split(marker, 1)[1]
    candidates = {
        (name.strip(), re.sub(r"\s+", "", vat).upper())
        for name, vat in OCR_FOOTER_IDENTITY.findall(ocr_text)
    }
    return candidates.pop() if len(candidates) == 1 else None

SYSTEM_PROMPT = (
    "You are a data extraction and classification specialist. Always respond with "
    "valid JSON only, no markdown or code blocks. Extract all information accurately "
    "from German business documents and classify into the appropriate commodity "
    "group based on the full context of the offer."
)


def _build_prompt(pdf_text: str, commodity_groups_list: str) -> str:
    return f"""You are a data extraction and classification specialist. Extract procurement/vendor offer information from the following German document text AND classify it into the appropriate commodity group.

Document Text:
{pdf_text}

Please extract the following information:
1. Title/Short Description (A brief summary of what this procurement is for - e.g., "Office Equipment Purchase", "Software Licenses", etc.)
2. Vendor Name (Lieferant/Anbieter/Firma/Company name)
3. VAT ID (Umsatzsteuer-Identifikationsnummer/VAT ID/USt-IdNr/Tax ID)
4. Department (only an explicitly named department, not the customer's company name)
5. Order Lines/Items (base-offer line items with):
   - Position Description (Bezeichnung/Product/Item/Artikel)
   - Unit Price (Einzelpreis/Unit Price/Preis/Price per unit)
   - Amount (Menge/Quantity/Anzahl)
   - Unit (Einheit/Unit/ME - e.g., "licenses", "pieces", "Stück", "items")
   - Total Price (Gesamtpreis/Total/Gesamt)
6. Total Cost (the document's stated final amount, including tax and shipping when included)
7. Commodity Group Classification - Select the SINGLE most appropriate commodity group from the list below

Available Commodity Groups (ID | Category | Name):
{commodity_groups_list}

IMPORTANT INSTRUCTIONS:
- For the title, create a SHORT (2-5 words) description based on the main items being purchased
- Extract all base-offer line items, even if there are many. Put optional or alternative
  positions (such as Alternativ, Alternative, Alt. or E.P.) in alternativeOrderLines,
  not orderLines. If there are no alternatives, return an empty alternativeOrderLines list.
  An alternative heading may precede the priced item and (Alt.) may
  appear on the line after its price. For example, if a quote shows 'Alternativ: Logo
  vertikal', followed by a priced vertical logo and '(Alt.)', the vertical logo is an
  alternative, even if its own description does not contain the word alternative.
  Exclude section headings and descriptions without a priced position.
- The supplier is the company making the offer, not the recipient/customer. Look for
  sender details, branding and signatures even if the recipient is more prominent.
- Supplementary OCR may reveal supplier details in images, but can contain transcription
  errors. Use the native text for prices when available; never assume OCR is perfectly accurate.
- Copy the printed final offer amount; do not sum alternative positions or invent totals.
- If a field is missing, use null for that field; do not guess supplier details or units.
- For unit prices and totals, extract only the numeric value (remove currency symbols)
- For the unit field, translate German terms to English (e.g., "Stück" -> "pieces", "Lizenzen" -> "licenses")
- Be careful with German number formatting (e.g., "1.438,00" = 1438.00)
- For classification, consider the PRIMARY nature and purpose of the items:
  * Branded office decor/furniture → Office Equipment (15)
  * Traditional promotional items (brochures, giveaways, banners) → Promotional Materials (43)
  * Computers and other IT hardware → Hardware (29); software → Software (31)
  * If genuinely uncertain, set commodityGroupId to null

Return ONLY valid JSON in this exact format (no markdown, no code blocks):
{{
  "title": "Short description of procurement",
  "vendorName": "Company Name",
  "vatId": "DE123456789",
  "department": "Department Name",
  "orderLines": [
    {{
      "positionDescription": "Item description",
      "unitPrice": 150.00,
      "amount": 10,
      "unit": "licenses",
      "totalPrice": 1500.00
    }}
  ],
  "alternativeOrderLines": [
    {{"positionDescription": "Optional alternative description", "unitPrice": 200.00,
      "amount": 1, "unit": "pieces", "totalPrice": 200.00}}
  ],
  "totalCost": 1500.00,
  "commodityGroupId": 15,
  "commodityGroupName": "Office Equipment"
}}"""


def _strip_markdown_fences(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()
    return cleaned


def extract_vendor_data(pdf_text: str, db: Session) -> ExtractionResponse:
    """Extract vendor data and classify commodity group in one AI call.

    Port of ``src/actions/extraction.ts`` — same prompt, model and validation.
    """
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    commodity_groups = list(
        db.scalars(select(CommodityGroup).order_by(CommodityGroup.id))
    )
    if not commodity_groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    # Give the model the full commodity catalog so it has complete context to
    # pick the most accurate match in a single pass.
    commodity_groups_list = "\n".join(
        f"{g.id} | {g.category} | {g.name}" for g in commodity_groups
    )

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_prompt(pdf_text, commodity_groups_list)},
            ],
            max_completion_tokens=8000 if settings.openai_model.startswith("gpt-6") else 4000,
            **({"reasoning_effort": "low"} if settings.openai_model.startswith("gpt-6") else {"temperature": 0.3}),
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if not content:
            return ExtractionResponse(success=False, error="Empty response from AI")

        extracted = ExtractedVendorData.model_validate(
            json.loads(_strip_markdown_fences(content))
        )
    except Exception as exc:  # noqa: BLE001 — mirror the original catch-all
        logger.exception("Error extracting vendor data")
        return ExtractionResponse(success=False, error=str(exc))

    # Prefer an unambiguous explicitly labelled printed final amount to an AI guess.
    # The frontend still asks the requester to verify it against the source document.
    printed_total = _printed_final_total(pdf_text)
    if printed_total is not None:
        extracted.total_cost = float(printed_total)

    _separate_marked_alternatives(pdf_text, extracted)
    extracted.tax_rate_percent = _verified_tax_rate(pdf_text, extracted)
    warnings = []
    footer_identity = _ocr_footer_identity(pdf_text)
    if footer_identity:
        supplier, vat = footer_identity
        if extracted.vat_id and re.sub(r"\s+", "", extracted.vat_id).upper() != vat:
            warnings.append("Supplier VAT details differ from the image text; check the original PDF.")
        else:
            extracted.vendor_name = supplier
            extracted.vat_id = vat
            warnings.append("Supplier details were read from the PDF image; verify them against the original.")

    # Check which fields are missing or empty (same rules as the original).
    missing_fields: list[str] = []
    if not extracted.title.strip():
        missing_fields.append("Title/Short Description")
    if not extracted.vendor_name.strip():
        missing_fields.append("Vendor Name")
    if not extracted.vat_id.strip():
        missing_fields.append("VAT ID")
    if not extracted.department.strip():
        missing_fields.append("Department")
    if not extracted.order_lines:
        missing_fields.append("Order Lines")
    elif any(not line.unit for line in extracted.order_lines):
        missing_fields.append("Order Line Units")
    if not extracted.total_cost:
        missing_fields.append("Total Cost")
    if not extracted.commodity_group_id or not extracted.commodity_group_name:
        missing_fields.append("Commodity Group")

    # Validate that the returned commodity group id actually exists.
    if extracted.commodity_group_id is not None:
        valid_ids = {g.id for g in commodity_groups}
        if extracted.commodity_group_id not in valid_ids:
            logger.warning(
                "Invalid commodity group ID returned: %s", extracted.commodity_group_id
            )
            extracted.commodity_group_id = None
            extracted.commodity_group_name = None
            if "Commodity Group" not in missing_fields:
                missing_fields.append("Commodity Group")

    if re.search(r"\balternativ(?:e|en)?\b|\(alt\.\)", pdf_text, re.IGNORECASE):
        warnings.append(
            "This quote mentions alternatives. Check the order lines against the PDF; "
            "alternatives may not always be detected correctly."
        )

    return ExtractionResponse(
        success=True,
        data=extracted,
        missing_fields=missing_fields or None,
        warnings=warnings,
    )
