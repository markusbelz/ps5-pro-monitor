"""Common retailer adapter interface."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime
from typing import ClassVar

from ..config import RetailerConfig
from ..http import Blocked, FetchError, Fetcher
from ..models import Offer, Outcome, RetailerResult
from .jsonld import looks_blocked, parse_product_page

log = logging.getLogger(__name__)


class Retailer(ABC):
    key: ClassVar[str]
    display_name: ClassVar[str]
    allowed_hosts: ClassVar[tuple[str, ...]]
    default_sellers: ClassVar[tuple[str, ...]] = ()
    # True only after the adapter was verified against real, live product data.
    live_validated: ClassVar[bool] = False
    limitation: ClassVar[str] = ""

    def __init__(self, cfg: RetailerConfig):
        self.cfg = cfg

    @abstractmethod
    def parse(self, html: str, now: datetime) -> list[Offer]:
        """Turn a product page into offers. Must never raise on missing fields."""

    def check(self, fetcher: Fetcher, now: datetime) -> RetailerResult:
        base = dict(retailer=self.key, retailer_name=self.display_name)
        try:
            html = fetcher.get(self.cfg.url, self.allowed_hosts)
        except Blocked as exc:
            return RetailerResult(outcome=Outcome.BLOCKED, error=str(exc), **base)
        except FetchError as exc:
            return RetailerResult(outcome=Outcome.ERROR, error=str(exc), **base)
        try:
            offers = self.parse(html, now)
        except Exception as exc:  # a parser bug must not take down the other retailers
            log.exception("parser crashed", extra={"retailer": self.key})
            return RetailerResult(outcome=Outcome.NO_DATA, error=f"parser error: {type(exc).__name__}", **base)
        if not offers:
            if looks_blocked(html):
                return RetailerResult(outcome=Outcome.BLOCKED, error="bot-protection page detected", **base)
            return RetailerResult(
                outcome=Outcome.NO_DATA, error="no product data found (page layout may have changed)", **base
            )
        offers = [self._apply_configured_shipping(o) for o in offers]
        return RetailerResult(outcome=Outcome.OK, offers=offers, **base)

    def _apply_configured_shipping(self, offer: Offer) -> Offer:
        if offer.shipping is None and self.cfg.shipping_eur is not None:
            return replace(offer, shipping=self.cfg.shipping_eur, shipping_source="config")
        return offer


class JsonLdRetailer(Retailer):
    """Adapter that reads schema.org JSON-LD from the product page."""

    def parse(self, html: str, now: datetime) -> list[Offer]:
        return parse_product_page(
            html, self.cfg.url, retailer=self.key, retailer_name=self.display_name, now=now
        )


class UnsupportedRetailer(Retailer):
    """Documented placeholder: nothing is fetched, nothing is claimed."""

    def parse(self, html: str, now: datetime) -> list[Offer]:  # pragma: no cover
        return []

    def check(self, fetcher: Fetcher, now: datetime) -> RetailerResult:
        return RetailerResult(
            retailer=self.key,
            retailer_name=self.display_name,
            outcome=Outcome.UNSUPPORTED,
            error=self.limitation,
        )
