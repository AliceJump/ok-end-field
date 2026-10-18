"""Only known delivery entries attach initial learning to the first terminal-board align."""

from src.core.learning.models import CaptureProfile, WorkflowContext
from src.core.learning.policy import learning_allowed
from src.core.sequence_parser import parse_int_sequence
from src.data.navigation_contexts import delivery_route_family


def delivery_learning_kwargs(task, config_key, origin_anchor, *, phase=None, distances=None):
    """Never infer an unknown current location from the requested destination."""
    if not learning_allowed():
        return {}
    family = delivery_route_family(config_key, origin_anchor=origin_anchor)
    state = vars(task)
    if not family or family[:2] != (state.get("delivery_area"), state.get("_accepted_delivery_location")):
        return {}
    kwargs = {"route_family": family}
    if phase is None:
        return kwargs
    if phase not in ("pickup_initial", "dropoff_initial"):
        raise ValueError("Unknown delivery initial-alignment phase")
    try:
        signature = (
            tuple(distances)
            if distances is not None
            else tuple(parse_int_sequence(task.get_zip_line_config_value(config_key)))
        )
        if not signature:
            return kwargs
        profile = CaptureProfile(
            task.width,
            task.height,
            f"delivery-reboard-v-track:zip-scroll-{bool(task.zip_line_scroll_enabled())}",
            "hud-initial-v1",
        )
        kwargs["target_learning_context"] = WorkflowContext(
            "delivery",
            phase,
            family[0],
            family[1],
            "after_zipline_reboard",
            f"delivery-terminal:{config_key}",
            f"delivery-pickup:{family[1]}" if phase == "pickup_initial" else f"delivery-npc:{config_key}",
            profile,
            signature,
        )
    except (AttributeError, TypeError, ValueError, OverflowError):
        # Missing runtime capture/route identity permits ordinary alignment only.
        pass
    return kwargs
