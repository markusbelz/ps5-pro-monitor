"""Amazon Germany: documented placeholder, intentionally NOT implemented."""

from .base import UnsupportedRetailer


class Amazon(UnsupportedRetailer):
    key = "amazon"
    display_name = "Amazon Germany"
    allowed_hosts = ("amazon.de",)
    default_sellers = ("Amazon", "Amazon.de")
    live_validated = False
    limitation = (
        "Not implemented: Amazon's conditions of use generally prohibit automated scraping and "
        "its pages use bot protection, which this project never circumvents. The permitted route "
        "is the Product Advertising API (needs an approved Associates account). Add an adapter "
        "for it if you have access."
    )
