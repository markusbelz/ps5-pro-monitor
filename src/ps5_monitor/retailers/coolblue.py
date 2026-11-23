"""Coolblue Germany (coolblue.de ships to Germany itself). JSON-LD adapter."""

from .base import JsonLdRetailer


class Coolblue(JsonLdRetailer):
    key = "coolblue"
    display_name = "Coolblue Germany"
    allowed_hosts = ("coolblue.de",)
    default_sellers = ("Coolblue",)
    live_validated = False
    limitation = (
        "Needs live validation: it is unknown whether the page exposes schema.org JSON-LD. "
        "If not, the dry-run shows NO_DATA and no alert is ever sent."
    )
