from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, get_current_user
from app.db.session import get_db
from app.models import CommodityGroup, OrganizationMember
from app.schemas.requests import CommodityGroupOut, OrderLineIn
from app.services.acme_categories import ACME_CATEGORY_RULES, acme_group, is_acme
from app.services.approval_policy import ACME_AUTO_APPROVAL_LIMIT

router = APIRouter(prefix="/commodity-groups", tags=["commodity-groups"])


class RuleInput(BaseModel):
    title: str = ''
    order_lines: list[OrderLineIn] = Field(default_factory=list, alias='orderLines')


@router.get('/customer-rules')
def customer_rules(membership: OrganizationMember = Depends(get_current_membership)) -> dict:
    applies = is_acme(membership)
    return {'rules': ACME_CATEGORY_RULES if applies else [],
            'approvalPolicy': {'limitEur': str(ACME_AUTO_APPROVAL_LIMIT)} if applies else None}


@router.post('/customer-rule')
def customer_rule(payload: RuleInput, membership: OrganizationMember = Depends(get_current_membership)) -> dict:
    group_id, conflicting = acme_group(payload.title, payload.order_lines) if is_acme(membership) else (None, False)
    return {'groupId': group_id, 'conflicting': conflicting}


@router.get("", response_model=list[CommodityGroupOut], dependencies=[Depends(get_current_user)])
def list_commodity_groups(db: Session = Depends(get_db)) -> list[CommodityGroupOut]:
    groups = db.scalars(select(CommodityGroup).order_by(CommodityGroup.id))
    return [CommodityGroupOut.model_validate(g) for g in groups]
