"""Saturn Germany (same group/platform as MediaMarkt). JSON-LD adapter."""

from .base import JsonLdRetailer


class Saturn(JsonLdRetailer):
    key = "saturn"
    display_name = "Saturn Germany"
    allowed_hosts = ("saturn.de",)
    default_sellers = ("Saturn", "Saturn Online")
    live_validated = False
    limitation = (
        "Needs live validation: same bot-protection caveats as MediaMarkt. Marketplace seller "
        "names must be confirmed against real pages."
    )
