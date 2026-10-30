"""schema.org JSON-LD extraction. Data is only parsed with json.loads, never executed."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..models import Condition, Offer, StockStatus, parse_price

IN_STOCK = {"instock", "onlineonly", "limitedavailability", "instoreonly"}
OUT_OF_STOCK = {"outofstock", "soldout", "discontinued", "preorder", "presale", "backorder"}
BLOCK_MARKERS = (
    "captcha", "robot check", "pardon our interruption", "access denied",
    "unusual traffic", "are you a human", "bot detection",
)


def looks_blocked(html: str) -> bool:
    head = html[:30_000].lower()
    return any(marker in head for marker in BLOCK_MARKERS)


def _last_segment(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("@id") or value.get("name")
    return str(value or "").rstrip("/").rsplit("/", 1)[-1].replace("schema:", "").lower()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _walk(node: Any) -> Iterable[dict]:
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _walk(node[key])


def extract_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue  # malformed block: ignore, other blocks may still be fine
        for node in _walk(data):
            types = [_last_segment(t) for t in _as_list(node.get("@type"))]
            if "product" in types:
                products.append(node)
    return products


def _stock(raw: Any) -> StockStatus:
    key = _last_segment(raw)
    if key in IN_STOCK:
        return StockStatus.IN_STOCK
    if key in OUT_OF_STOCK:
        return StockStatus.OUT_OF_STOCK
    return StockStatus.UNKNOWN  # missing or unrecognised: never assume availability


def _condition(raw: Any) -> Condition:
    key = _last_segment(raw)
    if key == "newcondition":
        return Condition.NEW
    if key == "refurbishedcondition":
        return Condition.REFURBISHED
    if key in {"usedcondition", "damagedcondition"}:
        return Condition.USED
    return Condition.UNKNOWN


def _seller(offer: dict) -> str | None:
    raw = offer.get("seller") or offer.get("offeredBy")
    if isinstance(raw, dict):
        raw = raw.get("name")
    raw = str(raw).strip() if raw else ""
    return raw or None


def _shipping(offer: dict) -> Decimal | None:
    """Shipping cost to Germany if the page states it explicitly, else None (unknown)."""
    candidates = [d for d in _as_list(offer.get("shippingDetails")) if isinstance(d, dict)]
    if not candidates:
        return None

    def to_germany(detail: dict) -> bool:
        countries = [
            str(d.get("addressCountry", "")).upper()
            for d in _as_list(detail.get("shippingDestination"))
            if isinstance(d, dict)
        ]
        return not countries or "DE" in countries

    for detail in candidates:
        if not to_germany(detail):
            continue
        rate = detail.get("shippingRate")
        if isinstance(rate, dict) and str(rate.get("currency", "EUR")).upper() == "EUR":
            return parse_price(rate.get("value"), allow_zero=True)
    return None


def _product_id(product: dict, url: str) -> str:
    for field in ("sku", "gtin13", "gtin", "mpn", "productID"):
        if product.get(field):
            return str(product[field]).strip()
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or "unknown"


def parse_product_page(html: str, url: str, *, retailer: str, retailer_name: str, now: datetime) -> list[Offer]:
    """One Offer per (product, offer) pair found in JSON-LD. Missing fields stay None/UNKNOWN."""
    offers: list[Offer] = []
    for product in extract_products(html):
        raw_offers: list[Any] = []
        for entry in _as_list(product.get("offers")):
            if isinstance(entry, dict) and _last_segment(entry.get("@type")) == "aggregateoffer":
                raw_offers.extend(_as_list(entry.get("offers")))  # lowPrice alone is not an offer
            else:
                raw_offers.append(entry)
        for entry in raw_offers:
            if not isinstance(entry, dict):
                continue
            spec = entry.get("priceSpecification")
            spec = spec[0] if isinstance(spec, list) and spec else spec
            spec = spec if isinstance(spec, dict) else {}
            price = parse_price(entry.get("price", spec.get("price")))
            vat = spec.get("valueAddedTaxIncluded")
            title = product.get("name")
            offers.append(
                Offer(
                    retailer=retailer,
                    retailer_name=retailer_name,
                    url=url,
                    product_id=_product_id(product, url),
                    title=str(title).strip() if title else None,
                    seller=_seller(entry),
                    condition=_condition(entry.get("itemCondition") or product.get("itemCondition")),
                    stock=_stock(entry.get("availability")),
                    price=price,
                    currency=str(entry.get("priceCurrency") or spec.get("priceCurrency") or "EUR").upper(),
                    shipping=_shipping(entry),
                    shipping_source="page" if _shipping(entry) is not None else "unknown",
                    vat_included=not (vat is False or str(vat).lower() == "false"),
                    checked_at=now,
                )
            )
    return offers

