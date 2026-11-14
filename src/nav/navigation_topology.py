"""Conservative, bounded grid reachability shared with pose consumers.

This is a search constraint, never position evidence. Callers must supply a
fresh visual anchor and expire the scope when that anchor's continuity breaks.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.nav.grid_io import CELL_FREE
from src.nav.layered_map import LayeredMap, LayerKey, layer_key


@dataclass(frozen=True)
class _Floor:
    height: int
    width: int
    x0: float
    z0: float
    cell_size: float
    cells: bytes

    def index(self, x, z):
        return math.floor((z - self.z0) / self.cell_size), math.floor((x - self.x0) / self.cell_size)

    def world(self, i, j):
        return self.x0 + (j + 0.5) * self.cell_size, self.z0 + (i + 0.5) * self.cell_size

    def state(self, i, j):
        if 0 <= i < self.height and 0 <= j < self.width:
            return self.cells[i * self.width + j]
        return None


def _walkable_endpoint_cells(floor, rect):
    """Only explicitly free endpoint cells participate in a transition."""
    if floor is None:
        return ()
    j0 = max(0, math.floor((rect["x0"] - floor.x0) / floor.cell_size))
    j1 = min(floor.width, math.ceil((rect["x1"] - floor.x0) / floor.cell_size))
    i0 = max(0, math.floor((rect["z0"] - floor.z0) / floor.cell_size))
    i1 = min(floor.height, math.ceil((rect["z1"] - floor.z0) / floor.cell_size))
    return tuple((i, j) for i in range(i0, i1) for j in range(j0, j1) if floor.state(i, j) == CELL_FREE)


@dataclass(frozen=True)
class NavigationTopologyScope:
    """Immutable snapshot; unmodeled map coverage keeps its competitors."""

    _floors: Mapping[LayerKey, _Floor]
    _distances: Mapping[LayerKey, Mapping[tuple[int, int], float]]
    max_distance_m: float
    tolerance_m: float

    def allows_tier(self, map_id, tier_id):
        if tier_id is None:
            return True
        key = layer_key(map_id, tier_id)
        floor = self._floors.get(key)
        return floor is None or bool(self._distances.get(key))

    def allows_candidate(self, map_id, tier_id, x, z):
        if tier_id is None or not all(math.isfinite(v) for v in (x, z)):
            return True
        key = layer_key(map_id, tier_id)
        floor = self._floors.get(key)
        if floor is None:
            return True
        distances = self._distances.get(key)
        if not distances:
            return False
        i, j = floor.index(x, z)
        if floor.state(i, j) != CELL_FREE or (i, j) not in distances:
            return False
        # Account for localization/cell quantization without connecting islands
        # in the graph itself. The allowance is applied once to total distance.
        radius = math.ceil(self.tolerance_m / floor.cell_size) + 1
        limit = self.max_distance_m + self.tolerance_m
        for di in range(-radius, radius + 1):
            for dj in range(-radius, radius + 1):
                distance = distances.get((i + di, j + dj))
                if distance is None:
                    continue
                wx, wz = floor.world(i + di, j + dj)
                offset = math.hypot(wx - x, wz - z)
                if offset <= self.tolerance_m and distance + offset <= limit + 1e-9:
                    return True
        return False


class NavigationTopology:
    """Snapshot grid cells and explicit directed cross-floor links.

    Distances are physical horizontal meters, with no navigation risk penalties
    or link costs. Portals may move discontinuously and therefore cost zero.
    Missing intermediate data or an exhausted search budget disables filtering.
    Unknown cells are intentionally treated as non-walkable, including connector
    endpoints and visual candidates outside known grid coverage.
    """

    def __init__(self, layered: LayeredMap, *, tolerance_m=2.0, max_expand=20000):
        self.tolerance_m = float(tolerance_m)
        self.max_expand = int(max_expand)
        if not math.isfinite(self.tolerance_m) or self.tolerance_m < 0 or self.max_expand < 1:
            raise ValueError("invalid topology search limits")
        floors = {}
        for key, grid in layered.layers.items():
            floors[layer_key(*key)] = _Floor(
                *grid.shape,
                float(grid.meta.origin[0]),
                float(grid.meta.origin[2]),
                float(grid.meta.cell_size),
                grid.cells.astype("uint8", copy=False).tobytes(),
            )
        self._floors = MappingProxyType(floors)
        links = {}
        for original in layered.links:
            directions = (original, original.reversed) if original.bidirectional else (original,)
            for link in directions:
                source = self._floors.get(link.source.key)
                target = self._floors.get(link.target.key)
                if source is None:
                    continue
                targets = _walkable_endpoint_cells(target, link.target.rect)
                for cell in _walkable_endpoint_cells(source, link.source.rect):
                    links.setdefault((link.source.key, *cell), []).append(
                        (
                            link.target.key,
                            targets,
                            link.kind == "portal",
                            tuple(link.target.rect[k] for k in ("x0", "z0", "x1", "z1")),
                        )
                    )
        self._links = {key: tuple(value) for key, value in links.items()}

    def scope(self, map_id, tier_id, world, max_distance_m):
        """Return complete bounded reachability, or ``None`` if it is unsafe."""
        if tier_id is None:
            return None
        try:
            x, z = map(float, world)
            budget = float(max_distance_m)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in (x, z, budget)) or budget < 0:
            return None
        key = layer_key(map_id, tier_id)
        floor = self._floors.get(key)
        if floor is None:
            return None
        i, j = floor.index(x, z)
        if floor.state(i, j) != CELL_FREE:
            return None
        wx, wz = floor.world(i, j)
        start = (key, i, j)
        distance = math.hypot(wx - x, wz - z)
        limit = budget + self.tolerance_m
        distances = {start: distance}
        queue = [(distance, start)]
        expanded = operations = 0
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node] or distance > limit:
                continue
            expanded += 1
            if expanded > self.max_expand:
                return None
            key, i, j = node
            floor = self._floors[key]
            edges = []
            for di, dj in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                if floor.state(i + di, j + dj) != CELL_FREE:
                    continue
                if di and dj and (floor.state(i + di, j) != CELL_FREE or floor.state(i, j + dj) != CELL_FREE):
                    continue
                edges.append(((key, i + di, j + dj), math.hypot(di, dj) * floor.cell_size))
            wx, wz = floor.world(i, j)
            for target_key, target_cells, portal, rect in self._links.get(node, ()):
                if not target_cells:
                    if target_key in self._floors:
                        # Present data marks the entrance blocked/unknown;
                        # this is not a missing resource that needs fallback.
                        continue
                    dx, dz = max(rect[0] - wx, 0, wx - rect[2]), max(rect[1] - wz, 0, wz - rect[3])
                    if distance + (0 if portal else math.hypot(dx, dz)) <= limit:
                        return None
                    continue
                target_floor = self._floors[target_key]
                for ti, tj in target_cells:
                    tx, tz = target_floor.world(ti, tj)
                    edges.append(((target_key, ti, tj), 0.0 if portal else math.hypot(tx - wx, tz - wz)))
                    if len(edges) > self.max_expand * 16:
                        return None
            operations += len(edges)
            if operations > self.max_expand * 16:
                return None
            for target, cost in edges:
                proposed = distance + cost
                if proposed <= limit and proposed < distances.get(target, math.inf):
                    distances[target] = proposed
                    heapq.heappush(queue, (proposed, target))
        by_floor = {}
        for (key, i, j), distance in distances.items():
            if distance <= limit:
                by_floor.setdefault(key, {})[(i, j)] = distance
        return NavigationTopologyScope(
            self._floors,
            MappingProxyType({key: MappingProxyType(value) for key, value in by_floor.items()}),
            budget,
            self.tolerance_m,
        )
