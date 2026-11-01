import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import get_current_membership, require_buyer_or_admin
from app.db.session import get_db
from app.models import (
    CommodityGroup,
    OrderLine,
    Organization,
    OrganizationMember,
    ProcurementRequest,
    RequestActivity,
    RequestDocument,
)
from app.schemas.requests import (
    ApprovalDecision,
    RequestActivityOut,
    RequestCreate,
    RequestOut,
    RequestUpdate,
    StatusUpdate,
)
from app.services.acme_categories import acme_group, is_acme
from app.services.approval_policy import ACME_AUTO_APPROVAL_LIMIT, auto_approval_applies, money

router = APIRouter(prefix="/requests", tags=["requests"])

MAX_PDF_BYTES = 10 * 1024 * 1024

# Human-readable labels for the fields we track in the activity log.
_FIELD_LABELS = {
    "requestor_name": "requestor",
    "title": "title",
    "vendor_name": "vendor",
    "vat_id": "VAT ID",
    "commodity_group_id": "commodity group",
    "total_cost": "total cost",
    "department": "department",
    "order_lines": "order lines",
}


def _load_request_options():
    return (
        selectinload(ProcurementRequest.order_lines),
        selectinload(ProcurementRequest.commodity_group),
    )


def _request_visibility(membership: OrganizationMember):
    filters = [ProcurementRequest.organization_id == membership.organization_id]
    if membership.role == "requester":
        # Old records can predate created_by; user_id is their original owner.
        filters.append(or_(
            ProcurementRequest.created_by == membership.user_id,
            and_(ProcurementRequest.created_by.is_(None), ProcurementRequest.user_id == membership.user_id),
        ))
    return filters


def _get_org_request(
    db: Session, request_id: uuid.UUID, membership: OrganizationMember, *, for_update: bool = False,
) -> ProcurementRequest:
    stmt = (
        select(ProcurementRequest)
        .options(*_load_request_options())
        # Refresh already-loaded relationships (e.g. after an update commit).
        .execution_options(populate_existing=True)
        .where(
            ProcurementRequest.id == request_id,
            *_request_visibility(membership),
        )
    )
    # Serialize decisions and content edits for the same request in PostgreSQL.
    if for_update:
        stmt = stmt.with_for_update()
    request = db.scalar(stmt)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found or you do not have permission to access it",
        )
    return request


def _validate_commodity_group(db: Session, commodity_group_id: int) -> None:
    if db.get(CommodityGroup, commodity_group_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown commodity group id: {commodity_group_id}",
        )


def _apply_acme_category(payload: RequestCreate | RequestUpdate, membership: OrganizationMember) -> None:
    if not is_acme(membership):
        return
    group_id, conflicting = acme_group(payload.title, payload.order_lines)
    if conflicting:
        raise HTTPException(status_code=422, detail="Acme rules require different categories for these items. Create separate requests.")
    if group_id is not None:
        payload.commodity_group_id = group_id


def _enforce_required_fields(
    db: Session, organization_id: uuid.UUID, payload: RequestCreate | RequestUpdate
) -> None:
    """Enforce the org's configurable required fields (see OrganizationSettings)."""
    org = db.get(Organization, organization_id)
    required = (org.settings or {}).get("required_fields", []) if org else []
    missing = [field for field in required if not getattr(payload, field, None)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing fields required by your organization: {', '.join(missing)}",
        )


def _log_activity(
    db: Session,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    summary: str,
    detail: dict | None = None,
) -> None:
    db.add(
        RequestActivity(
            request_id=request_id,
            actor_id=actor_id,
            action=action,
            summary=summary,
            detail=detail,
        )
    )


def _recheck_approval(db: Session, request: ProcurementRequest, membership: OrganizationMember, reason: str) -> None:
    previous = request.approval_status or "pending"
    auto = auto_approval_applies(membership, request.total_cost)
    request.approved_by = None
    if auto:
        request.approval_status = "approved"
        request.approval_source = "automatic"
        request.approved_at = datetime.now(timezone.utc)
        _log_activity(db, request.id, membership.user_id, "auto_approved",
                      f"Automatically approved under Acme's €{ACME_AUTO_APPROVAL_LIMIT:,.0f} limit",
                      {"reason": reason, "previous_status": previous, "limit_eur": str(ACME_AUTO_APPROVAL_LIMIT)})
    else:
        request.approval_status = "pending"
        request.approval_source = None
        request.approved_at = None
        if previous != "pending":
            _log_activity(db, request.id, membership.user_id, "approval_reset",
                          "Approval required again after a change",
                          {"reason": reason, "previous_status": previous})


