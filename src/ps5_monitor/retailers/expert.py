"""Expert Germany. Seller identity is NOT assumed: you must list trusted sellers explicitly."""

from .base import JsonLdRetailer


class Expert(JsonLdRetailer):
    key = "expert"
    display_name = "Expert Germany"
    allowed_hosts = ("expert.de",)
    # Deliberately empty: Expert is a cooperative of independent stores. Without an explicit
    # `trusted_sellers` entry in the config every Expert offer stays UNVERIFIED (no alert).
    default_sellers = ()
    live_validated = False
    limitation = (
        "Needs live validation. Seller identity and delivery to Germany cannot be established "
        "automatically, so offers are UNVERIFIED unless you add the exact seller name to "
        "`trusted_sellers` and (if shipping is not on the page) `shipping_eur`."
    )
