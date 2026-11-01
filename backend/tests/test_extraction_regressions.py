"""Regression tests for extraction without calling a paid model."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import CommodityGroup
from app.schemas.extraction import ExtractedVendorData
from app.services import extraction


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    CommodityGroup.__table__.create(engine)
    with Session(engine) as session:
        session.add(CommodityGroup(id=15, category="Facility Management", name="Office Equipment"))
        session.commit()
        yield session
    engine.dispose()


def fake_model(monkeypatch, response_data):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_data)))]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )
    monkeypatch.setattr(extraction.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(extraction, "OpenAI", lambda **kwargs: client)


def test_luna_request_uses_supported_reasoning_parameters(db, monkeypatch):
    calls = []
    data = {"orderLines": [], "totalCost": 0}
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response)))
    monkeypatch.setattr(extraction.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(extraction.settings, "openai_model", "gpt-6-luna")
    monkeypatch.setattr(extraction, "OpenAI", lambda **kwargs: client)
    assert extraction.extract_vendor_data("test quote", db).success
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning_effort"] == "low"
    assert "temperature" not in calls[0]


GREGG_TOTALS = "Nettosumme 1.758,00 €\nUmsatzsteuer 19 % 334,02 €\nGesamtsumme 2.092,02 €"


def gregg_data():
    return ExtractedVendorData.model_validate({
        "orderLines": [
            {"positionDescription": "Picture", "unitPrice": 1438, "amount": 1, "totalPrice": 1438},
            {"positionDescription": "Shipping", "unitPrice": 320, "amount": 1, "totalPrice": 320},
        ],
        "totalCost": 2092.02,
    })


def test_quote_tax_rate_is_returned_when_all_printed_amounts_reconcile(db, monkeypatch):
    data = gregg_data().model_dump(by_alias=True)
    data["taxRatePercent"] = 7  # A model-provided rate must not override verification.
    fake_model(monkeypatch, data)
    result = extraction.extract_vendor_data(GREGG_TOTALS, db)
    assert result.success
    assert result.data.tax_rate_percent == 19


def test_unverified_model_rate_is_discarded(db, monkeypatch):
    data = gregg_data().model_dump(by_alias=True)
    data["taxRatePercent"] = 19
    fake_model(monkeypatch, data)
    result = extraction.extract_vendor_data("The document has no verifiable tax details.", db)
    assert result.success
    assert result.data.tax_rate_percent is None


@pytest.mark.parametrize("text", [
    GREGG_TOTALS + "\nUSt. 7 %",
    GREGG_TOTALS.replace("1.758,00", "1.759,00"),
    GREGG_TOTALS.replace("334,02", "334,03"),
    GREGG_TOTALS.replace("Gesamtsumme 2.092,02 €", ""),
    "Supplementary OCR text; verify against the original document:\n" + GREGG_TOTALS,
])
def test_ambiguous_or_unverified_tax_never_produces_a_rate(text):
    assert extraction._verified_tax_rate(text, gregg_data()) is None


def test_incomplete_order_lines_do_not_produce_a_verified_rate():
    data = gregg_data()
    data.order_lines.pop()
    assert extraction._verified_tax_rate(GREGG_TOTALS, data) is None


def test_hoffmann_net_and_tax_format_is_verified():
    text = ("Zwischensumme netto 17.000,00 EUR\n"
            "zzgl. gesetzl. MwSt. (19%) 3.230,00 EUR\n"
            "Gesamtbetrag brutto 20.230,00 EUR")
    data = ExtractedVendorData.model_validate({
        "orderLines": [{"positionDescription": "Machine", "amount": 1, "totalPrice": 17000}],
        "totalCost": 20230,
    })
    assert extraction._verified_tax_rate(text, data) == 19


def test_null_optional_fields_do_not_discard_other_results(db, monkeypatch):
    fake_model(monkeypatch, {
        "title": "Machine",
        "vendorName": "Supplier",
        "vatId": None,
        "department": None,
        "orderLines": [{"positionDescription": "Machine", "unitPrice": 15000,
                        "amount": 1, "unit": None, "totalPrice": 15000}],
        "alternativeOrderLines": None,
        "totalCost": 20230,
        "commodityGroupId": 15,
        "commodityGroupName": "Office Equipment",
    })

    result = extraction.extract_vendor_data("Supplier quote", db)

    assert result.success
    assert result.data.vendor_name == "Supplier"
    assert result.data.vat_id == ""
    assert result.data.department == ""
    assert result.data.order_lines[0].unit == ""
    assert result.data.alternative_order_lines == []
    assert {"VAT ID", "Department", "Order Line Units"}.issubset(result.missing_fields)


def test_alternative_items_are_returned_separately(db, monkeypatch):
    fake_model(monkeypatch, {
        "title": "Moss picture", "vendorName": "Gregg", "vatId": "DE123",
        "department": "", "orderLines": [
            {"positionDescription": "Base", "unitPrice": 1438, "amount": 1,
             "unit": "piece", "totalPrice": 1438},
            {"positionDescription": "Delivery", "unitPrice": 320, "amount": 1,
             "unit": "service", "totalPrice": 320}],
        "alternativeOrderLines": [
            {"positionDescription": "Alternative", "unitPrice": 1926, "amount": 1,
             "unit": "piece", "totalPrice": 1926}],
        "totalCost": 2092.02, "commodityGroupId": 15, "commodityGroupName": "Office Equipment",
    })
    result = extraction.extract_vendor_data("Offer", db)
    assert result.success
    assert [line.position_description for line in result.data.order_lines] == ["Base", "Delivery"]
    assert result.data.alternative_order_lines[0].position_description == "Alternative"
    assert result.data.total_cost == 2092.02


def test_missed_explicit_alternative_is_separated_when_price_is_unique(db, monkeypatch):
    fake_model(monkeypatch, {"orderLines": [
        {"positionDescription": "Horizontal logo", "unitPrice": 622, "amount": 1,
         "unit": "piece", "totalPrice": 622},
        {"positionDescription": "Vertical logo", "unitPrice": 430, "amount": 1,
         "unit": "piece", "totalPrice": 430}], "totalCost": 1847.19})
    text = '4 Alternativ: Logo vertikal\n5 Vertikales Logo\n1,00 Stk. 430,00 430,00\n(Alt.)'
    result = extraction.extract_vendor_data(text, db)
    assert [line.position_description for line in result.data.order_lines] == ["Horizontal logo"]
    assert [line.position_description for line in result.data.alternative_order_lines] == ["Vertical logo"]
    assert result.warnings


def test_ambiguous_alternative_price_is_left_for_review(db, monkeypatch):
    fake_model(monkeypatch, {"orderLines": [
        {"positionDescription": name, "unitPrice": 430, "amount": 1,
         "unit": "piece", "totalPrice": 430}
        for name in ["Base", "Alternative"]], "totalCost": 1847.19})
    result = extraction.extract_vendor_data("Alternativ: logo\n430,00\n(Alt.)", db)
    assert len(result.data.order_lines) == 2
    assert not result.data.alternative_order_lines
    assert result.warnings


@pytest.mark.parametrize("label", ["Endsumme", "Gesamtsumme (EUR)", "Gesamtbetrag brutto"])
def test_explicit_final_total_overrides_incorrect_model_amount(db, monkeypatch, label):
    fake_model(monkeypatch, {"totalCost": 1867.26})
    result = extraction.extract_vendor_data(f"{label}   1.847,19 €", db)
    assert result.success
    assert result.data.total_cost == 1847.19


def test_ambiguous_printed_totals_do_not_override_model(db, monkeypatch):
    fake_model(monkeypatch, {"totalCost": 123.45})
    result = extraction.extract_vendor_data("Endsumme 1.847,19 €\nEndsumme 2.092,02 €", db)
    assert result.data.total_cost == 123.45


def test_total_on_next_line_is_not_guessed():
    # Some PDFs list all labels first and then all the amounts; the next line can
    # be a subtotal rather than the amount corresponding to Gesamtsumme.
    assert extraction._printed_final_total("Gesamtsumme (EUR)\n€1.467,61\n€1.759,01") is None


def test_unique_ocr_footer_identity_corrects_misidentified_customer(db, monkeypatch):
    fake_model(monkeypatch, {"vendorName": "Customer GmbH", "vatId": None})
    text = ("Document text\n\nSupplementary OCR text; verify against the original document:\n"
            "FlowerArt GmbH | Address | Phone\nGeschäftsführer: Name | USt-IdNr.: DE271073640\n")
    result = extraction.extract_vendor_data(text, db)
    assert result.data.vendor_name == "FlowerArt GmbH"
    assert result.data.vat_id == "DE271073640"
    assert "verify" in result.warnings[0].lower()


def test_conflicting_ocr_footer_vat_is_not_silently_overwritten(db, monkeypatch):
    fake_model(monkeypatch, {"vendorName": "Another supplier", "vatId": "DE111111111"})
    text = ("Supplementary OCR text; verify against the original document:\n"
            "FlowerArt GmbH | Address\nContact | USt-IdNr.: DE271073640\n")
    result = extraction.extract_vendor_data(text, db)
    assert result.data.vat_id == "DE111111111"
    assert "differ" in result.warnings[0]


def test_multiple_ocr_footer_identities_are_not_guessed():
    text = ("Supplementary OCR text; verify against the original document:\n"
            "First GmbH | Address\nContact | USt-IdNr.: DE111111111\n"
            "Second GmbH | Address\nContact | USt-IdNr.: DE222222222\n")
    assert extraction._ocr_footer_identity(text) is None
