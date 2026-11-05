"""Feedback measured on a presented position frame, in the current walking leg."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from src.nav.coasting import WALK_OCTANTS, can_coast
from src.nav.grid_io import CELL_FREE


class MeasuredTrail:
    """A recent physical path, including traversed positions outside the mapped road."""

    def __init__(self):
        self.points = deque(maxlen=32)
        self.area = None
        self.last_frame = None

    def record(self, area, position, stamp, cell_size):
        if self.area != area or self.last_frame is None or stamp - self.last_frame > 2.5:
            self.points.clear()
        elif stamp <= self.last_frame:
            return
        if self.points and math.dist(self.points[-1], position) > 2 + 20 * (stamp - self.last_frame):
            self.points.clear()
        if not self.points or math.dist(self.points[-1], position) >= 0.35 * cell_size:
            self.points.append(position)
        self.area, self.last_frame = area, stamp

    def path_to_known(self, grid):
        """Find the last known cell within retained history; lateral tolerance is unrelated to path length."""
        path = []
        for point in reversed(self.points):
            path.append(point)
            if grid.state(*grid.index_of_world(*point)) == CELL_FREE:
                return path
        return []

    def return_path(self, grid, position):
        path = self.path_to_known(grid)
        if path:
            return path
        # A relock may leave only the last part of the physical approach.
        # It still proves a way back to those measured positions, without
        # declaring their grid cells free.
        partial = list(reversed(self.points))
        if partial and math.dist(partial[-1], position) >= 0.65 * grid.meta.cell_size:
            return partial
        return []


def boundary_probe(planner, pos, heading, episode, distance, visited):
    """Probe only out of the occupied rejected cell into an adjacent known cell."""
    grid = planner.grid
    i, j = grid.index_of_world(*pos)
    candidates = []
    for di, dj in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
        cell = i + di, j + dj
        if grid.state(*cell) != CELL_FREE or not planner.passable(*cell):
            continue
        point = grid.world_of_index(*cell)
        for offset, keys in WALK_OCTANTS:
            rad = math.radians(heading + offset)
            direction = math.sin(rad), math.cos(rad)
            hit = _boundary_ray(grid, pos, point, direction, distance, (i, j))
            if hit is None:
                continue
            metres, end = hit
            along = sum(a * b for a, b in zip(episode.exit_direction or (0, 0), direction, strict=True))
            rank = (
                cell in episode.tried_exits,
                cell not in visited,
                -along,
                math.dist(pos, point),
                math.dist(end, point),
            )
            candidates.append((rank, cell, (keys, metres)))
    if not candidates:
        return None
    _, cell, (keys, metres) = min(candidates)
    episode.tried_exits.add(cell)
    return keys, metres


class ForwardInput:
    """Intersect issued W intervals with the time between two position frames."""

    def __init__(self):
        self.start = None
        self.intervals = deque()

    def edge(self, held, now):
        if held:
            self.start = now
        elif self.start is not None:
            self.intervals.append((self.start, now))
            self.start = None

    def seconds(self, since, until):
        while self.intervals and self.intervals[0][1] <= since:
            self.intervals.popleft()
        total = sum(max(0.0, min(until, b) - max(since, a)) for a, b in self.intervals)
        if self.start is not None:
            total += max(0.0, until - max(since, self.start))
        return min(total, max(0.0, until - since))


@dataclass
class LegObservation:
    start: tuple
    end: tuple
    position: tuple
    unit: tuple
    along: float
    side: float
    advance: float = 0.0
    lateral_change: float = 0.0

    @classmethod
    def measure(cls, start, end, position):
        length = math.dist(start, end)
        if length <= 1e-6:
            return None
        ux, uz = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        dx, dz = position[0] - start[0], position[1] - start[1]
        return cls(start, end, position, (ux, uz), dx * ux + dz * uz, dx * uz - dz * ux)


class WalkFeedback:
    def __init__(self):
        self.leg = None
        self.frame_time = None
        self.anchor = 0.0
        self.exposure = 0.0
        self.command = None
        self.short_start = None
        self.short_misses = 0

    def _record_progress(self, sample, previous, frame_time, input_seconds, min_progress):
        sample.advance = sample.along - previous.along
        sample.lateral_change = abs(sample.side) - abs(previous.side)
        if math.dist(sample.position, previous.position) >= min_progress:
            self.short_misses = 0
        if self.short_start is not None and frame_time > self.short_start[1]:
            # The read after key-up is the first opportunity to judge this
            # distinct start from rest. A stationary turn probe is excluded.
            if math.dist(self.short_start[0], sample.position) < min_progress:
                self.short_misses += 1
            else:
                self.short_misses = 0
            self.short_start = None
        if sample.along >= self.anchor + min_progress:
            self.anchor = sample.along
            self.exposure = 0.0
            self.short_misses = 0
        else:
            self.exposure += input_seconds

    def observe(self, sample, frame_time, input_seconds, min_progress, window):
        if self.frame_time is not None and frame_time <= self.frame_time:
            return
        previous = self.leg
        if (
            previous is None
            or sample is None
            or (previous.start, previous.end) != (sample.start, sample.end)
            or frame_time - self.frame_time > window
        ):
            self.exposure = 0.0
            self.anchor = sample.along if sample else 0.0
            self.command = None
            self.short_misses = 0
            self.short_start = None
        else:
            self._record_progress(sample, previous, frame_time, input_seconds, min_progress)
        self.leg, self.frame_time = sample, frame_time


@dataclass
class RecoveryEpisode:
    map_id: str
    tier: str | None
    origin: tuple
    unit: tuple
    attempts: int = 1
    after: float = 0.0
    confirmed_frames: int = 0
    last_frame: float = 0.0
    short_approach: bool = False
    tried_exits: set = field(default_factory=set)
    return_path: tuple = ()
    departure: tuple | None = None
    exit_direction: tuple | None = None

    @property
    def avoid_approach(self):
        return self.short_approach or self.attempts >= 2 or self.departure is not None

    def matches(self, map_id, tier, position, unit):
        return (
            self.map_id == map_id
            and self.tier == tier
            and math.dist(position, self.origin) <= 6.0
            and self.unit[0] * unit[0] + self.unit[1] * unit[1] > 0.5
        )

    def choose_keys(self, planner, leg, heading, metres, visited):
        """Prefer a verified new launch point for ineffective corner starts."""
        pos, target = leg.position, leg.end
        moves = [(180, ("s",)), (135, ("s", "d")), (-135, ("s", "a"))]
        if self.short_approach:
            moves += [(90, ("d",)), (-90, ("a",))]
        candidates = []
        for offset, keys in moves:
            rad = math.radians(heading + offset)
            end = pos[0] + metres * math.sin(rad), pos[1] + metres * math.cos(rad)
            if not can_coast(planner, pos, heading + offset, [pos, end], metres, planner.grid.meta.cell_size):
                continue
            sidestep = abs(offset) == 90
            if sidestep:
                remaining = math.dist(end, target)
                bearing = math.degrees(math.atan2(target[0] - end[0], target[1] - end[1]))
                if remaining <= 0 or not can_coast(
                    planner, end, bearing, [end, target], remaining, planner.grid.meta.cell_size
                ):
                    continue
                error = remaining
            else:
                error = abs(leg.side + (end[0] - pos[0]) * self.unit[1] - (end[1] - pos[1]) * self.unit[0])
            prefer_straight = (len(keys) == 1) == (self.attempts == 1)
            known = planner.grid.index_of_world(*end) in visited
            candidates.append(((sidestep, prefer_straight, known, -error), keys))
        return max(candidates, key=lambda pair: pair[0])[1] if candidates else None

    def passed(self, position, frame_time):
        if frame_time <= max(self.last_frame, self.after):
            return False
        dx, dz = position[0] - self.origin[0], position[1] - self.origin[1]
        if frame_time - self.last_frame > 1.5:
            self.confirmed_frames = 0
        self.last_frame = frame_time
        self.confirmed_frames = self.confirmed_frames + 1 if dx * self.unit[0] + dz * self.unit[1] > 2 else 0
        return self.confirmed_frames >= 2


def _boundary_ray(grid, pos, point, direction, distance, origin_cell):
    entry, exit_at = 0.0, distance
    for delta, component in zip((point[0] - pos[0], point[1] - pos[1]), direction, strict=True):
        half = 0.45 * grid.meta.cell_size
        if abs(component) < 1e-8:
            if abs(delta) > half:
                exit_at = -1.0
                break
            continue
        near, far = sorted(((delta - half) / component, (delta + half) / component))
        entry, exit_at = max(entry, near), min(exit_at, far)
    if exit_at < entry:
        return None
    centre = (point[0] - pos[0]) * direction[0] + (point[1] - pos[1]) * direction[1]
    metres = min(exit_at, max(entry, centre))
    end = pos[0] + metres * direction[0], pos[1] + metres * direction[1]
    steps = math.ceil(metres / (0.2 * grid.meta.cell_size))
    crossed = (
        grid.index_of_world(pos[0] + metres * direction[0] * k / steps, pos[1] + metres * direction[1] * k / steps)
        for k in range(1, steps + 1)
    )
    if any(c != origin_cell and grid.state(*c) != CELL_FREE for c in crossed):
        return None

    return metres, end
