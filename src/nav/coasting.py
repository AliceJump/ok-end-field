"""Check whether holding the current heading fits a prefix of the planned route."""

from __future__ import annotations

import math
from itertools import pairwise

from src.nav.grid_io import CELL_UNKNOWN

WALK_OCTANTS = (
    (0, ("w",)),
    (45, ("w", "d")),
    (90, ("d",)),
    (135, ("s", "d")),
    (180, ("s",)),
    (-135, ("s", "a")),
    (-90, ("a",)),
    (-45, ("w", "a")),
)


def route_prefix(position, waypoints, index, limit, horizon):
    """Clip the current continuous walking section at its arc-length horizon."""
    prefix = [position]
    remaining = horizon
    for i in range(index, limit + 1):
        last = prefix[-1]
        point = waypoints[i]
        length = math.dist(last, point)
        if length <= 1e-8:
            continue
        if length >= remaining:
            ratio = remaining / length
            prefix.append((last[0] + ratio * (point[0] - last[0]), last[1] + ratio * (point[1] - last[1])))
            return prefix
        prefix.append(point)
        remaining -= length
    return None


def _line_inside_corridor(position, end, prefix, width, stride):
    check_count = max(1, math.ceil(math.dist(position, end) / stride))
    segments = []
    for a, b in pairwise(prefix):
        dx, dz = b[0] - a[0], b[1] - a[1]
        segments.append((a[0], a[1], dx, dz, dx * dx + dz * dz))
    for i in range(1, check_count + 1):
        ratio = i / check_count
        x = position[0] + (end[0] - position[0]) * ratio
        z = position[1] + (end[1] - position[1]) * ratio
        for ax, az, dx, dz, sq in segments:
            along = min(1.0, max(0.0, ((x - ax) * dx + (z - az) * dz) / sq))
            if math.hypot(x - ax - along * dx, z - az - along * dz) <= width:
                break
        else:
            return False
    return True


def retrace_step(position, heading, path, cell_size, max_distance):
    """Choose a short octant input that stays within the observed return path."""
    points = [position]
    for point in path:
        if math.dist(point, points[-1]) > 1e-5:
            points.append(point)
    length = sum(math.dist(a, b) for a, b in pairwise(points))
    if length <= 1e-5:
        return None
    distance = min(max_distance, length)
    for span in (distance, min(distance, cell_size)):
        prefix = route_prefix(position, points, 1, len(points) - 1, span) or points
        candidates = []
        for offset, keys in WALK_OCTANTS:
            rad = math.radians(heading + offset)
            end = position[0] + span * math.sin(rad), position[1] + span * math.cos(rad)
            error = math.dist(end, prefix[-1])
            if error <= 0.5 * cell_size and _line_inside_corridor(
                position, end, prefix, 0.5 * cell_size, 0.25 * cell_size
            ):
                candidates.append((error, keys))
        if candidates:
            return min(candidates)[1], span
    return None


def can_coast(planner, position, heading, prefix, horizon, width):
    """Test forward progress, route deviation, collision and unknown-cell exposure."""
    if prefix is None or width <= 0:
        return False
    radians = math.radians(heading)
    dx, dz = math.sin(radians), math.cos(radians)
    end = position[0] + horizon * dx, position[1] + horizon * dz
    # Both routes have equal travel length. Ending close to the prefix endpoint
    # also excludes crossing a return bend or rejoining a later parallel road.
    if math.dist(end, prefix[-1]) > width:
        return False
    grid = planner.grid
    if not _line_inside_corridor(position, end, prefix, width, min(0.5, grid.meta.cell_size)):
        return False
    original_unknown, original_cost_cells = _prefix_exposure(planner, prefix)
    for offset in (0.0, -0.5 * grid.meta.cell_size, 0.5 * grid.meta.cell_size):
        a = grid.index_of_world(position[0] + offset * dz, position[1] - offset * dx)
        b = grid.index_of_world(end[0] + offset * dz, end[1] - offset * dx)
        if not planner.passable(*a) or not planner.line_clear(a, b):
            return False
        if not _ray_exposure_allowed(planner, a, b, original_unknown, original_cost_cells):
            return False
    return True


