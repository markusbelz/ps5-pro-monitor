from decimal import Decimal

import pytest

from ps5_monitor.http import Fetcher
from ps5_monitor.models import Condition, Outcome, StockStatus
from ps5_monitor.retailers import REGISTRY
from tests.conftest import MM_URL, NOW, PS_URL, Web, fixture, retailer_cfg

import httpx


def check(key, url, html, **cfg_kw):
    web = Web({url: html})
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(web.handler)), respect_robots=False, sleep=lambda s: None)
    return REGISTRY[key](retailer_cfg(key, url, **cfg_kw)).check(fetcher, NOW)


def test_in_stock_page_is_parsed_completely():
    result = check("playstation_direct", PS_URL, fixture("ps_direct_in_stock.html"))
    assert result.outcome is Outcome.OK
    (offer,) = result.offers
    assert offer.stock is StockStatus.IN_STOCK and offer.condition is Condition.NEW
    assert offer.price == Decimal("899.99") and offer.shipping == Decimal("0.00") and offer.shipping_source == "page"
    assert offer.seller == "Sony Interactive Entertainment" and offer.product_id == "PS5PRO-2TB"


def test_out_of_stock_page():
    (offer,) = check("playstation_direct", PS_URL, fixture("ps_direct_out_of_stock.html")).offers
    assert offer.stock is StockStatus.OUT_OF_STOCK


def test_missing_availability_is_unknown_not_in_stock():
    (offer,) = check("playstation_direct", PS_URL, fixture("unknown_availability.html")).offers
    assert offer.stock is StockStatus.UNKNOWN


def test_shipping_cost_from_page():
    (offer,) = check("mediamarkt", MM_URL, fixture("mediamarkt_in_stock_shipping.html")).offers
    assert offer.shipping == Decimal("4.99") and offer.total == Decimal("953.99")


def test_missing_shipping_stays_unknown_unless_configured():
    url = "https://www.saturn.de/x"
    (offer,) = check("saturn", url, fixture("saturn_unknown_shipping.html")).offers
    assert offer.shipping is None and offer.total is None
    (configured,) = check("saturn", url, fixture("saturn_unknown_shipping.html"), shipping=Decimal("0.00")).offers
    assert configured.shipping == Decimal("0.00") and configured.shipping_source == "config"


def test_invalid_price_becomes_none():
    (offer,) = check("playstation_direct", PS_URL, fixture("invalid_price.html")).offers
    assert offer.price is None


def test_layout_change_without_jsonld_is_no_data_not_out_of_stock():
    result = check("playstation_direct", PS_URL, fixture("no_jsonld.html"))
    assert result.outcome is Outcome.NO_DATA and result.offers == []


def test_malformed_jsonld_is_handled():
    assert check("playstation_direct", PS_URL, fixture("malformed_jsonld.html")).outcome is Outcome.NO_DATA


def test_captcha_page_is_reported_as_blocked():
    assert check("mediamarkt", MM_URL, fixture("blocked_captcha.html")).outcome is Outcome.BLOCKED


def test_amazon_is_an_unsupported_placeholder():
    cfg = retailer_cfg("amazon", "https://www.amazon.de/dp/XXXX")
    fetcher = Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("must not fetch"))))
    result = REGISTRY["amazon"](cfg).check(fetcher, NOW)
    assert result.outcome is Outcome.UNSUPPORTED and "not implemented" in result.error.lower()
