"""PlayStation Direct Germany (Sony). Reads schema.org JSON-LD from the configured product page."""

from .base import JsonLdRetailer


class PlayStationDirect(JsonLdRetailer):
    key = "playstation_direct"
    display_name = "PlayStation Direct Germany"
    allowed_hosts = ("direct.playstation.com",)
    default_sellers = (
        "Sony",
        "Sony Interactive Entertainment",
        "Sony Interactive Entertainment Europe",
        "PlayStation",
        "PlayStation Direct",
    )
    live_validated = False
    limitation = (
        "Needs live validation: the page may render stock client-side or sit behind a queue/bot "
        "protection. If no JSON-LD is served the check reports NO_DATA/BLOCKED, never IN_STOCK."
    )
