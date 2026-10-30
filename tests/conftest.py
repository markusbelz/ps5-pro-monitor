from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from ps5_monitor.config import RetailerConfig, Settings
from ps5_monitor.http import Fetcher
from ps5_monitor.models import Condition, Offer, StockStatus
from ps5_monitor.monitor import Monitor
from ps5_monitor.retailers import REGISTRY
from ps5_monitor.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 29, 12, 32, 8, tzinfo=timezone.utc)
PS_URL = "https://direct.playstation.com/de-de/ps5-pro-test-product"
MM_URL = "https://www.mediamarkt.de/de/product/ps5-pro-test.html"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def settings() -> Settings:
    return Settings(telegram_token="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789", telegram_chat_id="-100987654")


def retailer_cfg(key="playstation_direct", url=PS_URL, shipping=None, third_party=False, trusted=()) -> RetailerConfig:
    return RetailerConfig(
        key=key, url=url, shipping_eur=shipping, allow_third_party=third_party,
        accepted_sellers=tuple(REGISTRY[key].default_sellers) + tuple(trusted),
    )


@pytest.fixture
def cfg() -> RetailerConfig:
    return retailer_cfg()


def make_offer(**kw) -> Offer:
    base = dict(
        retailer="playstation_direct", retailer_name="PlayStation Direct Germany", url=PS_URL,
        product_id="PS5PRO-2TB", title="Sony PlayStation 5 Pro Konsole 2 TB", seller="Sony",
        condition=Condition.NEW, stock=StockStatus.IN_STOCK, price=Decimal("899.99"),
        shipping=Decimal("0.00"), shipping_source="page", checked_at=NOW,
    )
    base.update(kw)
    return Offer(**base)


class FakeNotifier:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.offers: list[tuple[Offer, object]] = []
        self.texts: list[str] = []

    def send_offer(self, offer, evaluation):
        from ps5_monitor.notifier import NotificationError
        if self.fail:
            raise NotificationError("Telegram sendMessage failed: HTTP 500")
        self.offers.append((offer, evaluation))

    def send_text(self, text):
        self.texts.append(text)


class Web:
    """Mutable fake internet: url -> html | Exception | (status, body)."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        page = self.pages.get(url)
        if isinstance(page, Exception):
            raise page
        if isinstance(page, tuple):
            return httpx.Response(page[0], text=page[1])
        if page is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=page)


def build_monitor(tmp_path, settings, web: Web, configs, notifier, *, db=None, clock=None, sleeps=None, watchers=None):
    client = httpx.Client(transport=httpx.MockTransport(web.handler))
    fetcher = Fetcher(client, max_attempts=3, respect_robots=False, sleep=(sleeps.append if sleeps is not None else lambda s: None))
    storage = Storage(db or tmp_path / "state.db")
    retailers = [REGISTRY[c.key](c) for c in configs]
    return Monitor(settings, retailers, fetcher, storage, notifier, clock=clock or (lambda: NOW), watchers=watchers)
