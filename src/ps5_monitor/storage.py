"""SQLite persistence: offer state (dedup), price history, retailer health, meta."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from .models import Offer, Outcome, RetailerResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS offers (
    offer_key TEXT PRIMARY KEY,
    retailer TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    seller TEXT,
    stock TEXT NOT NULL,
    price TEXT,
    shipping TEXT,
    total TEXT,
    verdict TEXT NOT NULL,
    qualifying INTEGER NOT NULL DEFAULT 0,
    last_notified_total TEXT,
    last_notified_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_key TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    stock TEXT NOT NULL,
    price TEXT,
    total TEXT,
    verdict TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_key_time ON observations (offer_key, observed_at);
CREATE TABLE IF NOT EXISTS retailer_status (
    retailer TEXT PRIMARY KEY,
    last_outcome TEXT NOT NULL,
    last_error TEXT,
    last_checked_at TEXT NOT NULL,
    last_success_at TEXT
);
"""

HISTORY_DAYS = 90


def offer_key(offer: Offer) -> str:
    """Stable identity: retailer + product id + seller + URL without query/fragment.

    Title, description and other volatile HTML content are deliberately excluded so
    cosmetic page changes do not create "new" offers and duplicate alerts.
    """
    parts = urlsplit(offer.url)
    url = f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/").lower()
    seller = " ".join((offer.seller or "").casefold().split())
    raw = "|".join([offer.retailer, offer.product_id.casefold(), seller, url])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class OfferState:
    offer_key: str
    stock: str
    total: Decimal | None
    verdict: str
    qualifying: bool
    last_notified_total: Decimal | None
    last_seen: str


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- offers -----------------------------------------------------------
    def get_offer(self, key: str) -> OfferState | None:
        row = self.db.execute("SELECT * FROM offers WHERE offer_key = ?", (key,)).fetchone()
        if row is None:
            return None
        return OfferState(
            offer_key=row["offer_key"],
            stock=row["stock"],
            total=_dec(row["total"]),
            verdict=row["verdict"],
            qualifying=bool(row["qualifying"]),
            last_notified_total=_dec(row["last_notified_total"]),
            last_seen=row["last_seen"],
        )

    def save_offer(
        self,
        key: str,
        offer: Offer,
        verdict: str,
        *,
        qualifying: bool,
        notified_total: Decimal | None = None,
        notified_at: datetime | None = None,
    ) -> None:
        """Upsert the offer state. last_notified_* only change when a notification was sent."""
        now = offer.checked_at.isoformat()
        self.db.execute(
            """
            INSERT INTO offers (offer_key, retailer, url, title, seller, stock, price, shipping, total,
                                verdict, qualifying, last_notified_total, last_notified_at, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(offer_key) DO UPDATE SET
                url=excluded.url, title=excluded.title, seller=excluded.seller, stock=excluded.stock,
                price=excluded.price, shipping=excluded.shipping, total=excluded.total,
                verdict=excluded.verdict, qualifying=excluded.qualifying,
                last_notified_total=COALESCE(excluded.last_notified_total, offers.last_notified_total),
                last_notified_at=COALESCE(excluded.last_notified_at, offers.last_notified_at),
                last_seen=excluded.last_seen
            """,
            (
                key, offer.retailer, offer.url, offer.title, offer.seller, offer.stock.value,
                _str(offer.price), _str(offer.shipping), _str(offer.total), verdict, int(qualifying),
                _str(notified_total), notified_at.isoformat() if notified_at else None, now, now,
            ),
        )
        self.db.commit()

    def record_observation(self, key: str, offer: Offer, verdict: str) -> None:
        self.db.execute(
            "INSERT INTO observations (offer_key, observed_at, stock, price, total, verdict) VALUES (?,?,?,?,?,?)",
            (key, offer.checked_at.isoformat(), offer.stock.value, _str(offer.price), _str(offer.total), verdict),
        )
        self.db.commit()

    def prune_history(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=HISTORY_DAYS)).isoformat()
        self.db.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff,))
        self.db.commit()

    def known_offers(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM offers ORDER BY retailer, last_seen DESC").fetchall()

    # --- retailer health --------------------------------------------------
    def record_retailer(self, result: RetailerResult, now: datetime) -> None:
        success = now.isoformat() if result.outcome is Outcome.OK else None
        self.db.execute(
            """
            INSERT INTO retailer_status (retailer, last_outcome, last_error, last_checked_at, last_success_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(retailer) DO UPDATE SET
                last_outcome=excluded.last_outcome, last_error=excluded.last_error,
                last_checked_at=excluded.last_checked_at,
                last_success_at=COALESCE(excluded.last_success_at, retailer_status.last_success_at)
            """,
            (result.retailer, result.outcome.value, result.error, now.isoformat(), success),
        )
        self.db.commit()

    def retailer_statuses(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM retailer_status ORDER BY retailer").fetchall()

    # --- meta -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()
