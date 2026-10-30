"""Decides whether a single Offer is a genuine, verified purchase opportunity.

Principle: anything that cannot be verified is UNVERIFIED (no alert), never a guess.
Only IN_STOCK offers can qualify.
"""

from __future__ import annotations

import re

from .config import RetailerConfig, Settings
from .models import Condition, Evaluation, Offer, StockStatus, Verdict

PS5_PRO = re.compile(r"(ps\s?5|playstation\s?5)\s*pro", re.IGNORECASE)
OTHER_STORAGE = re.compile(r"\b(1\s?tb|500\s?gb|825\s?gb)\b", re.IGNORECASE)
NOT_NEW = re.compile(
    r"refurbished|reconditioned|generalüberholt|generalueberholt|aufbereitet|gebraucht|"
    r"b-ware|open[\s-]?box|retoure|renewed|wie neu|used|zustand\s*:?\s*(gut|sehr gut)",
    re.IGNORECASE,
)
ACCESSORY = re.compile(
    r"controller|dualsense|headset|pulse|ladestation|charging|skin|hülle|tasche|"
    r"stand\b|ständer|zubehör|accessor|gutschein|guthaben|abo\b|playstation plus|"
    r"disc[\s-]?drive|laufwerk|spiel\b|game\b",
    re.IGNORECASE,
)
CONSOLE_WORD = re.compile(r"konsole|console", re.IGNORECASE)
DRIVE_WORD = re.compile(r"disc[\s-]?drive|laufwerk", re.IGNORECASE)
BUNDLE_WORD = re.compile(r"bundle|set\b|paket", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _seller_accepted(seller: str, accepted: tuple[str, ...]) -> bool:
    return _norm(seller) in {_norm(a) for a in accepted}


def evaluate(offer: Offer, cfg: RetailerConfig, settings: Settings) -> Evaluation:
    total = offer.total
    rejected: list[str] = []
    unverified: list[str] = []

    # 1. Availability: only an explicit IN_STOCK may qualify.
    if offer.stock is StockStatus.OUT_OF_STOCK:
        rejected.append("out of stock / not orderable")
    elif offer.stock is not StockStatus.IN_STOCK:
        unverified.append(f"availability not verifiable ({offer.stock.value})")

    # 2. Condition.
    title = offer.title or ""
    if offer.condition in (Condition.USED, Condition.REFURBISHED):
        rejected.append(f"condition is {offer.condition.value.lower()}")
    elif NOT_NEW.search(title):
        rejected.append("title indicates used/refurbished/open-box")
    elif offer.condition is not Condition.NEW:
        unverified.append("condition not stated as new")

    # 3. Is it really a PS5 Pro console?
    if not title:
        unverified.append("product title missing")
    elif not PS5_PRO.search(title):
        rejected.append("title is not a PS5 Pro")
    elif OTHER_STORAGE.search(title):
        rejected.append("title names a storage size other than 2 TB")
    elif ACCESSORY.search(title) and not CONSOLE_WORD.search(title):
        rejected.append("looks like an accessory/software, not the console")
    elif BUNDLE_WORD.search(title) and not DRIVE_WORD.search(title):
        unverified.append("bundle contents cannot be verified")

    # 4. Seller.
    if not offer.seller:
        unverified.append("seller not identified")
    elif not cfg.allow_third_party and not _seller_accepted(offer.seller, cfg.accepted_sellers):
        rejected.append(f"seller '{offer.seller}' is not an accepted seller (marketplace/third party)")

    # 5. Price, currency, VAT, shipping.
    if offer.currency != "EUR":
        rejected.append(f"currency is {offer.currency}, not EUR")
    if offer.price is None:
        unverified.append("price missing or invalid")
    if not offer.vat_included:
        unverified.append("price excludes VAT; total cannot be verified")
    if offer.shipping is None:
        unverified.append("shipping cost unknown")

    # 6. Threshold (only meaningful once the total is known).
    if total is not None and not rejected:
        if total > settings.max_price:  # default 999.99, so exactly 1000.00 never qualifies
            rejected.append(f"total {total} EUR is above the limit of {settings.max_price} EUR")

    if rejected:
        return Evaluation(Verdict.REJECTED, tuple(rejected + unverified), total)
    if unverified:
        return Evaluation(Verdict.UNVERIFIED, tuple(unverified), total)
    return Evaluation(Verdict.QUALIFIES, ("verified, in stock, new, trusted seller, under limit",), total)
