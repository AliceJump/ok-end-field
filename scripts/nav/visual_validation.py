"""网格覆盖与抓包时间对齐的纯计算工具，不发送游戏输入。"""

import math
from bisect import bisect_left, bisect_right
from collections import deque

import cv2
import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, DenseGrid


def duplicate_observation(row):
    """Use consumer-local identity when available, retaining compatibility with older logs."""
    return row.get("validation_duplicate", row["pose"].get("visual_duplicate_frame", False))


def navigation_pose_ready(pose):
    return bool(
        pose and pose.get("position_trusted") and (not pose.get("tier_evidence_required") or pose.get("tier_trusted"))
    )


class NavigationSourcePolicy:
    def __init__(self, enabled):
        self.enabled, self.capture, self.good, self.bad = enabled, False, 0, 0
        self._last_frame = None

    def choose(self, pose):
        ready = navigation_pose_ready(pose)
        stamp = (pose or {}).get("frame_t")
        if isinstance(stamp, (int, float)) and math.isfinite(stamp) and stamp > 0:
            if ready and self._last_frame is not None and stamp <= self._last_frame:
                return "capture" if self.capture else "visual_map"
            self._last_frame = stamp
        self.good = self.good + 1 if ready else 0
        self.bad = self.bad + 1 if not ready else 0
        if self.enabled and self.bad >= 3:
            self.capture = True
        if self.good >= 3:
            self.capture = False
        return "capture" if self.capture else "visual_map"


def temporary_obstacle_grid(grid, failures):
    cells = grid.cells.copy()
    for failure in failures:
        position, waypoint = failure.get("position"), failure.get("waypoint") or failure.get("goal")
        if not position or not waypoint or not failure.get("obstructed", False):
            continue
        distance = math.dist(position, waypoint)
        if distance < 0.5:
            continue
        center = tuple(a + (b - a) * min(2.5, distance) / distance for a, b in zip(position, waypoint, strict=True))
        row, col = grid.index_of_world(*center)
        radius = math.ceil(1.5 / grid.meta.cell_size)
        for i in range(max(0, row - radius), min(cells.shape[0], row + radius + 1)):
            for j in range(max(0, col - radius), min(cells.shape[1], col + radius + 1)):
                point = grid.world_of_index(i, j)
                if math.dist(point, center) <= 1.5 and math.dist(point, position) > 1.0:
                    cells[i, j] = CELL_BLOCKED
    return DenseGrid(cells, grid.meta)


def connected_region(grid, position):
    cell = grid.index_of_world(*position)
    if not grid.is_free(*cell):
        raise ValueError(f"start_not_on_free_grid: cell={cell}")
    _, labels = cv2.connectedComponents((grid.cells == CELL_FREE).astype(np.uint8), connectivity=4)
    region = labels == labels[cell]
    return region, int(np.count_nonzero(grid.cells == CELL_FREE) - region.sum())


def nearby_cells(region, start, steps):
    if not (0 <= start[0] < region.shape[0] and 0 <= start[1] < region.shape[1]) or not region[start]:
        return set()
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        (i, j), depth = queue.popleft()
        if depth >= steps:
            continue
        for point in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
            y, x = point
            if 0 <= y < region.shape[0] and 0 <= x < region.shape[1] and region[point] and point not in seen:
                seen.add(point)
                queue.append((point, depth + 1))
    return seen


def coverage_targets(grid, region, spacing=5.0):
    if not math.isfinite(spacing) or spacing < grid.meta.cell_size:
        raise ValueError("spacing must be finite and at least one grid cell")
    stride = max(1, round(spacing / grid.meta.cell_size))
    rows, columns = np.where(region)
    targets = []
    clearance = cv2.distanceTransform(region.astype(np.uint8), cv2.DIST_L2, 3)
    for i in range(int(rows.min()), int(rows.max()) + 1, stride):
        for j in range(int(columns.min()), int(columns.max()) + 1, stride):
            tile = region[i : i + stride, j : j + stride]
            count, labels = cv2.connectedComponents(tile.astype(np.uint8), connectivity=4)
            for label in range(1, count):
                yy, xx = np.where(labels == label)
                rank = clearance[yy + i, xx + j] - 0.01 * ((yy - stride / 2) ** 2 + (xx - stride / 2) ** 2)
                chosen = int(np.argmax(rank))
                targets.append((int(yy[chosen] + i), int(xx[chosen] + j)))
    covered = set()
    steps = math.floor(spacing / grid.meta.cell_size)
    for point in targets:
        covered.update(nearby_cells(region, point, steps))
    for y, x in zip(rows, columns, strict=True):
        point = (int(y), int(x))
        if point not in covered:
            targets.append(point)
            covered.update(nearby_cells(region, point, steps))
    return targets


