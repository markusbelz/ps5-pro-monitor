from decimal import Decimal

import pytest

from ps5_monitor.evaluate import evaluate
from ps5_monitor.models import Condition, StockStatus, Verdict
from tests.conftest import make_offer, retailer_cfg


def verdict(settings, cfg, **kw):
    return evaluate(make_offer(**kw), cfg, settings)


@pytest.mark.parametrize("price,expected", [
    ("899.99", Verdict.QUALIFIES),
    ("999.99", Verdict.QUALIFIES),
    ("1000.00", Verdict.REJECTED),  # exactly 1000.00 must NOT qualify
    ("1000.01", Verdict.REJECTED),
])
def test_price_thresholds(settings, cfg, price, expected):
    assert verdict(settings, cfg, price=Decimal(price)).verdict is expected


def test_shipping_is_included_in_total(settings, cfg):
    ok = verdict(settings, cfg, price=Decimal("949.00"), shipping=Decimal("4.99"))
    assert ok.verdict is Verdict.QUALIFIES and ok.total == Decimal("953.99")
    too_much = verdict(settings, cfg, price=Decimal("996.00"), shipping=Decimal("4.99"))
    assert too_much.verdict is Verdict.REJECTED and too_much.total == Decimal("1000.99")


def test_unknown_shipping_is_unverified_not_a_confirmed_deal(settings, cfg):
    result = verdict(settings, cfg, shipping=None)
    assert result.verdict is Verdict.UNVERIFIED and result.total is None
    assert any("shipping" in r for r in result.reasons)


def test_out_of_stock_is_rejected(settings, cfg):
    assert verdict(settings, cfg, stock=StockStatus.OUT_OF_STOCK).verdict is Verdict.REJECTED


@pytest.mark.parametrize("stock", [StockStatus.UNKNOWN, StockStatus.ERROR])
def test_unknown_stock_never_qualifies(settings, cfg, stock):
    assert verdict(settings, cfg, stock=stock).verdict is Verdict.UNVERIFIED


def test_third_party_seller_rejected_by_default(settings):
    cfg = retailer_cfg("mediamarkt", "https://www.mediamarkt.de/x")
    assert verdict(settings, cfg, seller="GamesDeals24 GmbH").verdict is Verdict.REJECTED
    assert verdict(settings, cfg, seller="MediaMarkt").verdict is Verdict.QUALIFIES


def test_third_party_allowed_only_if_configured(settings):
    cfg = retailer_cfg("mediamarkt", "https://www.mediamarkt.de/x", third_party=True)
    assert verdict(settings, cfg, seller="GamesDeals24 GmbH").verdict is Verdict.QUALIFIES


def test_missing_seller_is_unverified(settings, cfg):
    assert verdict(settings, cfg, seller=None).verdict is Verdict.UNVERIFIED


def test_expert_needs_explicit_trusted_seller(settings):
    plain = retailer_cfg("expert", "https://www.expert.de/x")
    assert verdict(settings, plain, seller="expert Technomarkt Musterstadt").verdict is Verdict.REJECTED
    trusted = retailer_cfg("expert", "https://www.expert.de/x", trusted=("expert Technomarkt Musterstadt",))
    assert verdict(settings, trusted, seller="Expert Technomarkt  Musterstadt").verdict is Verdict.QUALIFIES


@pytest.mark.parametrize("kw", [
    dict(condition=Condition.REFURBISHED),
    dict(condition=Condition.USED),
    dict(title="Sony PlayStation 5 Pro Konsole 2 TB - generalüberholt"),
    dict(title="PS5 Pro Konsole Open Box"),
])
def test_refurbished_or_used_rejected(settings, cfg, kw):
    assert verdict(settings, cfg, **kw).verdict is Verdict.REJECTED


def test_condition_not_stated_is_unverified(settings, cfg):
    assert verdict(settings, cfg, condition=Condition.UNKNOWN).verdict is Verdict.UNVERIFIED


@pytest.mark.parametrize("title", [
    "Sony DualSense Controller für PS5 Pro",
    "PlayStation 5 Pro Disc Drive",
    "Sony PlayStation 5 Slim Konsole",
    "PS5 Pro 1 TB Konsole",
])
def test_wrong_products_rejected(settings, cfg, title):
    assert verdict(settings, cfg, title=title).verdict is Verdict.REJECTED


def test_console_with_disc_drive_bundle_is_accepted(settings, cfg):
    title = "Sony PlayStation 5 Pro Konsole 2 TB inkl. Disc Drive Laufwerk"
    assert verdict(settings, cfg, title=title, price=Decimal("999.00")).verdict is Verdict.QUALIFIES


def test_unclear_bundle_is_unverified(settings, cfg):
    assert verdict(settings, cfg, title="PS5 Pro Konsole Bundle mit Zubehör-Paket").verdict is Verdict.UNVERIFIED


@pytest.mark.parametrize("kw,expected", [
    (dict(price=None), Verdict.UNVERIFIED),
    (dict(title=None), Verdict.UNVERIFIED),
    (dict(vat_included=False), Verdict.UNVERIFIED),
    (dict(currency="USD"), Verdict.REJECTED),
])
def test_invalid_or_incomplete_data(settings, cfg, kw, expected):
    assert verdict(settings, cfg, **kw).verdict is expected


def test_threshold_is_configurable(settings, cfg):
    from dataclasses import replace
    strict = replace(settings, max_price=Decimal("850.00"))
    assert verdict(strict, cfg, price=Decimal("899.99")).verdict is Verdict.REJECTED
