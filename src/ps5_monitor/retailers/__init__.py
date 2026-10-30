"""Retailer registry. Add a new retailer by creating an adapter and listing it here."""

from .amazon import Amazon
from .base import Retailer
from .expert import Expert
from .mediamarkt import MediaMarkt
from .playstation_direct import PlayStationDirect
from .saturn import Saturn

# Order = priority order (PlayStation Direct first).
REGISTRY: dict[str, type[Retailer]] = {
    cls.key: cls for cls in (PlayStationDirect, MediaMarkt, Saturn, Amazon, Expert)
}

__all__ = ["REGISTRY", "Retailer"]
