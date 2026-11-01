"""Customer-specific classification rules for the seeded Acme organization."""
import re
from decimal import Decimal, InvalidOperation

ELECTRICAL_ENGINEERING = 11
IT_HARDWARE = 29
OFFICE_SUPPLIES = 2001
OFFICE_SUPPLIES_ROW = (OFFICE_SUPPLIES, 'Office & Administration', 'Office Supplies')
MONTHLY_SUBSCRIPTION_LIMIT = Decimal('20.00')

# Kept alongside the enforcement logic so admins can inspect the active rules.
ACME_CATEGORY_RULES = [
    {'item': 'Cable ties', 'category': 'Electrical Engineering', 'groupId': ELECTRICAL_ENGINEERING,
     'condition': 'Including Kabelbinder and zip ties.'},
    {'item': 'Printer toner', 'category': 'IT Hardware', 'groupId': IT_HARDWARE,
     'condition': 'Including toner cartridges.'},
    {'item': 'Small software subscriptions', 'category': 'Office Supplies', 'groupId': OFFICE_SUPPLIES,
     'condition': f'Single-line subscription with an explicit monthly period and a total up to €{MONTHLY_SUBSCRIPTION_LIMIT:.0f} per month. Threshold and tax treatment need customer validation.'},
]


def acme_group(title: str, lines) -> tuple[int | None, bool]:
    """Return a group and whether incompatible rules were found in one request.

    Monthly means an explicit billing period and at most EUR 20 per request line,
    using its stated line total. Ambiguous or mixed subscription pricing is not guessed.
    """
    groups = set()
    small_monthly_software = False
    for line in lines:
        description = getattr(line, 'position_description', '') or ''
        unit = getattr(line, 'unit', '') or ''
        text = f'{title} {description} {unit}'.casefold()
        if re.search(r'kabelbinder|cable[\s-]*ties?\b|zip[\s-]*ties?\b', text):
            groups.add(ELECTRICAL_ENGINEERING)
        if re.search(r'\btoner\b|tonerkartusche|toner[\s-]*cartridge', text):
            groups.add(IT_HARDWARE)
        software = re.search(r'software|\bsaas\b|adobe\s+creative\s+cloud|microsoft\s+365|office\s+365', text)
        subscription = re.search(r'subscription|abonnement|\babo\b|lizenz|licen[cs]e', text)
        monthly = re.search(r'monthly|per\s+month|/\s*month|monatlich|pro\s+monat|/\s*monat|\bmonat\b|\bmonth\b', text)
        if software and subscription and monthly:
            try:
                total = Decimal(str(line.total_price))
                if total.is_finite() and Decimal('0') < total <= MONTHLY_SUBSCRIPTION_LIMIT:
                    small_monthly_software = True
            except (InvalidOperation, ValueError):
                pass
    if small_monthly_software and (len(lines) == 1 or groups):
        groups.add(OFFICE_SUPPLIES)
    return (next(iter(groups)) if len(groups) == 1 else None, len(groups) > 1)


def is_acme(membership) -> bool:
    return bool(membership.organization and membership.organization.slug == 'acme')
