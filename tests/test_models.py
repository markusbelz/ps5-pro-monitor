from decimal import Decimal

import pytest

from ps5_monitor.models import parse_price
from tests.conftest import make_offer


@pytest.mark.parametrize("raw,expected", [
    (899.99, "899.99"), ("899.99", "899.99"), ("899,99", "899.99"), ("1.099,00", "1099.00"),
    ("1,099.00", "1099.00"), ("€ 999,99", "999.99"), (1000, "1000.00"),
])
def test_parse_price_valid(raw, expected):
    assert parse_price(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", [None, "", "auf Anfrage", "0", 0, -5, "-3,00", "1.099", "899.999", True, float("nan")])
def test_parse_price_invalid_or_ambiguous(raw):
    assert parse_price(raw) is None


def test_parse_price_allows_zero_only_when_asked():
    assert parse_price(0) is None
    assert parse_price(0, allow_zero=True) == Decimal("0.00")


def test_total_is_decimal_and_exact():
    offer = make_offer(price=Decimal("0.10"), shipping=Decimal("0.20"))
    assert offer.total == Decimal("0.30")  # would be 0.30000000000000004 with floats


def test_total_unknown_without_shipping():
    assert make_offer(shipping=None).total is None
