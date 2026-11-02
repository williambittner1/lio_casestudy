from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.core.deps import get_current_user
from app.db.session import get_db
from app.schemas.extraction import ExtractionRequest, ExtractionResponse
from app.services.extraction import extract_vendor_data
from app.services.acme_categories import acme_group, is_acme
from app.models import CommodityGroup, OrganizationMember, User

router = APIRouter(prefix="/extraction", tags=["extraction"])


@router.post("", response_model=ExtractionResponse, response_model_by_alias=True)
def extract(payload: ExtractionRequest, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> ExtractionResponse:
    """Extract structured vendor data from PDF text via OpenAI.

    Returns ``{success, data?, error?, missingFields?}`` — the same envelope the
    original server action produced (errors are part of the payload, not HTTP
    errors, so the UI can show partial-extraction warnings).
    """
    result = extract_vendor_data(payload.text, db)
    membership = db.scalar(select(OrganizationMember).where(OrganizationMember.user_id == user.id))
    if result.success and result.data and membership and is_acme(membership):
        group_id, conflicting = acme_group(result.data.title, result.data.order_lines)
        if conflicting:
            result.warnings.append("Acme rules require different categories for these items. Create separate requests.")
        elif group_id is not None:
            group = db.get(CommodityGroup, group_id)
            if group:
                result.data.commodity_group_id = group.id
                result.data.commodity_group_name = group.name
                result.missing_fields = [field for field in (result.missing_fields or []) if field != 'Commodity Group'] or None
                result.warnings.append(f"Acme categorization rule applied: {group.name}.")
    return result
