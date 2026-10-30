"""Core data models. All money is Decimal; floats never touch prices."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
import re

CENT = Decimal("0.01")


class StockStatus(str, enum.Enum):
    IN_STOCK = "IN_STOCK"  # retailer explicitly says it can be ordered
    OUT_OF_STOCK = "OUT_OF_STOCK"  # retailer explicitly says it cannot
    UNKNOWN = "UNKNOWN"  # cannot be determined reliably
    ERROR = "ERROR"  # the check failed


class Condition(str, enum.Enum):
    NEW = "NEW"
    USED = "USED"
    REFURBISHED = "REFURBISHED"
    UNKNOWN = "UNKNOWN"


class Verdict(str, enum.Enum):
    QUALIFIES = "QUALIFIES"  # verified, in stock, new, trusted seller, total under limit
    UNVERIFIED = "UNVERIFIED"  # might qualify, but data is missing/ambiguous -> no alert
    REJECTED = "REJECTED"  # definitively does not qualify


class Outcome(str, enum.Enum):
    OK = "OK"  # page fetched and product data found
    NO_DATA = "NO_DATA"  # page fetched, but no usable product data (HTML changed?)
    BLOCKED = "BLOCKED"  # bot protection / robots.txt / 403 -> we do NOT work around it
    ERROR = "ERROR"  # network or HTTP failure after bounded retries
    UNSUPPORTED = "UNSUPPORTED"  # placeholder adapter, nothing is checked


def quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_price(value: object, *, allow_zero: bool = False) -> Decimal | None:
    """Parse a price into a Decimal with at most 2 decimals, or None if invalid.

    Handles 899.99, "899.99", "899,99", "1.099,00", "1,099.00", "€ 899,99".
    Anything ambiguous ("1.099"), negative, zero, NaN or with >2 decimals returns None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, (int, float)):
        text = repr(value)
    else:
        text = str(value)
    text = re.sub(r"[^\d.,\-]", "", text.strip())
    if not text or text.startswith("-"):
        return None
    if "." in text and "," in text:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text:
        text = text.replace(",", ".") if text.count(",") == 1 else ""
    elif text.count(".") > 1:
        text = text.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", text) else ""
    elif re.fullmatch(r"\d+\.\d{3}", text):
        return None  # "1.099" is ambiguous (1099 or 1.099?) -> treat as invalid, never guess
    try:
        number = Decimal(text)
    except Exception:
        return None
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        return None
    if number != number.quantize(CENT):
        return None
    return quantize(number)


@dataclass(frozen=True)
class Offer:
    retailer: str  # config key, e.g. "playstation_direct"
    retailer_name: str
    url: str
    product_id: str
    title: str | None
    seller: str | None
    condition: Condition
    stock: StockStatus
    price: Decimal | None
    checked_at: datetime
    shipping: Decimal | None = None
    shipping_source: str = "unknown"  # "page" | "config" | "unknown"
    currency: str = "EUR"
    vat_included: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> Decimal | None:
        """Estimated total (price + shipping). None if either part is unknown."""
        if self.price is None or self.shipping is None:
            return None
        return quantize(self.price + self.shipping)


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    reasons: tuple[str, ...]
    total: Decimal | None


@dataclass
class RetailerResult:
    retailer: str
    retailer_name: str
    outcome: Outcome
    offers: list[Offer] = field(default_factory=list)
    error: str | None = None
