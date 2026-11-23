"""Coolblue Germany (coolblue.de ships to Germany itself). JSON-LD adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..models import Offer
from .base import JsonLdRetailer


class Coolblue(JsonLdRetailer):
    key = "coolblue"
    display_name = "Coolblue Germany"
    allowed_hosts = ("coolblue.de",)
    default_sellers = ("Coolblue",)
    live_validated = True  # price + stock read from a real GitHub-runner dry-run on 2026-11-23
    limitation = (
        "The page carries price and stock in JSON-LD but names no seller. An offer with no seller is "
        "treated as sold by Coolblue itself (the configured URL is Coolblue's own product page); "
        "an explicitly named other seller is still rejected."
    )

    def parse(self, html: str, now: datetime) -> list[Offer]:
        return [replace(o, seller="Coolblue") if not (o.seller or "").strip() else o for o in super().parse(html, now)]
