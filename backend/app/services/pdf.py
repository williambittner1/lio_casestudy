import io
import logging
import shutil
import subprocess
from dataclasses import dataclass, field

from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)
MAX_OCR_PAGES = 6


@dataclass
class PdfTextResult:
    text: str
    warnings: list[str] = field(default_factory=list)


def _needs_ocr(page, text: str) -> bool:
    """Detect scanned pages and page-shaped background images, without OCRing every logo."""
    if len(text.strip()) < 50:
        return True
    page_ratio = float(page.mediabox.width) / float(page.mediabox.height)
    for image in page.images:
        width, height = image.image.size
        if width * height >= 1_000_000 and 0.5 <= (width / height) / page_ratio <= 2:
            return True
    return False


def _ocr_page(pdf_bytes: bytes, page_number: int) -> str:
    # Threshold pale text so light supplier footers are visible to OCR.
    image_bytes = subprocess.run(
        ["pdftoppm", "-f", str(page_number), "-l", str(page_number), "-scale-to", "3000", "-png", "-singlefile"],
        input=pdf_bytes, capture_output=True, check=True, timeout=30,
    ).stdout
    with Image.open(io.BytesIO(image_bytes)) as image:
        gray = image.convert("L").point(lambda value: 255 if value > 245 else 0)
        processed = io.BytesIO()
        gray.save(processed, format="PNG")
    return subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "deu+eng"],
        input=processed.getvalue(), capture_output=True, check=True, timeout=30,
    ).stdout.decode("utf-8", errors="replace")


def extract_pdf_text(pdf_bytes: bytes) -> PdfTextResult:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text_pages = [page.extract_text() or "" for page in reader.pages]
    warnings = []
    candidates = []
    for index, (page, text) in enumerate(zip(reader.pages, text_pages), start=1):
        try:
            if _needs_ocr(page, text):
                candidates.append(index)
        except Exception:
            logger.warning("Could not inspect images on PDF page %s", index, exc_info=True)
            warnings.append(f"Could not inspect images on page {index}; check the original PDF.")

    supplements = []
    if candidates and not (shutil.which("pdftoppm") and shutil.which("tesseract")):
        warnings.append("Some pages may contain image text that could not be read; check the original PDF.")
    else:
        if len(candidates) > MAX_OCR_PAGES:
            warnings.append(f"Only the first {MAX_OCR_PAGES} image-heavy pages were checked; review the original PDF.")
        for page_number in candidates[:MAX_OCR_PAGES]:
            try:
                result = _ocr_page(pdf_bytes, page_number).strip()
                if result:
                    supplements.append(f"OCR page {page_number} (may contain errors):\n{result}")
                else:
                    warnings.append(f"No image text could be read on page {page_number}; check the original PDF.")
            except (OSError, subprocess.SubprocessError, ValueError):
                logger.warning("OCR failed for PDF page %s", page_number, exc_info=True)
                warnings.append(f"Image text on page {page_number} could not be read; check the original PDF.")

    text = "\n".join(text_pages)
    if supplements:
        text += "\n\nSupplementary OCR text; verify against the original document:\n" + "\n\n".join(supplements)
    return PdfTextResult(text=text, warnings=warnings)


def extract_text(pdf_bytes: bytes) -> str:
    """Backward-compatible text-only interface."""
    return extract_pdf_text(pdf_bytes).text
