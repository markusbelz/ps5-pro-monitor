"""MediaMarkt Germany. JSON-LD adapter; marketplace sellers are excluded by default."""

from .base import JsonLdRetailer


class MediaMarkt(JsonLdRetailer):
    key = "mediamarkt"
    display_name = "MediaMarkt Germany"
    allowed_hosts = ("mediamarkt.de",)
    default_sellers = ("MediaMarkt", "Media Markt", "MediaMarkt Online")
    live_validated = False
    limitation = (
        "Needs live validation: the site is protected against automated access; if it answers with "
        "403/captcha the check reports BLOCKED and nothing is bypassed. Marketplace seller names "
        "must be confirmed against real pages."
    )
