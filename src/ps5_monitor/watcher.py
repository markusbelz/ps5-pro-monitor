"""Page watcher for official pages.

Idea: while the PS5 Pro is not purchasable, Sony's pages show an 'unavailable' notice. The watcher
remembers whether such a notice is present and raises an early heads-up when it disappears.
This is NOT a verified offer (price/stock/seller are not checked); the message says so.
"""

from __future__ import annotations

import json
import logging
import re
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


_HINT = re.compile(
    r"niet beschikbaar|niet op voorraad|uitverkocht|voorraad|binnenkort|beschikbaar|ikke på lager|udsolgt|"
    r"ikke tilgængelig|tilgængelig|nicht (?:zum kauf )?(?:erhältlich|verfügbar|vorrätig)|vorrätig|ausverkauft|"
    r"indisponible|rupture|épuisé|non disponibile|esaurito|no disponible|agotado|indispon[ií]vel|esgotado|n[ãa]o dispon[ií]vel|"
    r"not available|unavailable|out of stock|sold out|coming soon"
)


def hint_snippets(text: str, limit: int = 8) -> list[str]:
    """Short excerpts around availability-related words, to help choosing phrases for a new page."""
    found: dict[str, str] = {}
    for m in _HINT.finditer(text):
        found.setdefault(m.group(0), text[max(0, m.start() - 35): m.end() + 35])
        if len(found) >= limit:
            break
    return list(found.values())


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
    detail = (
        f"unavailable-notice={'yes' if unavailable else 'NO'} phrases={present} page={final_short} chars={len(text)}"
        f" hints={hint_snippets(text)}"
    )

    alert = None
    if prev is None:
        if not unavailable and cfg.alert_on_first_check:
            alert = _message(cfg, cfg.url, now, "First check: none of the 'currently unavailable' notices "
                             "is on the page. Either it is purchasable now or the notice is not visible to the monitor.")
    elif prev.get("unavailable") and not unavailable:
        alert = _message(cfg, cfg.url, now, "The 'currently unavailable' notice has disappeared from this page.")
    return WatchResult(cfg.name, Outcome.OK, alert=alert, new_state=state, detail=detail)


def _message(cfg: WatcherConfig, url: str, now: datetime, what: str) -> str:
    note = f"\nHinweis: {cfg.note}\n" if cfg.note else ""
    return (
        "🚨 PS5 PRO: POSSIBLY AVAILABLE (unverified heads-up)\n\n"
        f"Source: {cfg.name}\n"
        f"{what}\n"
        f"Last checked: {berlin_time(now)}\n"
        f"{note}\n"
        "This is an early signal from an official page. Price, stock and seller are NOT verified. Open it now:\n"
        f"{url}"
    )
