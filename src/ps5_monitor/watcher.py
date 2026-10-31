"""Page watcher for official pages.

Idea: while the PS5 Pro is not purchasable, Sony's pages show an 'unavailable' notice. The watcher
remembers whether such a notice is present and raises an early heads-up when it disappears.
This is NOT a verified offer (price/stock/seller are not checked); the message says so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .config import WatcherConfig
from .http import Blocked, FetchError, Fetcher
from .models import Outcome
from .notifier import berlin_time

log = logging.getLogger(__name__)


@dataclass
class WatchResult:
    name: str
    outcome: Outcome
    error: str | None = None
    alert: str | None = None  # message to send, if any
    new_state: dict[str, Any] | None = None  # persisted only after the alert was delivered
    detail: str = ""


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(" ")
    return " ".join(text.replace("\xa0", " ").replace("’", "'").lower().split())


def state_key(cfg: WatcherConfig) -> str:
    return f"watch:{cfg.name}"


def load_state(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and "unavailable" in data else None


def check_watcher(cfg: WatcherConfig, fetcher: Fetcher, prev: dict[str, Any] | None, now: datetime) -> WatchResult:
    try:
        page = fetcher.fetch(cfg.url, cfg.allowed_hosts)
    except Blocked as exc:
        return WatchResult(cfg.name, Outcome.BLOCKED, error=str(exc))
    except FetchError as exc:
        return WatchResult(cfg.name, Outcome.ERROR, error=str(exc))

    text = visible_text(page.text)
    present = [p for p in cfg.unavailable_phrases if p in text]
    final = urlsplit(page.final_url)
    final_short = f"{final.hostname}{final.path}"
    if len(text) < 200:
        # An (almost) empty page is not evidence of availability; never alert on it.
        return WatchResult(cfg.name, Outcome.NO_DATA, error=f"page has almost no visible text ({len(text)} chars)")

    unavailable = bool(present)
    state = {"unavailable": unavailable, "final": final_short}
    detail = f"unavailable-notice={'yes' if unavailable else 'NO'} phrases={present} page={final_short} chars={len(text)}"

    alert = None
    if prev is None:
        if not unavailable:
            alert = _message(cfg, cfg.url, now, "First check: none of the 'currently unavailable' notices "
                             "is on the page. Either it is purchasable now or the notice is not visible to the monitor.")
    elif prev.get("unavailable") and not unavailable:
        alert = _message(cfg, cfg.url, now, "The 'currently unavailable' notice has disappeared from this page.")
    return WatchResult(cfg.name, Outcome.OK, alert=alert, new_state=state, detail=detail)


def _message(cfg: WatcherConfig, url: str, now: datetime, what: str) -> str:
    return (
        "🚨 PS5 PRO: POSSIBLY AVAILABLE (unverified heads-up)\n\n"
        f"Source: {cfg.name}\n"
        f"{what}\n"
        f"Last checked: {berlin_time(now)}\n\n"
        "This is an early signal from an official page. Price, stock and seller are NOT verified. Open it now:\n"
        f"{url}"
    )
