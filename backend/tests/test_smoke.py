"""End-to-end smoke test exercising every endpoint over an in-memory SQLite DB.

Run with: python -m pytest tests/ -q   (or plainly: python tests/test_smoke.py)
"""
import io
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Article, CommodityGroup, Organization, RequestActivity, Supplier
from app.seed import seed

# ---------------------------------------------------------------------------
# Test database (in-memory SQLite) + commodity group seed
# ---------------------------------------------------------------------------
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# SQLite ignores foreign keys (and their ON DELETE CASCADE) unless enabled —
# Postgres enforces them natively.
from sqlalchemy import event  # noqa: E402


@event.listens_for(engine, "connect")
def _enable_sqlite_fks(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

Base.metadata.create_all(engine)

# Seed reference data through the same code path production uses, so the test DB
# mirrors it (large commodity taxonomy + 120 suppliers with empty descriptions).
with TestingSession() as _s:
    seed(_s)


def _override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_user_journey():
    # --- health ---
    assert client.get("/health").json() == {"status": "ok"}

    # --- unauthenticated access is rejected ---
    assert client.get("/requests").status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert client.get("/commodity-groups").status_code == 401

    # --- signup ---
    r = client.post(
        "/auth/signup",
        json={"email": "Owner@Example.com", "password": "secret123", "full_name": "Olive Owner"},
    )
    assert r.status_code == 201, r.text
    owner_token = r.json()["access_token"]
    assert r.json()["user"]["email"] == "owner@example.com"  # lower-cased

    # duplicate signup -> 409
    r = client.post(
        "/auth/signup", json={"email": "owner@example.com", "password": "secret123"}
    )
    assert r.status_code == 409

    # short password -> 422
    r = client.post("/auth/signup", json={"email": "x@example.com", "password": "abc"})
    assert r.status_code == 422

    # --- signin ---
    assert (
        client.post(
            "/auth/signin", json={"email": "owner@example.com", "password": "wrong"}
        ).status_code
        == 401
    )
    r = client.post(
        "/auth/signin", json={"email": "owner@example.com", "password": "secret123"}
    )
    assert r.status_code == 200
    owner_token = r.json()["access_token"]

    # --- me ---
    r = client.get("/auth/me", headers=_auth(owner_token))
    assert r.status_code == 200
    assert r.json()["profile"]["full_name"] == "Olive Owner"

    # --- no organization yet: org-scoped endpoints -> 403 ---
    assert client.get("/requests", headers=_auth(owner_token)).status_code == 403
    assert client.get("/organizations/me", headers=_auth(owner_token)).status_code == 403

    # --- create organization ---
    r = client.post(
        "/organizations",
        json={"name": "Acme Corp", "slug": "acme-corp"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "admin"

    # duplicate slug -> 409
    r = client.post(
        "/organizations",
        json={"name": "Acme 2", "slug": "acme-corp"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 409

    # --- organizations/me ---
    r = client.get("/organizations/me", headers=_auth(owner_token))
    assert r.status_code == 200
    assert r.json()["name"] == "Acme Corp"
    assert r.json()["role"] == "admin"

    # --- commodity groups ---
    r = client.get("/commodity-groups", headers=_auth(owner_token))
    assert r.status_code == 200
    group_ids = [g["id"] for g in r.json()]
    assert 15 in group_ids and 31 in group_ids and 43 in group_ids
    assert len(group_ids) >= 300

    # --- create request (camelCase payload, as the frontend sends) ---
    payload = {
        "requestorName": "Olive Owner",
        "titleShortDescription": "Software Licenses",
        "vendorName": "Adobe Systems",
        "vatId": "DE123456789",
        "commodityGroupId": 31,
        "totalCost": 1500.0,
        "department": "IT",
        "orderLines": [
            {
                "positionDescription": "Creative Cloud",
                "unitPrice": 150.0,
                "amount": 10,
                "unit": "licenses",
                "totalPrice": 1500.0,
            }
        ],
    }
    r = client.post("/requests", json=payload, headers=_auth(owner_token))
    assert r.status_code == 201, r.text
    request_id = r.json()["id"]
    assert r.json()["status"] == "open"
    assert r.json()["requestor_name"] == "Olive Owner"
    assert r.json()["commodity_groups"]["name"] == "Software"
    assert len(r.json()["order_lines"]) == 1
    assert r.json()["order_lines"][0]["position_description"] == "Creative Cloud"

    # unknown commodity group -> 422
    bad = dict(payload, commodityGroupId=999999)
    assert client.post("/requests", json=bad, headers=_auth(owner_token)).status_code == 422

    # --- list requests ---
    r = client.get("/requests", headers=_auth(owner_token))
    assert r.status_code == 200
    assert len(r.json()) == 1

    # --- status update logs activity ---
    r = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "in_progress"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"

    def _activity_actions() -> list[str]:
        with TestingSession() as s:
            rows = s.scalars(
                select(RequestActivity)
                .where(RequestActivity.request_id == uuid.UUID(request_id))
                .order_by(RequestActivity.created_at)
            ).all()
            return [a.action for a in rows]

    # created + status_changed so far
    assert _activity_actions() == ["created", "status_changed"]

    # same-status update does NOT add activity
    client.patch(
        f"/requests/{request_id}/status",
        json={"status": "in_progress"},
        headers=_auth(owner_token),
    )
    assert _activity_actions() == ["created", "status_changed"]

    # invalid status -> 422
    r = client.patch(
        f"/requests/{request_id}/status",
        json={"status": "bogus"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 422

    # --- full update replaces order lines ---
    update = {
        "requestorName": "Olive Owner",
        "title": "Software Licenses (updated)",
        "vendorName": "Adobe Systems",
        "vatId": "DE123456789",
        "commodityGroupId": 15,
        "totalCost": 500.0,
        "department": "IT",
        "orderLines": [
            {
                "positionDescription": "Desk",
                "unitPrice": 250.0,
                "amount": 2,
                "unit": "pieces",
                "totalPrice": 500.0,
            },
            {
                "positionDescription": "Chair",
                "unitPrice": 0.0,
                "amount": 1.28,
                "unit": "meters",
                "totalPrice": 0.0,
            },
        ],
    }
    r = client.put(f"/requests/{request_id}", json=update, headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Software Licenses (updated)"
    assert body["commodity_groups"]["id"] == 15
    assert [l["position_description"] for l in body["order_lines"]] == ["Desk", "Chair"]
    assert body["order_lines"][1]["amount"] == 1.28  # decimal amounts supported
    assert [l["line_order"] for l in body["order_lines"]] == [1, 2]

    # --- invites ---
    r = client.post(
        "/organizations/me/invites",
        json={"email": "member@example.com"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 201, r.text
    invite_token = None
    # token isn't exposed via the API response; fetch from DB like the email link would carry it
    from app.models import OrganizationInvite

    with TestingSession() as s:
        invite = s.scalar(
            select(OrganizationInvite).where(OrganizationInvite.email == "member@example.com")
        )
        assert invite is not None and invite.token and invite.expires_at is not None
        invite_token = invite.token

    # duplicate invite -> 409
    r = client.post(
        "/organizations/me/invites",
        json={"email": "member@example.com"},
        headers=_auth(owner_token),
    )
    assert r.status_code == 409

    # members endpoint shows 1 member + 1 pending invite
    r = client.get("/organizations/me/members", headers=_auth(owner_token))
    assert r.status_code == 200
    assert len(r.json()["members"]) == 1
    assert r.json()["members"][0]["profiles"]["full_name"] == "Olive Owner"
    assert len(r.json()["invites"]) == 1

    # --- second user accepts invite ---
    r = client.post(
        "/auth/signup",
        json={"email": "member@example.com", "password": "secret123", "full_name": "Mia Member"},
    )
    member_token = r.json()["access_token"]

    # wrong-email user cannot accept
    r = client.post(
        "/auth/signup", json={"email": "other@example.com", "password": "secret123"}
    )
    other_token = r.json()["access_token"]
    r = client.post(
        "/organizations/invites/accept",
        json={"token": invite_token},
        headers=_auth(other_token),
    )
    assert r.status_code == 403

    r = client.post(
        "/organizations/invites/accept",
        json={"token": invite_token},
        headers=_auth(member_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "requester"

    # accepted invite cannot be reused
    r = client.post(
        "/organizations/invites/accept",
        json={"token": invite_token},
        headers=_auth(member_token),
    )
    assert r.status_code == 404

    # A requester cannot see the owner's request.
    r = client.get("/requests", headers=_auth(member_token))
    assert r.status_code == 200
    assert r.json() == []

    # members endpoint now shows 2 members, 0 pending invites
    r = client.get("/organizations/me/members", headers=_auth(owner_token))
    assert len(r.json()["members"]) == 2
    assert len(r.json()["invites"]) == 0

    # plain member cannot invite (admin required)
    r = client.post(
        "/organizations/me/invites",
        json={"email": "third@example.com"},
        headers=_auth(member_token),
    )
    assert r.status_code == 403

    # --- cancel invite ---
    r = client.post(
        "/organizations/me/invites",
        json={"email": "third@example.com"},
        headers=_auth(owner_token),
    )
    invite_id = r.json()["id"]
    r = client.delete(
        f"/organizations/me/invites/{invite_id}", headers=_auth(owner_token)
    )
    assert r.status_code == 204

    # --- isolation: user in a different org sees nothing ---
    r = client.post(
        "/organizations",
        json={"name": "Other Org", "slug": "other-org"},
        headers=_auth(other_token),
    )
    assert r.status_code == 201
    r = client.get("/requests", headers=_auth(other_token))
    assert r.json() == []
    # ...and cannot touch the first org's request
    assert (
        client.delete(f"/requests/{request_id}", headers=_auth(other_token)).status_code
        == 404
    )

    # --- delete request ---
    r = client.delete(f"/requests/{request_id}", headers=_auth(owner_token))
    assert r.status_code == 204
    assert client.get("/requests", headers=_auth(owner_token)).json() == []
    # order lines + activity cascaded
    with TestingSession() as s:
        assert (
            s.scalars(
                select(RequestActivity).where(
                    RequestActivity.request_id == uuid.UUID(request_id)
                )
            ).all()
            == []
        )


def test_pdf_parse():
    # Build a tiny real PDF in memory, then round-trip it through the endpoint.
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)

    r = client.post("/auth/signup", json={"email": "pdf@example.com", "password": "secret123"})
    token = r.json()["access_token"]

    r = client.post(
        "/pdf/parse",
        files={"file": ("offer.pdf", buf.getvalue(), "application/pdf")},
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    assert "text" in r.json()  # blank page -> empty text, but parses fine

    # non-PDF rejected
    r = client.post(
        "/pdf/parse",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 400


def test_activity_log_and_document():
    from pypdf import PdfWriter

    admin = client.post(
        "/auth/signin", json={"email": "admin@acme.com", "password": "demo1234"}
    ).json()["access_token"]

    payload = {
        "requestorName": "Alex Admin", "titleShortDescription": "Chairs",
        "vendorName": "FurnitureCo", "vatId": "DE1", "commodityGroupId": 15,
        "totalCost": 5000.0, "department": "Ops",
        "orderLines": [
            {"positionDescription": "Chair", "unitPrice": 2500, "amount": 2, "unit": "pc", "totalPrice": 5000}
        ],
    }
    rid = client.post("/requests", json=payload, headers=_auth(admin)).json()["id"]

    # edit two fields -> one 'updated' activity naming those fields
    updated = dict(payload)
    updated["title"] = "Office Chairs"
    updated["totalCost"] = 6000.0
    updated["orderLines"] = [
        {"positionDescription": "Chair", "unitPrice": 3000, "amount": 2, "unit": "pc", "totalPrice": 6000}
    ]
    # RequestUpdate uses "title" (not titleShortDescription)
    updated = {
        "requestorName": "Alex Admin", "title": "Office Chairs", "vendorName": "FurnitureCo",
        "vatId": "DE1", "commodityGroupId": 15, "totalCost": 6000.0, "department": "Ops",
        "orderLines": updated["orderLines"],
    }
    r = client.put(f"/requests/{rid}", json=updated, headers=_auth(admin))
    assert r.status_code == 200, r.text

    # A different buyer approves, then an admin changes the status.
    buyer = client.post(
        "/auth/signin", json={"email": "buyer@acme.com", "password": "demo1234"}
    ).json()["access_token"]
    assert client.patch(f"/requests/{rid}/approval", json={"decision": "approved"}, headers=_auth(buyer)).status_code == 200
    client.patch(f"/requests/{rid}/status", json={"status": "closed"}, headers=_auth(admin))

    # attach a PDF
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    r = client.put(
        f"/requests/{rid}/document",
        files={"file": ("offer.pdf", buf.getvalue(), "application/pdf")},
        headers=_auth(admin),
    )
    assert r.status_code == 204, r.text
    # Adding the document is an edit and invalidates the prior decision.
    assert client.get(f"/requests/{rid}", headers=_auth(admin)).json()["approval_status"] == "pending"
    assert client.patch(f"/requests/{rid}/approval", json={"decision": "approved"}, headers=_auth(buyer)).status_code == 200

    # activity feed (newest first) captures every event
    r = client.get(f"/requests/{rid}/activity", headers=_auth(admin))
    assert r.status_code == 200
    actions = [a["action"] for a in r.json()]
    assert set(actions) == {
        "created", "updated", "approved", "status_changed", "document_attached", "approval_reset"
    }
    updated_entry = next(a for a in r.json() if a["action"] == "updated")
    assert set(updated_entry["detail"]["changed_fields"]) == {
        "title", "total_cost", "order_lines"
    }

    # the stored PDF is served back
    r = client.get(f"/requests/{rid}/document", headers=_auth(admin))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"

    client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_extraction_without_api_key():
    r = client.post("/auth/signup", json={"email": "ex@example.com", "password": "secret123"})
    token = r.json()["access_token"]

    r = client.post("/extraction", json={"text": "Angebot Nr. 123"}, headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    # Without OPENAI_API_KEY configured the endpoint reports a clean failure
    # envelope rather than an HTTP error.
    assert body["success"] is False
    assert "OPENAI_API_KEY" in body["error"]

    # empty text
    r = client.post("/extraction", json={"text": "  "}, headers=_auth(token))
    assert r.json()["success"] is False


def test_commodity_groups_expanded():
    r = client.post("/auth/signup", json={"email": "cg@example.com", "password": "secret123"})
    token = r.json()["access_token"]

    r = client.get("/commodity-groups", headers=_auth(token))
    assert r.status_code == 200
    groups = r.json()
    # Large taxonomy.
    assert len(groups) >= 1000, f"expected a large taxonomy, got {len(groups)}"
    # ids contiguous from 1; first one matches the original taxonomy
    assert groups[0] == {"id": 1, "category": "General Services", "name": "Accommodation Rentals"}
    assert len({g["category"] for g in groups}) >= 20

    # Every leaf name belongs to exactly one category.
    by_name: dict[str, set[str]] = {}
    for g in groups:
        by_name.setdefault(g["name"], set()).add(g["category"])
    ambiguous = {n: c for n, c in by_name.items() if len(c) > 1}
    assert not ambiguous, f"names spanning multiple categories: {ambiguous}"


def _signin(email: str, password: str = "demo1234") -> str:
    r = client.post("/auth/signin", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _policy_request(total: float) -> dict:
    return {
        "requestorName": "Requester", "titleShortDescription": "Desk",
        "vendorName": "Vendor", "vatId": "", "commodityGroupId": 15,
        "totalCost": total, "department": "Operations",
        "orderLines": [{"positionDescription": "Desk", "unitPrice": total,
                        "amount": 1, "unit": "piece", "totalPrice": total}],
    }


def _policy_update(payload: dict) -> dict:
    return {**payload, "title": payload["titleShortDescription"]}


def test_acme_auto_approval_is_scoped_and_rounds_at_the_boundary():
    requester = _signin("requester@acme.com")
    admin = _signin("admin@acme.com")
    walmart = _signin("admin@walmart.com")
    ids = []
    try:
        for amount, expected_status, expected_total in [
            (0, "approved", 0), (1999.99, "approved", 1999.99),
            (2000, "pending", 2000), (2000.01, "pending", 2000.01),
            (1999.999, "pending", 2000), (1999.994, "approved", 1999.99),
        ]:
            response = client.post("/requests", json=_policy_request(amount), headers=_auth(requester))
            assert response.status_code == 201, response.text
            request = response.json()
            ids.append(request["id"])
            assert request["total_cost"] == expected_total
            assert request["approval_status"] == expected_status
            if expected_status == "approved":
                assert request["approval_source"] == "automatic"
                assert request["approved_by"] is None and request["approved_at"]
                activity = client.get(f"/requests/{request['id']}/activity", headers=_auth(requester)).json()
                assert any(a["action"] == "auto_approved" for a in activity)
            else:
                assert request["approval_source"] is None and request["approved_at"] is None
        assert client.post("/requests", json=_policy_request(-1), headers=_auth(requester)).status_code == 422

        response = client.post("/requests", json=_policy_request(100), headers=_auth(walmart))
        assert response.status_code == 201, response.text
        walmart_request = response.json()
        assert walmart_request["approval_status"] == "pending"
        assert walmart_request["approval_source"] is None
        client.delete(f"/requests/{walmart_request['id']}", headers=_auth(walmart))
    finally:
        for rid in ids:
            client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_all_request_content_edits_recheck_approval():
    from copy import deepcopy
    requester = _signin("requester@acme.com")
    buyer = _signin("buyer@acme.com")
    admin = _signin("admin@acme.com")
    payload = _policy_request(2500)
    created = client.post("/requests", json=payload, headers=_auth(requester)).json()
    rid = created["id"]
    updated = _policy_update(payload)
    try:
        changes = [
            ("requestorName", "Edited requester"), ("title", "Edited title"),
            ("vendorName", "Edited vendor"), ("vatId", "DE123456789"),
            ("commodityGroupId", 31), ("department", "IT"),
            ("totalCost", 2600),
            ("orderLines", [{"positionDescription": "Desk with drawers", "unitPrice": 2600,
                              "amount": 1, "unit": "piece", "totalPrice": 2600}]),
        ]
        for field, value in changes:
            approved = client.patch(f"/requests/{rid}/approval", json={"decision": "approved"}, headers=_auth(buyer))
            assert approved.status_code == 200, approved.text
            assert approved.json()["approval_source"] == "manual"
            updated[field] = deepcopy(value)
            result = client.put(f"/requests/{rid}", json=updated, headers=_auth(requester))
            assert result.status_code == 200, result.text
            assert result.json()["approval_status"] == "pending", field
            assert result.json()["approved_by"] is None and result.json()["approved_at"] is None
            assert result.json()["approval_source"] is None
        activity = client.get(f"/requests/{rid}/activity", headers=_auth(requester)).json()
        assert sum(a["action"] == "approval_reset" for a in activity) == len(changes)

        # Submitting an unchanged form keeps the decision and existing line IDs.
        approved = client.patch(f"/requests/{rid}/approval", json={"decision": "approved"}, headers=_auth(buyer)).json()
        saved = client.put(f"/requests/{rid}", json=updated, headers=_auth(requester)).json()
        assert saved["approval_status"] == "approved"
        assert saved["approved_by"] == approved["approved_by"]
        assert saved["order_lines"][0]["id"] == approved["order_lines"][0]["id"]
        # Workflow status transitions are not edits to request contents.
        assert client.patch(f"/requests/{rid}/status", json={"status": "closed"}, headers=_auth(buyer)).json()["approval_status"] == "approved"
    finally:
        client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_edits_reapply_threshold_and_document_replacement_invalidates_approval():
    requester = _signin("requester@acme.com")
    buyer = _signin("buyer@acme.com")
    admin = _signin("admin@acme.com")
    payload = _policy_request(100)
    rid = client.post("/requests", json=payload, headers=_auth(requester)).json()["id"]
    update = _policy_update(payload)
    try:
        invalid = dict(update, totalCost=-1)
        assert client.put(f"/requests/{rid}", json=invalid, headers=_auth(requester)).status_code == 422
        assert client.get(f"/requests/{rid}", headers=_auth(requester)).json()["approval_status"] == "approved"
        update["title"] = "Changed under threshold"
        result = client.put(f"/requests/{rid}", json=update, headers=_auth(requester)).json()
        assert result["approval_status"] == "approved" and result["approval_source"] == "automatic"
        update["totalCost"] = 2000
        result = client.put(f"/requests/{rid}", json=update, headers=_auth(requester)).json()
        assert result["approval_status"] == "pending" and result["approval_source"] is None

        first_file = {"file": ("quote.pdf", b"%PDF-first", "application/pdf")}
        second_file = {"file": ("quote.pdf", b"%PDF-second", "application/pdf")}
        assert client.put(f"/requests/{rid}/document", files=first_file, headers=_auth(requester)).status_code == 204
        assert client.patch(f"/requests/{rid}/approval", json={"decision": "approved"}, headers=_auth(buyer)).status_code == 200
        assert client.put(f"/requests/{rid}/document", files=first_file, headers=_auth(requester)).status_code == 204
        assert client.get(f"/requests/{rid}", headers=_auth(requester)).json()["approval_status"] == "approved"
        assert client.put(f"/requests/{rid}/document", files=second_file, headers=_auth(requester)).status_code == 204
        changed = client.get(f"/requests/{rid}", headers=_auth(requester)).json()
        assert changed["approval_status"] == "pending" and changed["approved_by"] is None

        update["totalCost"] = 1999.99
        result = client.put(f"/requests/{rid}", json=update, headers=_auth(requester)).json()
        assert result["approval_status"] == "approved" and result["approval_source"] == "automatic"
    finally:
        client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_legacy_missing_total_is_rechecked_when_set_to_zero():
    from app.models import ProcurementRequest
    requester = _signin("requester@acme.com")
    admin = _signin("admin@acme.com")
    payload = _policy_request(0)
    rid = client.post("/requests", json=payload, headers=_auth(requester)).json()["id"]
    try:
        with TestingSession() as db:
            request = db.get(ProcurementRequest, uuid.UUID(rid))
            request.total_cost = None
            request.approval_status = "pending"
            request.approval_source = None
            request.approved_at = None
            db.commit()
        result = client.put(f"/requests/{rid}", json=_policy_update(payload), headers=_auth(requester)).json()
        assert result["total_cost"] == 0 and result["approval_source"] == "automatic"
    finally:
        client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_customer_rules_are_visible_only_to_the_applicable_organization():
    assert client.get("/commodity-groups/customer-rules").status_code == 401
    acme = _signin("admin@acme.com")
    walmart = _signin("admin@walmart.com")
    rules = client.get("/commodity-groups/customer-rules", headers=_auth(acme)).json()["rules"]
    assert {r["groupId"] for r in rules} == {11, 29, 2001}
    assert any("€20" in r["condition"] for r in rules)
    assert client.get("/commodity-groups/customer-rules", headers=_auth(acme)).json()["approvalPolicy"] == {"limitEur": "2000.00"}
    assert client.get("/commodity-groups/customer-rules", headers=_auth(walmart)).json() == {"rules": [], "approvalPolicy": None}


def test_non_finite_totals_are_rejected_by_request_validation():
    import pytest
    from pydantic import ValidationError
    from app.schemas.requests import RequestCreate
    for amount in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            RequestCreate.model_validate(_policy_request(amount))


def test_suppliers_are_org_scoped_master_data():
    token = _signin("admin@acme.com")

    # auth required
    assert client.get("/suppliers").status_code == 401

    r = client.get("/suppliers/count", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["count"] >= 100

    r = client.get("/suppliers?limit=500", headers=_auth(token))
    assert r.status_code == 200
    suppliers = r.json()
    assert len(suppliers) >= 100
    # every seeded supplier has an empty description, ready for enrichment
    assert all((s["description"] or "") == "" for s in suppliers)
    # sorted by name, carries catalog metadata
    assert suppliers == sorted(suppliers, key=lambda s: s["name"])
    assert all(s["name"] and s["category"] for s in suppliers)

    # search filter works
    r = client.get("/suppliers?search=logistik", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) >= 1
    assert all("logistik" in s["name"].lower() for s in r.json())

    # master data is private to Acme: another tenant sees none of it
    other = _signin("admin@walmart.com")
    assert client.get("/suppliers/count", headers=_auth(other)).json()["count"] == 0
    assert client.get("/suppliers?limit=500", headers=_auth(other)).json() == []


def test_articles_catalog_paginated_and_searchable():
    token = _signin("admin@acme.com")

    # auth required
    assert client.get("/articles").status_code == 401

    r = client.get("/articles/count", headers=_auth(token))
    assert r.status_code == 200
    total = r.json()["count"]
    assert total >= 1000

    # first page: bounded size, correct envelope, ordered by article number
    r = client.get("/articles?limit=25&offset=0", headers=_auth(token))
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == total
    assert page["limit"] == 25 and page["offset"] == 0
    assert len(page["items"]) == 25
    numbers = [a["article_number"] for a in page["items"]]
    assert numbers == sorted(numbers)
    # every article carries its supplier and a price
    assert all(a["supplier_id"] and a["supplier_name"] for a in page["items"])
    assert all(a["description"] and a["unit"] for a in page["items"])

    # second page returns different rows
    r2 = client.get("/articles?limit=25&offset=25", headers=_auth(token))
    assert r2.status_code == 200
    assert {a["id"] for a in r2.json()["items"]}.isdisjoint({a["id"] for a in page["items"]})

    # search matches the seeded office-decoration items
    r = client.get("/articles?search=moss", headers=_auth(token))
    assert r.status_code == 200
    found = r.json()
    assert found["total"] >= 1
    assert any("moss wall" in a["description"].lower() for a in found["items"])

    # catalog is private to Acme: another tenant's catalog is empty
    other = _signin("admin@walmart.com")
    assert client.get("/articles/count", headers=_auth(other)).json()["count"] == 0
    assert client.get("/articles", headers=_auth(other)).json()["total"] == 0
    article_id = found["items"][0]["id"]
    detail = client.get(f"/articles/{article_id}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["id"] == article_id
    assert client.get(f"/articles/{article_id}", headers=_auth(other)).status_code == 404
    assert client.get(f"/articles/{article_id}").status_code == 401


def test_requester_can_only_access_their_own_requests():
    requester = _signin("requester@acme.com")
    buyer = _signin("buyer@acme.com")
    admin = _signin("admin@acme.com")
    other = _signin("admin@walmart.com")
    payload = {"requestorName": "Requester", "titleShortDescription": "Desk", "vendorName": "Vendor",
               "vatId": "", "commodityGroupId": 15, "totalCost": 100, "department": "",
               "orderLines": [{"positionDescription": "Desk", "unitPrice": 100, "amount": 1, "unit": "piece", "totalPrice": 100}]}
    own = client.post("/requests", json=payload, headers=_auth(requester)).json()["id"]
    others = client.post("/requests", json=payload, headers=_auth(buyer)).json()["id"]
    try:
        file = {"file": ("quote.pdf", b"%PDF-1.4", "application/pdf")}
        assert client.put(f"/requests/{others}/document", files=file, headers=_auth(buyer)).status_code == 204
        ids = {item["id"] for item in client.get("/requests", headers=_auth(requester)).json()}
        assert own in ids and others not in ids
        assert client.get(f"/requests/{own}", headers=_auth(requester)).status_code == 200
        assert client.get(f"/requests/{others}", headers=_auth(requester)).status_code == 404
        assert client.get(f"/requests/{others}/activity", headers=_auth(requester)).status_code == 404
        assert client.get(f"/requests/{others}/document", headers=_auth(requester)).status_code == 404
        assert client.put(f"/requests/{others}/document", files=file, headers=_auth(requester)).status_code == 404
        assert client.patch(f"/requests/{others}/status", json={"status": "closed"}, headers=_auth(requester)).status_code == 404
        update = dict(payload, title=payload["titleShortDescription"])
        assert client.put(f"/requests/{others}", json=update, headers=_auth(requester)).status_code == 404
        assert client.delete(f"/requests/{others}", headers=_auth(requester)).status_code == 404
        assert client.get(f"/requests/{own}", headers=_auth(buyer)).status_code == 200
        assert client.get(f"/requests/{others}", headers=_auth(admin)).status_code == 200
        assert client.get(f"/requests/{own}", headers=_auth(other)).status_code == 404

        # Legacy records use user_id when created_by is absent.
        from app.models import ProcurementRequest
        with TestingSession() as db:
            db.get(ProcurementRequest, uuid.UUID(own)).created_by = None
            db.commit()
        assert client.get(f"/requests/{own}", headers=_auth(requester)).status_code == 200
    finally:
        client.delete(f"/requests/{own}", headers=_auth(admin))
        client.delete(f"/requests/{others}", headers=_auth(admin))


def test_article_suggestions_are_ranked_and_tenant_scoped():
    assert client.get("/articles/suggestions", params={"description": "docking station"}).status_code == 401
    acme = _signin("requester@acme.com")
    walmart = _signin("admin@walmart.com")
    response = client.get("/articles/suggestions", params={"description": "USB-C docking station"}, headers=_auth(acme))
    assert response.status_code == 200
    matches = response.json()
    assert 0 < len(matches) <= 5
    assert all("docking" in item["description"].lower() and "station" in item["description"].lower() for item in matches)
    assert matches[0]["supplier_name"] and matches[0]["unit_price"]
    assert client.get("/articles/suggestions", params={"description": "USB-C docking station"}, headers=_auth(walmart)).json() == []
    assert client.get("/articles/suggestions", params={"description": "RHE-00001"}, headers=_auth(acme)).json()[0]["article_number"] == "RHE-00001"
    assert client.get("/articles/suggestions", params={"description": "xyzunmatched"}, headers=_auth(acme)).json() == []
    moss_matches = client.get("/articles/suggestions", params={"description": "Moosbild mit Schriftzug"}, headers=_auth(acme)).json()
    assert moss_matches and all("moss" in item["description"].lower() for item in moss_matches)

    # Both organizations have matching articles; neither should see the other's.
    with TestingSession() as db:
        walmart_org = db.scalar(select(Organization).where(Organization.slug == "walmart"))
        supplier = Supplier(organization_id=walmart_org.id, name="Walmart-only supplier")
        db.add(supplier)
        db.flush()
        article = Article(organization_id=walmart_org.id, supplier_id=supplier.id,
                          article_number="WMT-TEST", description="USB docking station",
                          unit_price=99, currency="EUR", unit="piece", quantity=1)
        db.add(article)
        db.commit()
        supplier_id = supplier.id
    try:
        walmart_matches = client.get("/articles/suggestions", params={"description": "USB docking station"}, headers=_auth(walmart)).json()
        acme_matches = client.get("/articles/suggestions", params={"description": "USB docking station"}, headers=_auth(acme)).json()
        assert [item["article_number"] for item in walmart_matches] == ["WMT-TEST"]
        assert acme_matches and all(item["article_number"] != "WMT-TEST" for item in acme_matches)
    finally:
        with TestingSession() as db:
            db.delete(db.get(Supplier, supplier_id))
            db.commit()


def test_acme_rules_on_create_update_and_other_tenant():
    acme = _signin('requester@acme.com')
    walmart = _signin('admin@walmart.com')
    payload = {'requestorName': 'Test', 'titleShortDescription': 'Office order', 'vendorName': 'Vendor',
               'vatId': '', 'commodityGroupId': 15, 'totalCost': 15, 'department': '',
               'orderLines': [{'positionDescription': 'Kabelbinder', 'unitPrice': 15, 'amount': 1, 'unit': 'piece', 'totalPrice': 15}]}
    rule_payload = {'title': payload['titleShortDescription'], 'orderLines': payload['orderLines']}
    assert client.post('/commodity-groups/customer-rule', json=rule_payload, headers=_auth(acme)).json()['groupId'] == 11
    assert client.post('/commodity-groups/customer-rule', json=rule_payload, headers=_auth(walmart)).json()['groupId'] is None
    response = client.post('/requests', json=payload, headers=_auth(acme))
    assert response.status_code == 201 and response.json()['commodity_group_id'] == 11
    other = client.post('/requests', json=payload, headers=_auth(walmart))
    assert other.status_code == 201 and other.json()['commodity_group_id'] == 15
    update = dict(payload, title=payload['titleShortDescription'])
    update['orderLines'] = [{'positionDescription': 'Toner cartridge', 'unitPrice': 15, 'amount': 1, 'unit': 'piece', 'totalPrice': 15}]
    changed = client.put(f"/requests/{response.json()['id']}", json=update, headers=_auth(acme))
    assert changed.status_code == 200 and changed.json()['commodity_group_id'] == 29
    payload['orderLines'] = [{'positionDescription': 'Software subscription monthly', 'unitPrice': 20, 'amount': 1, 'unit': 'month', 'totalPrice': 20}]
    subscription = client.post('/requests', json=payload, headers=_auth(acme))
    assert subscription.status_code == 201 and subscription.json()['commodity_group_id'] == 2001
    payload['orderLines'].append({'positionDescription': 'Kabelbinder', 'unitPrice': 1, 'amount': 1, 'unit': 'piece', 'totalPrice': 1})
    payload['orderLines'].append({'positionDescription': 'Toner', 'unitPrice': 1, 'amount': 1, 'unit': 'piece', 'totalPrice': 1})
    assert client.post('/requests', json=payload, headers=_auth(acme)).status_code == 422


def test_acme_rule_applies_during_pdf_extraction_only_for_acme(monkeypatch):
    from app.schemas.extraction import ExtractedVendorData, ExtractionResponse
    from app.routers import extraction as extraction_router

    def fake_extraction(text, db):
        data = ExtractedVendorData.model_validate({'title': 'Toner', 'commodityGroupId': 15,
            'commodityGroupName': 'Office Equipment', 'orderLines': [{'positionDescription': 'Printer toner'}]})
        return ExtractionResponse(success=True, data=data)

    monkeypatch.setattr(extraction_router, 'extract_vendor_data', fake_extraction)
    acme = client.post('/extraction', json={'text': 'test'}, headers=_auth(_signin('requester@acme.com')))
    walmart = client.post('/extraction', json={'text': 'test'}, headers=_auth(_signin('admin@walmart.com')))
    assert acme.json()['data']['commodityGroupId'] == 29
    assert walmart.json()['data']['commodityGroupId'] == 15


def test_seed_is_idempotent():
    # The DB was already seeded at import; seeding again must add nothing and
    # must not change the row counts (safe to run on every startup).
    with TestingSession() as db:
        before_cg = db.scalar(select(func.count()).select_from(CommodityGroup))
        before_sup = db.scalar(select(func.count()).select_from(Supplier))
        before_art = db.scalar(select(func.count()).select_from(Article))
        counts = seed(db)
        after_cg = db.scalar(select(func.count()).select_from(CommodityGroup))
        after_sup = db.scalar(select(func.count()).select_from(Supplier))
        after_art = db.scalar(select(func.count()).select_from(Article))

    assert all(v == 0 for v in counts.values()), counts
    assert before_cg == after_cg
    assert before_sup == after_sup
    assert before_art == after_art


def test_demo_user_can_sign_in():
    # The seeded demo account works out of the box (documented in the README).
    r = client.post(
        "/auth/signin", json={"email": "admin@acme.com", "password": "demo1234"}
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    # ...and already belongs to an organization, so the app is usable immediately.
    r = client.get("/organizations/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["slug"] == "acme"
    assert r.json()["role"] == "admin"

    assert client.get("/requests", headers=_auth(token)).status_code == 200


def test_approval_flow_with_roles():
    # Uses the seeded demo org: requester creates, buyer approves.
    def signin(email: str) -> str:
        r = client.post("/auth/signin", json={"email": email, "password": "demo1234"})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    requester = signin("requester@acme.com")
    buyer = signin("buyer@acme.com")
    admin = signin("admin@acme.com")

    payload = {
        "requestorName": "Rene Requester",
        "titleShortDescription": "Laptops",
        "vendorName": "TechSupply",
        "vatId": "DE999",
        "commodityGroupId": 31,
        "totalCost": 4200.0,
        "department": "Engineering",
        "orderLines": [
            {"positionDescription": "Laptop", "unitPrice": 1400, "amount": 3, "unit": "pc", "totalPrice": 4200}
        ],
    }
    # Buyers and admins may approve their own pending requests. Self-rejection
    # remains restricted; the requester role has no approval privileges.
    for reviewer in (buyer, admin):
        own = client.post("/requests", json=payload, headers=_auth(reviewer)).json()["id"]
        assert client.patch(f"/requests/{own}/approval", json={"decision": "rejected"}, headers=_auth(reviewer)).status_code == 403
        response = client.patch(f"/requests/{own}/approval", json={"decision": "approved"}, headers=_auth(reviewer))
        assert response.status_code == 200
        assert response.json()["approval_status"] == "approved"
        assert response.json()["approved_by"] is not None
        client.delete(f"/requests/{own}", headers=_auth(admin))
    r = client.post("/requests", json=payload, headers=_auth(requester))
    assert r.status_code == 201, r.text
    request_id = r.json()["id"]
    # every new request starts pending
    assert r.json()["approval_status"] == "pending"
    assert r.json()["approved_by"] is None

    # requester cannot approve (not a buyer/admin)
    r = client.patch(
        f"/requests/{request_id}/approval",
        json={"decision": "approved"},
        headers=_auth(requester),
    )
    assert r.status_code == 403

    # buyer approves
    r = client.patch(
        f"/requests/{request_id}/approval",
        json={"decision": "approved"},
        headers=_auth(buyer),
    )
    assert r.status_code == 200, r.text
    assert r.json()["approval_status"] == "approved"
    assert r.json()["approved_by"] is not None
    assert r.json()["approved_at"] is not None

    # already decided -> 409 (even for an admin)
    r = client.patch(
        f"/requests/{request_id}/approval",
        json={"decision": "rejected"},
        headers=_auth(admin),
    )
    assert r.status_code == 409

    # admins can also decide; rejection works
    r = client.post("/requests", json=payload, headers=_auth(requester))
    second_id = r.json()["id"]
    r = client.patch(
        f"/requests/{second_id}/approval",
        json={"decision": "rejected"},
        headers=_auth(admin),
    )
    assert r.status_code == 200
    assert r.json()["approval_status"] == "rejected"

    # buyers cannot touch org settings (admin-only)
    assert (
        client.patch(
            "/organizations/me/settings",
            json={"required_fields": []},
            headers=_auth(buyer),
        ).status_code
        == 403
    )

    # cleanup: remove the demo-org requests this test created
    for rid in (request_id, second_id):
        client.delete(f"/requests/{rid}", headers=_auth(admin))


def test_org_settings_required_fields():
    # Fresh org (settings default to empty).
    r = client.post(
        "/auth/signup", json={"email": "settings@example.com", "password": "secret123"}
    )
    token = r.json()["access_token"]
    client.post(
        "/organizations",
        json={"name": "Settings Co", "slug": "settings-co"},
        headers=_auth(token),
    )

    # Default settings: nothing extra required.
    r = client.get("/organizations/me/settings", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"required_fields": []}

    base_payload = {
        "requestorName": "S", "titleShortDescription": "T", "vendorName": "V",
        "vatId": "", "commodityGroupId": 31, "totalCost": 10.0, "department": "",
        "orderLines": [
            {"positionDescription": "x", "unitPrice": 10, "amount": 1, "unit": "pc", "totalPrice": 10}
        ],
    }
    # Without a requirement, a request with blank vat_id/department is accepted.
    assert client.post("/requests", json=base_payload, headers=_auth(token)).status_code == 201

    # Configure vat_id + department as required.
    r = client.patch(
        "/organizations/me/settings",
        json={"required_fields": ["vat_id", "department"]},
        headers=_auth(token),
    )
    assert r.status_code == 200
    assert set(r.json()["required_fields"]) == {"vat_id", "department"}

    # Now the same blank-field request is rejected...
    r = client.post("/requests", json=base_payload, headers=_auth(token))
    assert r.status_code == 422
    assert "vat_id" in r.json()["detail"] and "department" in r.json()["detail"]

    # ...and providing them succeeds.
    ok = dict(base_payload, vatId="DE123", department="IT")
    assert client.post("/requests", json=ok, headers=_auth(token)).status_code == 201

    # Unknown setting keys are rejected (validated at the edge).
    assert (
        client.patch(
            "/organizations/me/settings",
            json={"bogus": True},
            headers=_auth(token),
        ).status_code
        == 422
    )


def test_org_settings_admin_only():
    # A plain member cannot change settings.
    owner = client.post(
        "/auth/signup", json={"email": "so-owner@example.com", "password": "secret123"}
    ).json()["access_token"]
    client.post(
        "/organizations",
        json={"name": "SO Co", "slug": "so-co"},
        headers=_auth(owner),
    )
    invite = client.post(
        "/organizations/me/invites",
        json={"email": "so-member@example.com"},
        headers=_auth(owner),
    )
    from app.models import OrganizationInvite

    with TestingSession() as db:
        token_val = db.scalar(
            select(OrganizationInvite.token).where(
                OrganizationInvite.email == "so-member@example.com"
            )
        )
    member = client.post(
        "/auth/signup", json={"email": "so-member@example.com", "password": "secret123"}
    ).json()["access_token"]
    client.post(
        "/organizations/invites/accept", json={"token": token_val}, headers=_auth(member)
    )

    # member can read settings but not change them
    assert client.get("/organizations/me/settings", headers=_auth(member)).status_code == 200
    r = client.patch(
        "/organizations/me/settings",
        json={"required_fields": ["vat_id"]},
        headers=_auth(member),
    )
    assert r.status_code == 403


if __name__ == "__main__":
    test_full_user_journey()
    test_pdf_parse()
    test_extraction_without_api_key()
    test_activity_log_and_document()
    test_commodity_groups_expanded()
    test_suppliers_seeded_with_empty_descriptions()
    test_seed_is_idempotent()
    test_demo_user_can_sign_in()
    test_approval_flow_with_roles()
    test_org_settings_required_fields()
    test_org_settings_admin_only()
    print("ALL SMOKE TESTS PASSED")
