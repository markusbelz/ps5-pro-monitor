"""Galaxus Germany. Only offers sold by Galaxus itself qualify, marketplace sellers never do."""

from .base import JsonLdRetailer


class Galaxus(JsonLdRetailer):
    key = "galaxus"
    display_name = "Galaxus Germany"
    allowed_hosts = ("galaxus.de",)
    default_sellers = ("Galaxus",)
    live_validated = False
    limitation = (
        "Needs live validation: the page seems to carry price/availability as meta tags, not JSON-LD, "
        "and often lists marketplace sellers. Without JSON-LD the dry-run shows NO_DATA and nothing is sent."
    )
