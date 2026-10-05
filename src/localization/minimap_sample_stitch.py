"""小地图定位诊断：切片裁剪、连续里程计轨迹、位姿图对齐、最大权重拼接与统计。

本模块不依赖 ok 框架，只做数学/图像处理，供
``src.tasks.localization.MinimapPositionRecorder`` 和
``scripts/data-capture/stitch_minimap_recorder.py`` 共用。

坐标与拼接流程：

1. 每张小地图切片保存为 BGRA PNG，alpha 是圆环 mask；
2. 切片初值来自 ``fused_px``（缺少时用 ``odom_px`` + 全局偏移补齐）；
3. 对候选切片对做重叠区相位相关，得到它们之间的相对平移 ``d_ij``；
4. 把每张切片当成一个节点，求解加权最小二乘位姿图：

   ``Σ w_ij ||(p_j - p_i) - d_ij||² + Σ w_k ||p_k - a_k||²``

   其中 ``a_k`` 是静止校准时刻的 WS 绝对锚点。二维平移分量解耦，用预条件
   共轭梯度（PCG）分别求 x / y，不需要 scipy，也不形成稠密矩阵；
5. 用 Huber 可靠权重迭代，抑制动态 UI、切图、低相关边等离群约束；
6. 最后按优化后的位置做“最大权重选片”：每个像素取 alpha 最高的那一片，
   避免把大量未完全对齐的重叠切片平均成模糊图。

``fused_px`` / ``odom_px`` / ``step_px`` 的语义保持不变，分别用于初值、连续
里程计诊断和逐拍位移统计。
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from src.localization.minimap_odometry import annulus_mask, region_geometry

__all__ = [
    "ContinuousOdomTrack",
    "PatchNode",
    "PoseGraphEdge",
    "PoseGraphResult",
    "candidate_pairs",
    "crop_minimap_patch",
    "fused_map_px",
    "imread_unicode",
    "imwrite_unicode",
    "measure_edge",
    "read_jsonl",
    "solve_component",
    "solve_pose_graph",
    "stitch_records",
    "summarize_records",
    "time_gaps",
    "write_jsonl",
]

_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]")


def _safe_component(text, fallback: str = "unknown") -> str:
    """把 map_id 之类的外部字符串变成安全的文件名片段。"""

    cleaned = _SAFE_RE.sub("_", str(text if text is not None else "").strip())
    return cleaned.strip("._") or fallback


def imread_unicode(path: str | Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    """``cv2.imread`` 在 Windows 下不认非 ASCII 路径，这里用 imdecode 绕开。"""

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite_unicode(path: str | Path, image: np.ndarray) -> bool:
    """``cv2.imwrite`` 的 Windows 非 ASCII 路径安全版本。"""

    path = Path(path)
    suffix = path.suffix or ".png"
    ok, buf = cv2.imencode(suffix, image)
    if not ok:
        return False
    try:
        buf.tofile(str(path))
    except OSError:
        return False
    return True


def write_jsonl(path: str | Path, rows: Iterable[Mapping]) -> int:
    """原子写 JSONL，返回写入条数。"""

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")
            count += 1
    tmp.replace(path)
    return count


def read_jsonl(path: str | Path) -> list[dict]:
    """读取 JSONL；空行和坏行直接跳过。"""

    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def fused_map_px(map_to_world_px, x: float | None, z: float | None):
    """把融合世界坐标 (x, z) 换算成小地图像素 (px, py)。

    ``map_to_world_px`` 满足 ``world = M @ map_px``，所以这里用逆矩阵。
    返回 ``(px, py)`` 或 ``None``。
    """

    if x is None or z is None:
        return None
    try:
        matrix = np.asarray(map_to_world_px, dtype=np.float64).reshape(2, 2)
        world = np.asarray([float(x), float(z)], dtype=np.float64)
        if not np.all(np.isfinite(world)):
            return None
        if abs(float(np.linalg.det(matrix))) < 1e-12:
            return None
        map_px = np.linalg.solve(matrix, world)
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return None
    if not np.all(np.isfinite(map_px)):
        return None
    return (float(map_px[0]), float(map_px[1]))


class ContinuousOdomTrack:
    """把融合重锚会清零的 ``position_px()`` 接成连续里程计轨迹。"""

    def __init__(self):
        self._origin = np.zeros(2, dtype=np.float64)
        self._prev_position = None
        self._track = None

    def reset(self) -> None:
        """地图切换时调用：清空原点和上一拍，开始新一段轨迹。"""

        self._origin = np.zeros(2, dtype=np.float64)
        self._prev_position = None
        self._track = None

    def update(
        self,
        position_px: Sequence[float],
        *,
        reset_anchor: bool = False,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """推进一拍，返回 ``(track_px, step_px)``。

        Args:
            position_px: 里程计当前的 ``position_px()``（相对当前融合锚点）。
            reset_anchor: 本拍是否发生了重锚并清零了 ``position_px()``。为真时用
                上一拍位置推进全局原点，保证 ``track_px`` 连续。
        """

        pos = np.asarray(position_px, dtype=np.float64).reshape(2)
        if not np.all(np.isfinite(pos)):
            pos = np.zeros(2, dtype=np.float64)
        if reset_anchor and self._prev_position is not None:
            self._origin = self._origin + self._prev_position
        track = self._origin + pos
        step = track if self._track is None else track - self._track
        self._prev_position = pos
        self._track = track
        return (
            (float(track[0]), float(track[1])),
            (float(step[0]), float(step[1])),
        )


def crop_minimap_patch(
    frame: np.ndarray,
    width: int,
    height: int,
    *,
    pad: int = 2,
    feather: int = 3,
) -> tuple[np.ndarray | None, dict]:
    """从整帧裁出小地图圆环，返回 ``(BGRA patch, meta)``。

    patch 的 alpha 通道是 :func:`annulus_mask` 生成的羽化圆环。meta 里带
    patch 尺寸、圆心在 patch 内的坐标和内外半径，供离线拼接使用。
    """

    if frame is None:
        return None, {}
    width = int(width or 0)
    height = int(height or 0)
    if width <= 0 or height <= 0:
        return None, {}
    if frame.shape[0] != height or frame.shape[1] != width:
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.shape[2] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    cx, cy, r_inner, r_outer = region_geometry(width, height)
    half = math.ceil(r_outer) + max(0, int(pad))
    side = half * 2
    if side <= 0:
        return None, {}

    x0 = round(cx) - half
    y0 = round(cy) - half
    x1 = x0 + side
    y1 = y0 + side
    patch = np.zeros((side, side, 3), dtype=np.uint8)

    sx0 = max(0, x0)
    sy0 = max(0, y0)
    sx1 = min(width, x1)
    sy1 = min(height, y1)
    if sx1 > sx0 and sy1 > sy0:
        dx0 = sx0 - x0
        dy0 = sy0 - y0
        patch[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)] = frame[sy0:sy1, sx0:sx1]

    center = (float(cx - x0), float(cy - y0))
    alpha = annulus_mask(side, side, center, r_inner, r_outer, feather=feather)
    patch_bgra = cv2.cvtColor(patch, cv2.COLOR_BGR2BGRA)
    patch_bgra[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

    meta = {
        "size": [int(side), int(side)],
        "center": [float(center[0]), float(center[1])],
        "r_inner": float(r_inner),
        "r_outer": float(r_outer),
        "region": [float(cx), float(cy), float(r_inner), float(r_outer)],
    }
    return patch_bgra, meta


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    idx = round(max(0.0, min(1.0, q)) * (len(ordered) - 1))
    return ordered[idx]


def _stat_block(values: Sequence[float], *, digits: int = 4) -> dict:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return {"n": 0, "median": None, "p90": None, "max": None}
    vals_sorted = sorted(vals)
    median = vals_sorted[len(vals_sorted) // 2]
    if len(vals_sorted) % 2 == 0:
        median = (vals_sorted[len(vals_sorted) // 2 - 1] + vals_sorted[len(vals_sorted) // 2]) / 2.0
    return {
        "n": len(vals),
        "median": round(float(median), digits),
        "p90": round(float(_percentile(vals_sorted, 0.9)), digits),
        "max": round(float(max(vals_sorted)), digits),
    }


def summarize_records(
    records: Sequence[Mapping],
    *,
    calibrations: Sequence[Mapping] | None = None,
    scale_m_per_px: float | None = None,
) -> dict:
    """汇总采样记录：逐拍像素位移、校准残差、相关响应和里程计原因分布。"""

    records = list(records)
    calibrations = list(calibrations or [])
    scale = None
    if scale_m_per_px is not None:
        try:
            scale = float(scale_m_per_px)
            if not math.isfinite(scale) or scale <= 0:
                scale = None
        except (TypeError, ValueError):
            scale = None
    step_px = []
    step_m_values = []
    residual_m = []
    response = []
    reasons: Counter[str] = Counter()
    map_ids = set()
    segments = set()

    for r in records:
        step = r.get("step_px")
        if isinstance(step, (list, tuple)) and len(step) == 2:
            length = float(math.hypot(float(step[0]), float(step[1])))
            step_px.append(length)
            record_scale = r.get("scale_m_per_px")
            try:
                record_scale = float(record_scale) if record_scale is not None else None
            except (TypeError, ValueError):
                record_scale = None
            if record_scale is None:
                record_scale = scale
            if record_scale is not None and record_scale > 0:
                step_m_values.append(length * record_scale)
        raw = r.get("odom_response")
        if raw is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                response.append(value)
        reason = r.get("odom_reason")
        reasons[str(reason if reason is not None else "none")] += 1
        if r.get("map_id") is not None:
            map_ids.add(str(r.get("map_id")))
        if r.get("segment") is not None:
            segments.add(int(r.get("segment")))

    for c in calibrations:
        dist = c.get("residual_dist_m")
        if dist is None:
            continue
        with contextlib.suppress(TypeError, ValueError):
            residual_m.append(float(dist))

    path_px = float(sum(step_px))
    path_m = float(sum(step_m_values)) if step_m_values else None
    out = {
        "samples": len(records),
        "calibrations": len(calibrations),
        "duration_s": None,
        "map_ids": sorted(map_ids),
        "segments": sorted(segments),
        "scale_m_per_px": scale,
        "path_px": round(path_px, 3),
        "path_m": None if path_m is None else round(path_m, 3),
        "step_px": _stat_block(step_px, digits=4),
        "step_m": None if not step_m_values else _stat_block(step_m_values, digits=4),
        "sync_residual_m": _stat_block(residual_m, digits=4),
        "sync_residual_values_m": [round(v, 4) for v in residual_m],
        "response": {
            "n": len(response),
            "min": round(float(min(response)), 4) if response else None,
            "median": round(float(sorted(response)[len(response) // 2]), 4) if response else None,
        },
        "odom_reason_counts": dict(sorted(reasons.items())),
    }
    if records:
        times = [float(r["t"]) for r in records if r.get("t") is not None and math.isfinite(float(r.get("t")))]
        if times:
            out["duration_s"] = round(max(times) - min(times), 3)
    return out


def _patch_alpha(patch: np.ndarray, record: Mapping) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    """拆出 BGR 和 alpha；没有 alpha 通道时按记录的圆环参数重建。"""

    patch = np.asarray(patch)
    if patch.ndim != 3:
        raise ValueError("patch 必须是 3 维图像")
    if patch.shape[2] == 4:
        bgr = patch[:, :, :3].astype(np.uint8)
        alpha = patch[:, :, 3].astype(np.float32) / 255.0
    else:
        bgr = patch.astype(np.uint8)
        h, w = bgr.shape[:2]
        center = record.get("patch_center") or [w / 2.0, h / 2.0]
        r_inner = float(record.get("patch_r_inner") or 0.0)
        r_outer = float(record.get("patch_r_outer") or (min(w, h) / 2.0))
        alpha = annulus_mask(h, w, (float(center[0]), float(center[1])), r_inner, r_outer)
    center = record.get("patch_center") or [patch.shape[1] / 2.0, patch.shape[0] / 2.0]
    return bgr, np.clip(alpha, 0.0, 1.0), (float(center[0]), float(center[1]))


# ---------------------------------------------------------------------- #
# 位姿图
# ---------------------------------------------------------------------- #


@dataclass(slots=True)
class PatchNode:
    """一张参与位姿图的切片。"""

    index: int
    record: Mapping
    path: Path
    bgr: np.ndarray
    gray: np.ndarray
    alpha: np.ndarray
    patch_center: tuple[float, float]
    initial_pos: np.ndarray
    anchor: np.ndarray | None = None
    anchor_weight: float = 0.0


@dataclass(slots=True)
class PoseGraphEdge:
    """两张切片之间的相对平移约束。"""

    i: int
    j: int
    weight: float
    dx: float
    dy: float
    response: float
    overlap_fraction: float
    gap: int
    initial_residual: float = 0.0
    optimized_residual: float = 0.0


@dataclass(slots=True)
class PoseGraphResult:
    """位姿图求解结果。"""

    positions: np.ndarray
    edges: list[PoseGraphEdge]
    iterations: int
    residual_before: np.ndarray
    residual_after: np.ndarray
    weights_after: np.ndarray


def _load_patch_node(index: int, record: Mapping, patch_root: Path) -> PatchNode | None:
    patch_path = patch_root / str(record.get("patch") or "")
    patch = imread_unicode(patch_path, cv2.IMREAD_UNCHANGED)
    if patch is None:
        return None
    try:
        bgr, alpha, center = _patch_alpha(patch, record)
    except ValueError:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return PatchNode(
        index=index,
        record=record,
        path=patch_path,
        bgr=bgr,
        gray=gray,
        alpha=alpha,
        patch_center=center,
        initial_pos=np.zeros(2, dtype=np.float64),
    )


def _initial_positions(nodes: Sequence[PatchNode]) -> np.ndarray:
    """初始化每张切片的画布位置：优先 fused_px，缺失时用 odom_px + 全局偏移。"""

    offset = None
    for node in nodes:
        fused = node.record.get("fused_px")
        odom = node.record.get("odom_px")
        if fused is not None and odom is not None:
            offset = np.asarray(fused, dtype=np.float64) - np.asarray(odom, dtype=np.float64)
            break
    positions = np.zeros((len(nodes), 2), dtype=np.float64)
    for node in nodes:
        fused = node.record.get("fused_px")
        odom = node.record.get("odom_px")
        if fused is not None:
            pos = np.asarray(fused, dtype=np.float64)
        elif odom is not None:
            pos = np.asarray(odom, dtype=np.float64)
            if offset is not None:
                pos = pos + offset
        else:
            pos = np.zeros(2, dtype=np.float64)
        node.initial_pos = pos
        positions[node.index] = pos
    return positions


def _sync_anchors(nodes: Sequence[PatchNode], map_to_world_px, weight: float) -> None:
    """把静止校准的 WS 绝对坐标写成锚点约束。"""

    if not map_to_world_px:
        return
    for node in nodes:
        if not node.record.get("just_synced"):
            continue
        ws = None
        residual = node.record.get("sync_residual")
        if isinstance(residual, Mapping) and residual.get("ws_x") is not None:
            ws = (residual.get("ws_x"), residual.get("ws_z"))
        elif node.record.get("ws") is not None:
            ws = tuple(node.record["ws"][:2])
        if ws is None:
            continue
        anchor = fused_map_px(map_to_world_px, ws[0], ws[1])
        if anchor is None:
            continue
        node.anchor = np.asarray(anchor, dtype=np.float64)
        node.anchor_weight = float(weight)


def time_gaps(max_gap: int) -> list[int]:
    """几何递增的时间间隔，兼顾短程精度和长程约束，避免 O(n²)。"""

    max_gap = max(1, int(max_gap))
    gaps: list[int] = []
    gap = 1
    while gap <= max_gap:
        gaps.append(gap)
        gap = max(gap + 1, round(gap * 1.6))
    if gaps[-1] != max_gap:
        gaps.append(max_gap)
    return sorted(set(gaps))


def candidate_pairs(
    positions: np.ndarray,
    *,
    edge_max_gap: int,
    spatial_radius: float,
    spatial_neighbors: int,
) -> list[tuple[int, int]]:
    """时间近邻 + 空间近邻（回环）两类候选边。"""

    n = len(positions)
    pairs: set[tuple[int, int]] = set()
    for i in range(n):
        for gap in time_gaps(edge_max_gap):
            j = i + gap
            if j < n:
                pairs.add((i, j))
    if spatial_radius > 0 and spatial_neighbors > 0:
        cell = max(32.0, float(spatial_radius))
        grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx, pos in enumerate(positions):
            grid[(math.floor(pos[0] / cell), math.floor(pos[1] / cell))].append(idx)
        for i, pos in enumerate(positions):
            cx, cy = math.floor(pos[0] / cell), math.floor(pos[1] / cell)
            nearest: list[tuple[float, int]] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((cx + dx, cy + dy), ()):
                        if j <= i:
                            continue
                        dist = float(np.linalg.norm(positions[j] - pos))
                        if dist <= spatial_radius:
                            nearest.append((dist, j))
            nearest.sort()
            for _, j in nearest[:spatial_neighbors]:
                pairs.add((i, j))
    return sorted(pairs)


@lru_cache(maxsize=64)
def _hann_window(height: int, width: int) -> np.ndarray:
    """按重叠区尺寸缓存 Hann 窗，避免每对切片重复分配。"""

    return cv2.createHanningWindow((width, height), cv2.CV_32F)


def measure_edge(
    node_a: PatchNode,
    node_b: PatchNode,
    *,
    base: np.ndarray,
    alpha_threshold: float,
    min_overlap_px: int,
    max_correlation_size: int = 192,
) -> tuple[float, float, float, float, float] | None:
    """在初始位置附近的重叠区做相位相关，返回总相对平移和残差。"""

    ha, wa = node_a.gray.shape[:2]
    hb, wb = node_b.gray.shape[:2]
    x0 = max(0, int(base[0]))
    y0 = max(0, int(base[1]))
    x1 = min(wa, int(base[0]) + wb)
    y1 = min(ha, int(base[1]) + hb)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    bx0 = x0 - int(base[0])
    by0 = y0 - int(base[1])
    bx1 = bx0 + (x1 - x0)
    by1 = by0 + (y1 - y0)
    if bx0 < 0 or by0 < 0 or bx1 > wb or by1 > hb:
        return None

    ga = node_a.gray[y0:y1, x0:x1].astype(np.float32)
    gb = node_b.gray[by0:by1, bx0:bx1].astype(np.float32)
    aa = node_a.alpha[y0:y1, x0:x1]
    ab = node_b.alpha[by0:by1, bx0:bx1]
    mask = (aa >= alpha_threshold) & (ab >= alpha_threshold)
    full_area = int(mask.sum())
    if full_area < min_overlap_px:
        return None
    if max_correlation_size and max(ga.shape) > max_correlation_size:
        h0 = (ga.shape[0] - max_correlation_size) // 2
        w0 = (ga.shape[1] - max_correlation_size) // 2
        ga = ga[h0 : h0 + max_correlation_size, w0 : w0 + max_correlation_size]
        gb = gb[h0 : h0 + max_correlation_size, w0 : w0 + max_correlation_size]
        mask = mask[h0 : h0 + max_correlation_size, w0 : w0 + max_correlation_size]
    area = int(mask.sum())
    if area < min_overlap_px:
        return None

    vals_a = ga[mask]
    vals_b = gb[mask]
    mean_a = float(vals_a.mean())
    mean_b = float(vals_b.mean())
    std_a = float(vals_a.std()) + 1e-6
    std_b = float(vals_b.std()) + 1e-6
    na = ((ga - mean_a) / std_a) * mask
    nb = ((gb - mean_b) / std_b) * mask
    window = _hann_window(na.shape[0], na.shape[1])
    (dx, dy), response = cv2.phaseCorrelate(na * window, nb * window)
    response = float(response) if np.isfinite(response) else 0.0
    residual = float(math.hypot(dx, dy))
    overlap_fraction = full_area / max(1, min(node_a.gray.size, node_b.gray.size))
    return (
        float(base[0] - dx),
        float(base[1] - dy),
        response,
        float(overlap_fraction),
        residual,
    )


def _build_edges(
    nodes: Sequence[PatchNode],
    *,
    pairs: Sequence[tuple[int, int]],
    edge_max_gap: int,
    alpha_threshold: float,
    min_overlap_px: int,
    min_response: float,
    max_residual_px: float,
) -> list[PoseGraphEdge]:
    """对候选切片对做相位相关，得到加权相对平移边。"""

    edges: list[PoseGraphEdge] = []
    for i, j in pairs:
        a, b = nodes[i], nodes[j]
        delta = b.initial_pos - a.initial_pos
        base = np.floor(delta).astype(np.int64)
        measured = measure_edge(
            a,
            b,
            base=base,
            alpha_threshold=alpha_threshold,
            min_overlap_px=min_overlap_px,
            max_correlation_size=256,
        )
        if measured is None:
            continue
        dx, dy, response, overlap_fraction, residual = measured
        if response < min_response or residual > max_residual_px:
            continue
        gap = abs(j - i)
        time_weight = 1.0 / (1.0 + gap / max(1.0, float(edge_max_gap)))
        weight = (response**2) * overlap_fraction * time_weight
        if weight <= 0.0:
            continue
        edges.append(
            PoseGraphEdge(
                i=i,
                j=j,
                weight=float(weight),
                dx=float(dx),
                dy=float(dy),
                response=response,
                overlap_fraction=overlap_fraction,
                gap=gap,
                initial_residual=residual,
            )
        )
    return edges


def solve_component(
    n: int,
    *,
    edge_i: np.ndarray,
    edge_j: np.ndarray,
    edge_w: np.ndarray,
    edge_d: np.ndarray,
    anchor_i: np.ndarray,
    anchor_w: np.ndarray,
    anchor_a: np.ndarray,
    prior_w: float,
    prior_p: np.ndarray,
    x0: np.ndarray,
    max_iter: int,
    tol: float,
) -> tuple[np.ndarray, int]:
    """PCG 解一个坐标分量（x 或 y）。矩阵不显式构造。"""

    if n == 0:
        return np.zeros(0, dtype=np.float64), 0
    b = np.zeros(n, dtype=np.float64)
    diag = np.zeros(n, dtype=np.float64)
    if edge_w.size:
        np.add.at(b, edge_i, -edge_w * edge_d)
        np.add.at(b, edge_j, edge_w * edge_d)
        np.add.at(diag, edge_i, edge_w)
        np.add.at(diag, edge_j, edge_w)
    if anchor_w.size:
        np.add.at(b, anchor_i, anchor_w * anchor_a)
        np.add.at(diag, anchor_i, anchor_w)
    b += prior_w * prior_p
    diag += prior_w
    diag += 1e-9

    def matvec(x: np.ndarray) -> np.ndarray:
        y = np.zeros(n, dtype=np.float64)
        if edge_w.size:
            contrib = edge_w * (x[edge_i] - x[edge_j])
            y += np.bincount(edge_i, weights=contrib, minlength=n)
            y -= np.bincount(edge_j, weights=contrib, minlength=n)
        if anchor_w.size:
            y[anchor_i] += anchor_w * x[anchor_i]
        if prior_w:
            y += prior_w * x
        return y

    x = x0.copy()
    r = b - matvec(x)
    z = r / diag
    p = z.copy()
    rz = float(r @ z)
    iterations = 0
    for iteration in range(1, max_iter + 1):
        iterations = iteration
        ap = matvec(p)
        denom = float(p @ ap)
        if denom <= 1e-18:
            break
        alpha = rz / denom
        x += alpha * p
        r -= alpha * ap
        if float(np.linalg.norm(r)) <= tol * max(1.0, float(np.linalg.norm(b))):
            break
        z = r / diag
        rz_new = float(r @ z)
        if abs(rz) <= 1e-30:
            break
        beta = rz_new / rz
        p = z + beta * p
        rz = rz_new
    return x, iterations


def solve_pose_graph(
    nodes: Sequence[PatchNode],
    edges: Sequence[PoseGraphEdge],
    *,
    anchor_weight: float,
    prior_weight: float,
    robust_delta_px: float,
    robust_iterations: int,
    max_iter: int,
    tol: float,
    initial_positions: np.ndarray | None = None,
    extra_anchors: Sequence[tuple[int, np.ndarray, float]] | None = None,
) -> PoseGraphResult:
    """Huber IRLS + PCG 求解二维平移位姿图。"""

    n = len(nodes)
    if n == 0:
        return PoseGraphResult(
            positions=np.zeros((0, 2), dtype=np.float64),
            edges=list(edges),
            iterations=0,
            residual_before=np.zeros(0, dtype=np.float64),
            residual_after=np.zeros(0, dtype=np.float64),
            weights_after=np.zeros(0, dtype=np.float64),
        )

    initial = np.asarray([node.initial_pos for node in nodes], dtype=np.float64)
    edge_i = np.asarray([e.i for e in edges], dtype=np.int64)
    edge_j = np.asarray([e.j for e in edges], dtype=np.int64)
    edge_dx = np.asarray([e.dx for e in edges], dtype=np.float64)
    edge_dy = np.asarray([e.dy for e in edges], dtype=np.float64)
    base_weights = np.asarray([e.weight for e in edges], dtype=np.float64)
    anchor_idx = np.asarray([i for i, node in enumerate(nodes) if node.anchor is not None], dtype=np.int64)
    anchor_w = (
        np.asarray([node.anchor_weight for node in nodes if node.anchor is not None], dtype=np.float64)
        if anchor_idx.size
        else np.zeros(0, dtype=np.float64)
    )
    anchor_x = (
        np.asarray([node.anchor[0] for node in nodes if node.anchor is not None], dtype=np.float64)
        if anchor_idx.size
        else np.zeros(0, dtype=np.float64)
    )
    anchor_y = (
        np.asarray([node.anchor[1] for node in nodes if node.anchor is not None], dtype=np.float64)
        if anchor_idx.size
        else np.zeros(0, dtype=np.float64)
    )
    if extra_anchors:
        extra_i = []
        extra_w = []
        extra_x = []
        extra_y = []
        for index, position, weight in extra_anchors:
            if index < 0 or index >= n or weight <= 0:
                continue
            pos = np.asarray(position, dtype=np.float64).reshape(2)
            if not np.all(np.isfinite(pos)):
                continue
            extra_i.append(int(index))
            extra_w.append(float(weight))
            extra_x.append(float(pos[0]))
            extra_y.append(float(pos[1]))
        if extra_i:
            anchor_idx = np.concatenate([anchor_idx, np.asarray(extra_i, dtype=np.int64)])
            anchor_w = np.concatenate([anchor_w, np.asarray(extra_w, dtype=np.float64)])
            anchor_x = np.concatenate([anchor_x, np.asarray(extra_x, dtype=np.float64)])
            anchor_y = np.concatenate([anchor_y, np.asarray(extra_y, dtype=np.float64)])

    def residuals(positions: np.ndarray) -> np.ndarray:
        if edge_i.size == 0:
            return np.zeros(0, dtype=np.float64)
        dx = (positions[edge_j, 0] - positions[edge_i, 0]) - edge_dx
        dy = (positions[edge_j, 1] - positions[edge_i, 1]) - edge_dy
        return np.hypot(dx, dy)

    residual_before = residuals(initial)
    weights = base_weights.copy()
    positions = initial.copy() if initial_positions is None else np.asarray(initial_positions, dtype=np.float64).copy()
    total_iterations = 0
    for _ in range(max(1, robust_iterations)):
        x, it_x = solve_component(
            n,
            edge_i=edge_i,
            edge_j=edge_j,
            edge_w=weights,
            edge_d=edge_dx,
            anchor_i=anchor_idx,
            anchor_w=anchor_w,
            anchor_a=anchor_x,
            prior_w=prior_weight,
            prior_p=initial[:, 0],
            x0=positions[:, 0],
            max_iter=max_iter,
            tol=tol,
        )
        y, it_y = solve_component(
            n,
            edge_i=edge_i,
            edge_j=edge_j,
            edge_w=weights,
            edge_d=edge_dy,
            anchor_i=anchor_idx,
            anchor_w=anchor_w,
            anchor_a=anchor_y,
            prior_w=prior_weight,
            prior_p=initial[:, 1],
            x0=positions[:, 1],
            max_iter=max_iter,
            tol=tol,
        )
        positions = np.column_stack([x, y])
        total_iterations += max(it_x, it_y)
        if edge_i.size == 0:
            break
        current = residuals(positions)
        robust = np.ones_like(current)
        mask = current > robust_delta_px
        robust[mask] = np.sqrt(robust_delta_px / current[mask])
        new_weights = base_weights * robust
        if np.allclose(new_weights, weights):
            weights = new_weights
            break
        weights = new_weights

    residual_after = residuals(positions)
    if not np.all(np.isfinite(positions)) or float(np.max(np.linalg.norm(positions - initial, axis=1))) > 500.0:
        positions = initial.copy()
        residual_after = residuals(positions)
    return PoseGraphResult(
        positions=positions,
        edges=list(edges),
        iterations=int(total_iterations),
        residual_before=residual_before,
        residual_after=residual_after,
        weights_after=weights,
    )


# ---------------------------------------------------------------------- #
# 最大权重拼接
# ---------------------------------------------------------------------- #


def _warp_roi(
    image: np.ndarray,
    *,
    canvas_shape: tuple[int, int],
    top_left: tuple[float, float],
) -> tuple[np.ndarray, tuple[int, int, int, int], int, int]:
    """把整张切片按亚像素位置 warp 到局部 ROI，返回 ROI 图和外接框。"""

    ph, pw = image.shape[:2]
    x0f = float(top_left[0])
    y0f = float(top_left[1])
    x0 = math.floor(x0f) - 1
    y0 = math.floor(y0f) - 1
    x1 = math.ceil(x0f + pw) + 1
    y1 = math.ceil(y0f + ph) + 1
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(canvas_shape[1], x1)
    cy1 = min(canvas_shape[0], y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return np.zeros((0, 0), image.dtype), (0, 0, 0, 0), 0, 0
    transform = np.float32([[1, 0, x0f - x0], [0, 1, y0f - y0]])
    warped = cv2.warpAffine(
        image,
        transform,
        (x1 - x0, y1 - y0),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    sx0 = cx0 - x0
    sy0 = cy0 - y0
    sx1 = cx1 - x0
    sy1 = cy1 - y0
    return warped[sy0:sy1, sx0:sx1], (cy0, cy1, cx0, cx1), sx0, sy0


def _stitch_best_patch(
    nodes: Sequence[PatchNode],
    positions: np.ndarray,
    *,
    margin: int,
    max_side: int,
    background: tuple[int, int, int],
) -> tuple[np.ndarray, np.ndarray, dict]:
    """按优化位置做最大权重选片，返回画布、原点和诊断。"""

    half_max = 0.0
    for node in nodes:
        half_max = max(half_max, max(node.bgr.shape[:2]) / 2.0)
    half_max = max(half_max, 1.0)
    lo = positions.min(axis=0) - half_max - max(0, int(margin))
    hi = positions.max(axis=0) + half_max + max(0, int(margin))
    width = math.ceil(float(hi[0] - lo[0]))
    height = math.ceil(float(hi[1] - lo[1]))
    clipped = False
    max_side = max(64, int(max_side))
    if width > max_side or height > max_side:
        clipped = True
        center = positions.mean(axis=0)
        lo = np.asarray([center[0] - max_side / 2.0, center[1] - max_side / 2.0], dtype=np.float64)
        width = height = max_side
    best_weight = np.zeros((height, width), dtype=np.float32)
    best_rgb = np.zeros((height, width, 3), dtype=np.uint8)
    placed = 0
    for node in nodes:
        center = positions[node.index] - lo
        top_left = (center[0] - node.patch_center[0], center[1] - node.patch_center[1])
        warped_bgr, bounds, _, _ = _warp_roi(node.bgr, canvas_shape=(height, width), top_left=top_left)
        if warped_bgr.size == 0:
            continue
        warped_alpha, _, _, _ = _warp_roi(node.alpha, canvas_shape=(height, width), top_left=top_left)
        cy0, cy1, cx0, cx1 = bounds
        roi_w = best_weight[cy0:cy1, cx0:cx1]
        roi_rgb = best_rgb[cy0:cy1, cx0:cx1]
        mask = warped_alpha > roi_w
        roi_w[mask] = warped_alpha[mask]
        roi_rgb[mask] = warped_bgr[mask]
        placed += 1
    alpha = best_weight[..., None]
    canvas = best_rgb.astype(np.float32) * alpha + np.asarray(background, dtype=np.float32) * (1.0 - alpha)
    canvas = np.clip(canvas, 0, 255).astype(np.uint8)
    diagnostics = {
        "size": [int(width), int(height)],
        "placed": int(placed),
        "skipped": int(len(nodes) - placed),
        "clipped": bool(clipped),
        "origin": [float(lo[0]), float(lo[1])],
    }
    return canvas, lo, diagnostics


def _draw_overlay(
    image: np.ndarray,
    nodes: Sequence[PatchNode],
    positions: np.ndarray,
    origin: np.ndarray,
) -> None:
    """在拼接图上画出优化后的轨迹、起终点和同步点。"""

    pts = np.asarray(positions - origin, dtype=np.float64)
    if len(pts) == 0:
        return
    pts_i = np.round(pts).astype(np.int32)
    if len(pts_i) >= 2:
        cv2.polylines(image, [pts_i], False, (90, 90, 90), 1, cv2.LINE_AA)
    stride = max(1, len(pts_i) // 200)
    for idx in range(0, len(pts_i), stride):
        x, y = pts_i[idx]
        cv2.circle(image, (int(x), int(y)), 1, (150, 150, 150), -1, cv2.LINE_AA)
    for node, pt in zip(nodes, pts_i, strict=False):
        if not node.record.get("just_synced"):
            continue
        x, y = int(pt[0]), int(pt[1])
        cv2.circle(image, (x, y), 6, (0, 220, 0), 2, cv2.LINE_AA)
        residual = node.record.get("sync_residual") or {}
        dist = residual.get("dist")
        label = "sync" if dist is None else f"sync {float(dist):.2f}m"
        cv2.putText(
            image,
            label,
            (x + 8, y - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 220, 0),
            1,
            cv2.LINE_AA,
        )
    start, end = pts_i[0], pts_i[-1]
    cv2.circle(image, (int(start[0]), int(start[1])), 5, (255, 120, 0), -1, cv2.LINE_AA)
    cv2.circle(image, (int(end[0]), int(end[1])), 5, (0, 80, 255), -1, cv2.LINE_AA)
    cv2.putText(
        image,
        "start",
        (int(start[0]) + 6, int(start[1]) + 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (255, 120, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        image, "end", (int(end[0]) + 6, int(end[1]) + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 80, 255), 1, cv2.LINE_AA
    )


def _residual_stats(values: np.ndarray) -> dict:
    vals = [float(v) for v in values if math.isfinite(float(v))]
    if not vals:
        return {"n": 0, "median": None, "p90": None, "max": None, "rms": None}
    rms = math.sqrt(sum(v * v for v in vals) / len(vals))
    return {
        "n": len(vals),
        "median": round(float(_percentile(vals, 0.5)), 4),
        "p90": round(float(_percentile(vals, 0.9)), 4),
        "max": round(float(max(vals)), 4),
        "rms": round(float(rms), 4),
    }


def _pose_graph_payload(
    nodes: Sequence[PatchNode],
    positions: np.ndarray,
    edges: Sequence[PoseGraphEdge],
    result: PoseGraphResult,
    *,
    anchor_weight: float,
    prior_weight: float,
) -> dict:
    """产出可离线分析的位姿图 JSON。"""

    return {
        "nodes": [
            {
                "i": int(node.record.get("i") or node.index),
                "index": int(node.index),
                "initial": [float(node.initial_pos[0]), float(node.initial_pos[1])],
                "optimized": [float(positions[node.index][0]), float(positions[node.index][1])],
                "anchor": None if node.anchor is None else [float(node.anchor[0]), float(node.anchor[1])],
                "anchor_weight": float(node.anchor_weight),
                "is_sync": bool(node.record.get("just_synced")),
                "map_id": node.record.get("map_id"),
            }
            for node in nodes
        ],
        "edges": [
            {
                "i": int(nodes[e.i].record.get("i") or e.i),
                "j": int(nodes[e.j].record.get("i") or e.j),
                "gap": int(e.gap),
                "weight": round(float(e.weight), 8),
                "response": round(float(e.response), 6),
                "overlap_fraction": round(float(e.overlap_fraction), 6),
                "dx": round(float(e.dx), 6),
                "dy": round(float(e.dy), 6),
                "initial_residual": round(float(e.initial_residual), 6),
                "optimized_residual": round(float(e.optimized_residual), 6),
            }
            for e in edges
        ],
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "anchors": sum(1 for node in nodes if node.anchor is not None),
            "iterations": int(result.iterations),
            "anchor_weight": float(anchor_weight),
            "prior_weight": float(prior_weight),
            "residual_before": _residual_stats(result.residual_before),
            "residual_after": _residual_stats(result.residual_after),
        },
    }


def stitch_records(
    records: Sequence[Mapping],
    *,
    patch_root: str | Path,
    out_dir: str | Path | None = None,
    margin: int = 128,
    max_side: int = 4096,
    draw_overlay: bool = True,
    edge_max_gap: int = 30,
    spatial_radius: float = 260.0,
    spatial_neighbors: int = 24,
    min_response: float = 0.25,
    min_overlap_px: int = 600,
    max_residual_px: float = 6.0,
    alpha_threshold: float = 0.2,
    anchor_weight: float = 50.0,
    prior_weight: float = 0.01,
    robust_delta_px: float = 3.0,
    robust_iterations: int = 3,
    max_iterations: int = 400,
    tolerance: float = 1e-6,
    background: tuple[int, int, int] = (32, 32, 32),
) -> list[dict]:
    """用位姿图对齐切片，并按最大权重选片拼成参考图。

    每个 ``(map_id, segment)`` 分组独立求解。返回每组的输出路径与位姿图统计。
    """

    patch_root = Path(patch_root)
    out_dir = Path(out_dir) if out_dir is not None else patch_root
    out_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[tuple[str, int], list[Mapping]] = {}
    for record in records:
        if not record.get("patch"):
            continue
        if record.get("fused_px") is None and record.get("odom_px") is None:
            continue
        key = (str(record.get("map_id") or "unknown"), int(record.get("segment") or 0))
        groups.setdefault(key, []).append(record)

    outputs: list[dict] = []
    for (map_id, segment), group in sorted(groups.items()):
        group = sorted(group, key=lambda r: int(r.get("i") or 0))
        nodes: list[PatchNode] = []
        for index, record in enumerate(group):
            node = _load_patch_node(index, record, patch_root)
            if node is not None:
                nodes.append(node)
        if not nodes:
            continue
        for index, node in enumerate(nodes):
            node.index = index
        initial = _initial_positions(nodes)
        map_to_world_px = group[0].get("map_to_world_px")
        _sync_anchors(nodes, map_to_world_px, anchor_weight)
        pairs = candidate_pairs(
            initial,
            edge_max_gap=max(1, int(edge_max_gap)),
            spatial_radius=float(spatial_radius),
            spatial_neighbors=max(0, int(spatial_neighbors)),
        )
        edges = _build_edges(
            nodes,
            pairs=pairs,
            edge_max_gap=max(1, int(edge_max_gap)),
            alpha_threshold=float(alpha_threshold),
            min_overlap_px=max(1, int(min_overlap_px)),
            min_response=float(min_response),
            max_residual_px=float(max_residual_px),
        )
        result = solve_pose_graph(
            nodes,
            edges,
            anchor_weight=float(anchor_weight),
            prior_weight=float(prior_weight),
            robust_delta_px=float(robust_delta_px),
            robust_iterations=max(1, int(robust_iterations)),
            max_iter=max(1, int(max_iterations)),
            tol=float(tolerance),
        )
        for edge, residual in zip(result.edges, result.residual_after, strict=False):
            edge.optimized_residual = float(residual)
        canvas, origin, stitch_stats = _stitch_best_patch(
            nodes,
            result.positions,
            margin=max(0, int(margin)),
            max_side=max(64, int(max_side)),
            background=background,
        )
        stem = f"stitch_{_safe_component(map_id)}_s{segment}_aligned"
        path = out_dir / f"{stem}.png"
        imwrite_unicode(path, canvas)
        overlay_path = None
        if draw_overlay:
            overlay_image = canvas.copy()
            _draw_overlay(overlay_image, nodes, result.positions, origin)
            overlay_path = out_dir / f"{stem}_overlay.png"
            imwrite_unicode(overlay_path, overlay_image)
        pose_graph_path = out_dir / f"pose_graph_{_safe_component(map_id)}_s{segment}.json"
        payload = _pose_graph_payload(
            nodes,
            result.positions,
            result.edges,
            result,
            anchor_weight=anchor_weight,
            prior_weight=prior_weight,
        )
        try:
            pose_graph_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pose_graph_path = None
        outputs.append(
            {
                "map_id": map_id,
                "segment": segment,
                "path": str(path),
                "overlay_path": None if overlay_path is None else str(overlay_path),
                "pose_graph_path": None if pose_graph_path is None else str(pose_graph_path),
                "size": stitch_stats["size"],
                "nodes": len(nodes),
                "edges": len(edges),
                "anchors": int(payload["stats"]["anchors"]),
                "placed": stitch_stats["placed"],
                "skipped": stitch_stats["skipped"],
                "clipped": stitch_stats["clipped"],
                "iterations": int(result.iterations),
                "residual_before": payload["stats"]["residual_before"],
                "residual_after": payload["stats"]["residual_after"],
            }
        )
    return outputs
