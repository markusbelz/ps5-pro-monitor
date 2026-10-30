"""Telegram notifications via the official Bot API (sendMessage)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import httpx

from .logging_config import register_secret
from .models import Evaluation, Offer

log = logging.getLogger(__name__)
BERLIN = ZoneInfo("Europe/Berlin")
API = "https://api.telegram.org"


class NotificationError(Exception):
    """Sending failed. The message never contains the bot token."""


class Notifier(Protocol):
    def send_offer(self, offer: Offer, evaluation: Evaluation) -> None: ...
    def send_text(self, text: str) -> None: ...


def euro(value: Decimal | None) -> str:
    return "unknown" if value is None else f"€{value:.2f}"


def berlin_time(moment: datetime) -> str:
    return moment.astimezone(BERLIN).strftime("%H:%M:%S Europe/Berlin")


def format_offer_message(offer: Offer, evaluation: Evaluation) -> str:
    """Only called for verified (QUALIFIES) offers; every field shown is verified or configured."""
    shipping_note = " (configured)" if offer.shipping_source == "config" else ""
    return (
        "🎮 PS5 PRO AVAILABLE\n\n"
        f"Retailer: {offer.retailer_name}\n"
        f"Product: {offer.title or 'Sony PlayStation 5 Pro'}\n"
        "Condition: New\n"
        f"Seller: {offer.seller}\n"
        "Availability: In stock\n"
        f"Product price: {euro(offer.price)}\n"
        f"Shipping: {euro(offer.shipping)}{shipping_note}\n"
        f"Total: {euro(evaluation.total)}\n\n"
        "Status: VERIFIED\n"
        f"Last checked: {berlin_time(offer.checked_at)}\n\n"
        f"BUY NOW:\n{offer.url}"
    )


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 15.0,
    ):
        register_secret(token)
        register_secret(chat_id)
        self._token = token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=timeout)
        self._max_attempts = max_attempts
        self._sleep = sleep

    def send_offer(self, offer: Offer, evaluation: Evaluation) -> None:
        self.send_text(format_offer_message(offer, evaluation))

    def send_text(self, text: str) -> None:
        url = f"{API}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text}
        last_error = "unknown error"
        for attempt in range(1, self._max_attempts + 1):
            delay = min(2.0 ** attempt, 20.0)
            try:
                response = self._client.post(url, json=payload)
            except httpx.HTTPError as exc:
                # str(exc) can contain the request URL (= token), so only the type is kept.
                last_error = f"network error ({type(exc).__name__})"
            else:
                if response.status_code == 200 and self._is_ok(response):
                    return
                description = self._description(response)
                last_error = f"HTTP {response.status_code}: {description}"
                if response.status_code == 429:
                    delay = min(float(self._retry_after(response) or delay), 30.0)
                elif response.status_code < 500:
                    break  # 4xx (bad token / chat id) will not fix itself: no retry
            if attempt < self._max_attempts:
                log.warning("telegram send failed, retrying", extra={"attempt": attempt, "error": last_error})
                self._sleep(delay)
        raise NotificationError(f"Telegram sendMessage failed: {last_error}")

    @staticmethod
    def _is_ok(response: httpx.Response) -> bool:
        try:
            return bool(response.json().get("ok"))
        except ValueError:
            return False

    @staticmethod
    def _description(response: httpx.Response) -> str:
        try:
            return str(response.json().get("description", "no description"))[:200]
        except ValueError:
            return "non-JSON response"

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        try:
            return int(response.json().get("parameters", {}).get("retry_after"))
        except (ValueError, TypeError, AttributeError):
            return None
