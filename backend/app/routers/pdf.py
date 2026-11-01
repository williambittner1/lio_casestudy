import base64
import json
import subprocess
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from pypdf.errors import PdfReadError

from app.core.deps import get_current_user
from app.schemas.extraction import ExtractedVendorData, PdfParseResponse
from app.services.pdf import extract_pdf_text
from app.services.pdf_preview import make_highlighted_preview

router = APIRouter(prefix="/pdf", tags=["pdf"])

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB, matches the upload UI's stated limit


@router.post("/parse", response_model=PdfParseResponse, dependencies=[Depends(get_current_user)])
async def parse_pdf(file: UploadFile) -> PdfParseResponse:
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Please upload a PDF file"
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF exceeds the 10MB limit",
        )

    try:
        result = extract_pdf_text(pdf_bytes)
    except Exception:  # noqa: BLE001 — mirror the original catch-all
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse PDF",
        )

    return PdfParseResponse(text=result.text, warnings=result.warnings)


@router.post("/highlight-preview", dependencies=[Depends(get_current_user)])
async def highlight_preview(file: UploadFile, extracted: str = Form(...)) -> dict:
    """Return a temporary annotated copy; never modify the uploaded document."""
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 10MB limit")
    if len(extracted) > 100_000:
        raise HTTPException(status_code=400, detail="Too many extracted fields")
    try:
        data = ExtractedVendorData.model_validate(json.loads(extracted)).model_dump(by_alias=True)
        preview, matched, unmatched, regions = make_highlighted_preview(pdf_bytes, data)
    except (json.JSONDecodeError, ValidationError, ValueError, PdfReadError, ParseError, subprocess.SubprocessError, OSError):
        raise HTTPException(status_code=422, detail="Could not create highlighted preview")
    return {"pdfBase64": base64.b64encode(preview).decode("ascii"), "matchedFields": matched,
            "unmatchedFields": unmatched, "regions": regions}
