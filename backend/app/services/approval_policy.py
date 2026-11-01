"""Approval thresholds use the recorded request total rounded to cents."""
from decimal import Decimal, ROUND_HALF_UP

from app.services.acme_categories import is_acme

ACME_AUTO_APPROVAL_LIMIT = Decimal("2000.00")


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def auto_approval_applies(membership, total: float | Decimal | None) -> bool:
    return bool(is_acme(membership) and total is not None
                and Decimal("0") <= money(total) < ACME_AUTO_APPROVAL_LIMIT)
