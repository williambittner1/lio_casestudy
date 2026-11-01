from types import SimpleNamespace

from app.services import pdf


def page(text, width=0, height=0):
    images = [SimpleNamespace(image=SimpleNamespace(size=(width, height)))] if width else []
    return SimpleNamespace(
        extract_text=lambda: text,
        mediabox=SimpleNamespace(width=595, height=842),
        images=images,
    )


def test_scanned_and_page_sized_images_are_selected_for_ocr():
    assert pdf._needs_ocr(page("", 100, 100), "")
    assert pdf._needs_ocr(page("x" * 100, 2480, 3508), "x" * 100)
    assert not pdf._needs_ocr(page("x" * 100, 1600, 684), "x" * 100)


def test_ocr_supplements_native_text_and_is_bounded(monkeypatch):
    monkeypatch.setattr(pdf, "PdfReader", lambda _: SimpleNamespace(pages=[page("base text " * 10, 2480, 3508)] * 8))
    monkeypatch.setattr(pdf.shutil, "which", lambda _: "/available")
    calls = []
    def fake_ocr(_data, number):
        calls.append(number)
        return f"Supplier from image {number}"
    monkeypatch.setattr(pdf, "_ocr_page", fake_ocr)
    result = pdf.extract_pdf_text(b"pdf")
    assert calls == [1, 2, 3, 4, 5, 6]
    assert "base text" in result.text
    assert "Supplier from image 1" in result.text
    assert result.warnings


def test_unavailable_ocr_preserves_readable_text_and_warns(monkeypatch):
    monkeypatch.setattr(pdf, "PdfReader", lambda _: SimpleNamespace(pages=[page("base text " * 10, 2480, 3508)]))
    monkeypatch.setattr(pdf.shutil, "which", lambda _: None)
    result = pdf.extract_pdf_text(b"pdf")
    assert "base text" in result.text
    assert result.warnings and "could not be read" in result.warnings[0]
