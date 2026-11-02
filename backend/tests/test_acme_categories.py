from types import SimpleNamespace

import pytest

from app.services.acme_categories import acme_group, ELECTRICAL_ENGINEERING, IT_HARDWARE, OFFICE_SUPPLIES


def line(description, total=0, unit='piece'):
    return SimpleNamespace(position_description=description, total_price=total, unit=unit)


@pytest.mark.parametrize(('description', 'expected'), [
    ('Kabelbinder 100 Stück', ELECTRICAL_ENGINEERING),
    ('Cable ties, black', ELECTRICAL_ENGINEERING),
    ('Printer toner cartridge', IT_HARDWARE),
    ('Tonerkartusche schwarz', IT_HARDWARE),
])
def test_product_category_rules(description, expected):
    assert acme_group('', [line(description)]) == (expected, False)


def test_monthly_software_threshold_and_ambiguity():
    assert acme_group('', [line('Software subscription monthly', 19.99)]) == (OFFICE_SUPPLIES, False)
    assert acme_group('', [line('Software subscription monthly', 20)]) == (OFFICE_SUPPLIES, False)
    assert acme_group('Adobe Creative Cloud subscription', [line('Monthly licence', 20)]) == (OFFICE_SUPPLIES, False)
    assert acme_group('', [line('Software subscription monthly', 20.01)]) == (None, False)
    assert acme_group('', [line('Software subscription', 10)]) == (None, False)
    assert acme_group('', [line('Software subscription annually', 12)]) == (None, False)
    assert acme_group('', [line('Software subscription monthly', 10), line('Additional service', 5)]) == (None, False)


def test_conflicting_categories_require_separate_requests():
    assert acme_group('', [line('Kabelbinder'), line('Toner')]) == (None, True)
    assert acme_group('', [line('Software subscription monthly', 10), line('Kabelbinder')]) == (None, True)
