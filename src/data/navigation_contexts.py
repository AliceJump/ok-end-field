"""Explicit business meanings for route families; distance equality proves no origin."""

from src.data.delivery_area import DELIVERY_AREA_CONFIG

DELIVERY_DISPATCH_BOARD = "delivery_dispatch_board"
DELIVERY_TRANSFER_BOARD = "delivery_transfer_board"


def delivery_route_family(config_key, *, origin_anchor):
    """Require a caller-verified physical entry, using canonical (never OCR) names.

    NPC destinations from one dispatch board may share prefixes. Transfer-to-pickup
    routes have a different origin/direction. Unknown entries, ambiguous names and
    gather stages remain private until their physical anchors are documented.
    """
    matches = []
    for area, data in DELIVERY_AREA_CONFIG.items():
        for location, targets in data["delivery_targets_by_location"].items():
            target_names = {target if isinstance(target, str) else target.get("name") for target in targets}
            if config_key in target_names and origin_anchor == DELIVERY_DISPATCH_BOARD:
                matches.append((area, location, origin_anchor, "outbound", "delivery"))
            if config_key == f"通向{location}送货点" and origin_anchor == DELIVERY_TRANSFER_BOARD:
                matches.append((area, location, origin_anchor, "pickup", "delivery"))
    return matches[0] if len(matches) == 1 else ()