def _ray_exposure_allowed(planner, a, b, original_unknown, original_cost_cells):
    for cell in planner.line_cells(a, b):
        if planner.recovery_cost and cell in planner.recovery_cost and cell not in original_cost_cells:
            return False
        if planner.grid.state(*cell) == CELL_UNKNOWN and cell not in original_unknown:
            return False
    return True


def steer_through(
    planner, position, heading, prefix, horizon, width, off_route_radius, gain, max_angle, max_start, *, target=None
):
    """Choose a visible aim point only if moving rotation has a clear swept sector."""
    if prefix is None or width <= 0 or off_route_radius <= 0:
        return None
    end = prefix[-1]
    if target is None:
        target = math.degrees(math.atan2(end[0] - position[0], end[1] - position[1])) % 360
    error = (target - heading + 180) % 360 - 180
    if abs(error) > max_start:
        return None
    command = max(-max_angle, min(max_angle, error * gain))
    if not can_coast(planner, position, heading + command, prefix, horizon, width):
        return None
    # A slow actuator can initially keep the old direction. Bound the whole
    # intervening motion by a sector, using the outer cell circumradius as
    # well as the character's half-cell margin in the occupancy check.
    grid = planner.grid
    radius = grid.meta.cell_size
    margin = radius * (0.5 + math.sqrt(0.5))
    half = math.radians(abs(error) * 0.5)
    mid = math.radians(heading + error * 0.5)
    dx, dz = math.sin(mid), math.cos(mid)
    spread = math.tan(half)
    unknown = set()
    for a, b in pairwise(prefix):
        unknown.update(
            cell
            for cell in planner.line_cells(grid.index_of_world(*a), grid.index_of_world(*b))
            if grid.state(*cell) == CELL_UNKNOWN
        )
    if not _sector_clear(planner, position, horizon, margin, half, dx, dz, spread, unknown):
        return None
    rad = math.radians(heading)
    straight_end = (position[0] + horizon * math.sin(rad), position[1] + horizon * math.cos(rad))
    if not _line_inside_corridor(position, straight_end, prefix, off_route_radius, min(0.5, radius)):
        return None
    return target


def _prefix_exposure(planner, prefix):
    grid = planner.grid
    original_unknown = set()
    original_cost_cells = set()
    for a, b in pairwise(prefix):
        cells = planner.line_cells(grid.index_of_world(*a), grid.index_of_world(*b))
        original_unknown.update(cell for cell in cells if grid.state(*cell) == CELL_UNKNOWN)
        if planner.recovery_cost:
            original_cost_cells.update(cell for cell in cells if cell in planner.recovery_cost)
    return original_unknown, original_cost_cells


def _sector_clear(planner, position, horizon, margin, half, dx, dz, spread, unknown):
    grid = planner.grid
    i0, j0 = grid.index_of_world(position[0] - horizon - margin, position[1] - horizon - margin)
    i1, j1 = grid.index_of_world(position[0] + horizon + margin, position[1] + horizon + margin)
    for i in range(min(i0, i1), max(i0, i1) + 1):
        for j in range(min(j0, j1), max(j0, j1) + 1):
            x, z = grid.world_of_index(i, j)
            x, z = x - position[0], z - position[1]
            along = x * dx + z * dz
            if (
                along < -margin
                or abs(x * dz - z * dx) > along * spread + margin / math.cos(half)
                or x * x + z * z > (horizon + margin) ** 2
            ):
                continue
            if not planner.passable(i, j) or (grid.state(i, j) == CELL_UNKNOWN and (i, j) not in unknown):
                return False
    return True