def _order_lines_signature(lines) -> list[tuple]:
    return [
        (
            line.position_description,
            float(line.unit_price),
            float(line.amount),
            line.unit,
            float(line.total_price),
        )
        for line in lines
    ]


def _changed_fields(request: ProcurementRequest, payload: RequestUpdate) -> list[str]:
    """Return the field keys whose value differs between the request and payload."""
    changed: list[str] = []
    if request.requestor_name != payload.requestor_name:
        changed.append("requestor_name")
    if request.title != payload.title:
        changed.append("title")
    if request.vendor_name != payload.vendor_name:
        changed.append("vendor_name")
    if (request.vat_id or "") != (payload.vat_id or ""):
        changed.append("vat_id")
    if request.commodity_group_id != payload.commodity_group_id:
        changed.append("commodity_group_id")
    if request.total_cost is None or money(request.total_cost) != money(payload.total_cost):
        changed.append("total_cost")
    if (request.department or "") != (payload.department or ""):
        changed.append("department")
    old_lines = _order_lines_signature(
        sorted(request.order_lines, key=lambda line: line.line_order)
    )
    new_lines = _order_lines_signature(payload.order_lines)
    if old_lines != new_lines:
        changed.append("order_lines")
    return changed


@router.get("", response_model=list[RequestOut])
def list_requests(
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> list[RequestOut]:
    requests = db.scalars(
        select(ProcurementRequest)
        .options(*_load_request_options())
        .where(*_request_visibility(membership))
        .order_by(ProcurementRequest.created_at.desc())
    )
    return [RequestOut.model_validate(r) for r in requests]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    return RequestOut.model_validate(
        _get_org_request(db, request_id, membership)
    )


@router.post("", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    _apply_acme_category(payload, membership)
    _validate_commodity_group(db, payload.commodity_group_id)
    _enforce_required_fields(db, membership.organization_id, payload)
    payload.total_cost = float(money(payload.total_cost))

    # Vendor fields are already cleaned up during extraction, so we can persist
    # them directly here.
    request = ProcurementRequest(
        user_id=membership.user_id,
        requestor_name=payload.requestor_name,
        title=payload.title,
        vendor_name=payload.vendor_name,
        vat_id=payload.vat_id,
        commodity_group_id=payload.commodity_group_id,
        total_cost=payload.total_cost,
        department=payload.department,
        organization_id=membership.organization_id,
        created_by=membership.user_id,
        status="open",
        approval_status="pending",
    )
    request.order_lines = [
        OrderLine(
            position_description=line.position_description,
            unit_price=line.unit_price,
            amount=line.amount,
            unit=line.unit,
            total_price=line.total_price,
            line_order=index + 1,
        )
        for index, line in enumerate(payload.order_lines)
    ]
    db.add(request)
    db.flush()
    _log_activity(db, request.id, membership.user_id, "created", "Request created")
    _recheck_approval(db, request, membership, "created")
    db.commit()

    request = _get_org_request(db, request.id, membership)
    return RequestOut.model_validate(request)


@router.put("/{request_id}", response_model=RequestOut)
def update_request(
    request_id: uuid.UUID,
    payload: RequestUpdate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    request = _get_org_request(db, request_id, membership, for_update=True)
    _apply_acme_category(payload, membership)
    _validate_commodity_group(db, payload.commodity_group_id)
    _enforce_required_fields(db, membership.organization_id, payload)
    payload.total_cost = float(money(payload.total_cost))

    changed = _changed_fields(request, payload)

    request.requestor_name = payload.requestor_name
    request.title = payload.title
    request.vendor_name = payload.vendor_name
    request.vat_id = payload.vat_id
    request.commodity_group_id = payload.commodity_group_id
    request.total_cost = payload.total_cost
    request.department = payload.department

    if "order_lines" in changed:
        request.order_lines = [
            OrderLine(
                position_description=line.position_description,
                unit_price=line.unit_price,
                amount=line.amount,
                unit=line.unit,
                total_price=line.total_price,
                line_order=index + 1,
            )
            for index, line in enumerate(payload.order_lines)
        ]

    if changed:
        labels = [_FIELD_LABELS.get(f, f) for f in changed]
        _log_activity(
            db,
            request.id,
            membership.user_id,
            "updated",
            f"Updated {', '.join(labels)}",
            {"changed_fields": changed},
        )
        _recheck_approval(db, request, membership, "request_edited")
    db.commit()

    request = _get_org_request(db, request_id, membership)
    return RequestOut.model_validate(request)


@router.patch("/{request_id}/status", response_model=RequestOut)
def update_status(
    request_id: uuid.UUID,
    payload: StatusUpdate,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> RequestOut:
    request = _get_org_request(db, request_id, membership, for_update=True)

    if request.status != payload.status:
        _log_activity(
            db,
            request.id,
            membership.user_id,
            "status_changed",
            f"Status changed from {request.status} to {payload.status}",
            {"from": request.status, "to": payload.status},
        )
        request.status = payload.status
        db.commit()

    request = _get_org_request(db, request_id, membership)
    return RequestOut.model_validate(request)


@router.patch("/{request_id}/approval", response_model=RequestOut)
def decide_approval(
    request_id: uuid.UUID,
    payload: ApprovalDecision,
    membership: OrganizationMember = Depends(require_buyer_or_admin),
    db: Session = Depends(get_db),
) -> RequestOut:
    """Approve or reject a pending request (buyers and admins only)."""
    request = _get_org_request(db, request_id, membership, for_update=True)

    if payload.decision == "rejected" and (request.created_by or request.user_id) == membership.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot reject your own request.",
        )

    if request.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request has already been {request.approval_status}.",
        )

    request.approval_status = payload.decision
    request.approval_source = "manual"
    request.approved_by = membership.user_id
    request.approved_at = datetime.now(timezone.utc)
    _log_activity(
        db,
        request.id,
        membership.user_id,
        payload.decision,
        f"Request {payload.decision}",
    )
    db.commit()

    request = _get_org_request(db, request_id, membership)
    return RequestOut.model_validate(request)


@router.get("/{request_id}/activity", response_model=list[RequestActivityOut])
def get_activity(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> list[RequestActivityOut]:
    _get_org_request(db, request_id, membership)  # scope check
    activity = db.scalars(
        select(RequestActivity)
        .where(RequestActivity.request_id == request_id)
        .order_by(RequestActivity.created_at.desc())
    )
    return [RequestActivityOut.model_validate(a) for a in activity]


@router.put("/{request_id}/document", status_code=status.HTTP_204_NO_CONTENT)
async def upload_document(
    request_id: uuid.UUID,
    file: UploadFile,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> None:
    """Attach (or replace) the original PDF for a request."""
    request = _get_org_request(db, request_id, membership, for_update=True)

    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Please upload a PDF file"
        )
    content = await file.read()
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF exceeds the 10MB limit",
        )

    document = db.scalar(
        select(RequestDocument).where(RequestDocument.request_id == request.id)
    )
    if document is not None and document.filename == (file.filename or "document.pdf") and document.content == content:
        return
    if document is None:
        document = RequestDocument(request_id=request.id)
        db.add(document)
    document.filename = file.filename or "document.pdf"
    document.content_type = "application/pdf"
    document.content = content

    _log_activity(
        db,
        request.id,
        membership.user_id,
        "document_attached",
        f"Attached document {document.filename}",
    )
    _recheck_approval(db, request, membership, "document_changed")
    db.commit()


@router.get("/{request_id}/document")
def get_document(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> Response:
    _get_org_request(db, request_id, membership)  # scope check
    document = db.scalar(
        select(RequestDocument).where(RequestDocument.request_id == request_id)
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No document attached"
        )
    return Response(
        content=document.content,
        media_type=document.content_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> None:
    request = _get_org_request(db, request_id, membership, for_update=True)
    db.delete(request)  # order lines, activity, document cascade
    db.commit()
