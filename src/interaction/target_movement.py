"""Pure camera movement calculations shared by HUD and world targets."""

import math


def calc_direction_step(from_pos, to_pos, max_step=100, min_step=5, slow_radius=200, deadzone=8):
    dx_raw = to_pos[0] - from_pos[0]
    dy_raw = to_pos[1] - from_pos[1]
    dist = math.hypot(dx_raw, dy_raw)
    if dist <= deadzone:
        return 0, 0
    step = min_step + (max_step - min_step) * (1 - math.exp(-dist / slow_radius))
    step = min(step, max_step)
    return round(dx_raw / dist * step), round(dy_raw / dist * step)


def ellipse_geometry(center, scale, radius_x=720, radius_y=405):
    return *center, max(1, round(radius_x * scale)), max(1, round(radius_y * scale))


def ellipse_radius(x, y, geometry):
    cx, cy, rx, ry = geometry
    return math.hypot((x - cx) / rx, (y - cy) / ry)


def ellipse_move_profile(
    radius, *, max_step=120, min_step=20, slow_radius=350, deadzone=8, inner_radius=0.5, outer_boost=1.75
):
    progress = min(1.0, max(0.0, (radius - inner_radius) / (1.0 - inner_radius)))
    boost = 1.0 + (outer_boost - 1.0) * progress
    return {
        "max_step": round(max_step * boost),
        "min_step": round(min_step * boost),
        "slow_radius": slow_radius,
        "deadzone": deadzone,
    }


def hud_direction_step(target_center, geometry, width, *, reference_radius_x=720, **profile_options):
    """Normalize ellipse axes, then scale raw input by capture width on both axes."""
    cx, cy, rx, ry = geometry
    nx, ny = (target_center[0] - cx) / rx, (target_center[1] - cy) / ry
    profile = ellipse_move_profile(math.hypot(nx, ny), **profile_options)
    width_scale = width / 1920
    radius = reference_radius_x * width_scale
    return calc_direction_step(
        (0, 0),
        (nx * radius, ny * radius),
        **{key: max(1, round(value * width_scale)) for key, value in profile.items()},
    )


def resolve_move_profile(profile, *, ocr, use_yolo):
    if profile not in (None, "hud_direction", "world_projection"):
        raise ValueError(f"Unknown navigation movement profile: {profile}")
    # A world projection never acquires HUD geometry through a default/override.
    if not ocr and use_yolo:
        return "world_projection"
    return profile or ("world_projection" if ocr else "hud_direction")