def valid_reference(record):
    try:
        values = [float(record[key]) for key in ("time", "x", "z")]
        return all(math.isfinite(v) for v in values) and values[0] > 0 and not record.get("replayed")
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def reference_map_id(record):
    """Keep raw packet identity separate from the production resolver's result."""
    return record.get("resolved_map_id", record.get("mapId"))


class ReferenceTimeline:
    def __init__(self, records):
        self.samples, self.times = [], []
        self.revision = 0
        self.extend(records)

    def extend(self, records):
        """Validate only new records; retain stable ordering for late arrivals."""
        incoming = sorted(
            ({**r, **{key: float(r[key]) for key in ("time", "x", "z")}} for r in records if valid_reference(r)),
            key=lambda r: r["time"],
        )
        if not incoming:
            return
        if not self.times or float(incoming[0]["time"]) >= self.times[-1]:
            self.samples.extend(incoming)
            self.times.extend(float(row["time"]) for row in incoming)
            return
        self.revision += 1
        for row in incoming:
            stamp = float(row["time"])
            index = bisect_right(self.times, stamp)
            self.times.insert(index, stamp)
            self.samples.insert(index, row)


def align_reference(records, stamp, map_id, *, flow=None, max_gap=0.6, stationary=False):
    timeline = records if isinstance(records, ReferenceTimeline) else ReferenceTimeline(records)
    samples, times = timeline.samples, timeline.times
    if not samples:
        return None
    index = bisect_left(times, stamp)
    left = samples[max(0, index - 1)]
    right = samples[min(index, len(samples) - 1)]
    if index < len(samples) and abs(times[index] - stamp) < 1e-6:
        left = right
    pair = (left, right)
    if any(reference_map_id(r) != map_id or (flow is not None and r.get("flow") != flow) for r in pair):
        return None
    if left.get("flow") != right.get("flow") or left.get("scene") != right.get("scene"):
        return None
    begin, end = float(left["time"]), float(right["time"])
    if begin <= stamp <= end and end - begin <= max_gap:
        if math.dist((left["x"], left["z"]), (right["x"], right["z"])) > max(1.0, (end - begin) * 30):
            return None
        weight = (stamp - begin) / (end - begin) if end > begin else 0.0
        return {
            "x": left["x"] + weight * (right["x"] - left["x"]),
            "z": left["z"] + weight * (right["z"] - left["z"]),
            "mode": "interpolated",
            "gap_s": end - begin,
            "scene": left.get("scene"),
            "mapId": reference_map_id(left),
            "flow": left.get("flow"),
        }
    if (
        stationary
        and float(left.get("speed") if left.get("speed") is not None else math.inf) <= 0.1
        and stamp >= begin
        and (index == len(samples) or end - stamp > max_gap)
    ):
        return {
            "x": left["x"],
            "z": left["z"],
            "mode": "stationary_hold",
            "gap_s": stamp - begin,
            "scene": left.get("scene"),
            "mapId": reference_map_id(left),
            "flow": left.get("flow"),
        }
    return None


def error_statistics(pairs):
    if not pairs:
        return {"count": 0}
    delta = np.asarray(pairs, dtype=float)
    distance = np.linalg.norm(delta, axis=1)
    return {
        "count": len(pairs),
        "median_m": float(np.median(distance)),
        "p95_m": float(np.quantile(distance, 0.95)),
        "max_m": float(distance.max()),
        "rmse_m": float(np.sqrt(np.mean(distance**2))),
        "bias_x_m": float(np.median(delta[:, 0])),
        "bias_z_m": float(np.median(delta[:, 1])),
    }
