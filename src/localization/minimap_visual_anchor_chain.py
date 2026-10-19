"""关键帧视觉锚定链校正：只输出最新位置，不落盘、不截图、不绘图。

导航只需要实时位置。本模块维护最近 ``window_size`` 张小地图切片的灰度/alpha、
局部相对平移边、静止校准 WS 绝对锚点和关键帧视觉传播链，每拍返回最新节点的
锚定链校正位置。

与离线 ``stitch_records`` 共用同一套边测量和 PCG 求解器，不重复维护算法。
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from src.localization.minimap_sample_stitch import (
    PoseGraphEdge,
    measure_edge,
    solve_pose_graph,
    time_gaps,
)

__all__ = [
    "KeyframeNode",
    "KeyframeResult",
    "MinimapKeyframeVisualAnchorChain",
    "MinimapVisualAnchorChainCorrector",
    "VisualAnchorChainNode",
    "VisualAnchorChainResult",
]


@dataclass(slots=True)
class VisualAnchorChainNode:
    """关键帧视觉锚定链校正窗口内的一张切片。"""

    uid: int
    index: int
    time: float
    gray: np.ndarray | None
    alpha: np.ndarray | None
    initial_pos: np.ndarray
    anchor: np.ndarray | None = None
    anchor_weight: float = 0.0
    extra_anchor: np.ndarray | None = None
    extra_anchor_weight: float = 0.0


@dataclass(slots=True)
class VisualAnchorChainResult:
    """关键帧视觉锚定链校正每拍的输出。"""

    valid: bool
    reason: str
    position: np.ndarray
    raw_position: np.ndarray
    correction_px: np.ndarray
    correction_m: float
    nodes: int
    edges: int
    anchors: int
    iterations: int
    min_response: float
    median_response: float
    residual_median: float
    residual_max: float


class MinimapVisualAnchorChainCorrector:
    """关键帧视觉锚定链校正的局部求解器。线程不安全，按单线程执行器使用。"""

    def __init__(
        self,
        *,
        window_size: int = 60,
        edge_gap: int = 12,
        min_response: float = 0.25,
        min_overlap_px: int = 600,
        max_residual_px: float = 6.0,
        alpha_threshold: float = 0.2,
        anchor_weight: float = 50.0,
        prior_weight: float = 0.1,
        robust_delta_px: float = 3.0,
        robust_iterations: int = 2,
        max_iter: int = 200,
        tolerance: float = 1e-6,
        scale_m_per_px: float | None = None,
        max_correction_m: float = 5.0,
        window_anchor_weight: float = 20.0,
    ):
        self._window_size = max(2, int(window_size))
        self._edge_gap = max(1, int(edge_gap))
        self._min_response = float(min_response)
        self._min_overlap_px = max(1, int(min_overlap_px))
        self._max_residual_px = float(max_residual_px)
        self._alpha_threshold = float(alpha_threshold)
        self._anchor_weight = float(anchor_weight)
        self._prior_weight = float(prior_weight)
        self._robust_delta_px = float(robust_delta_px)
        self._robust_iterations = max(1, int(robust_iterations))
        self._max_iter = max(1, int(max_iter))
        self._tolerance = float(tolerance)
        self._scale_m_per_px = None if scale_m_per_px is None else float(scale_m_per_px)
        self._max_correction_m = max(0.0, float(max_correction_m))
        self._window_anchor_weight = max(0.0, float(window_anchor_weight))
        self._nodes: deque[VisualAnchorChainNode] = deque()
        self._edges: list[PoseGraphEdge] = []
        self._prev_positions: np.ndarray | None = None
        self._window_anchor_pos: np.ndarray | None = None
        self._next_uid = 0
        self._ws_anchor_uid: int | None = None
        self._ws_anchor_pos: np.ndarray | None = None

    def reset(self) -> None:
        """位置源/地图/分辨率变化时清空窗口。"""

        self._nodes.clear()
        self._edges.clear()
        self._prev_positions = None
        self._window_anchor_pos = None
        self._next_uid = 0
        self._ws_anchor_uid = None
        self._ws_anchor_pos = None

    def add(
        self,
        *,
        time: float,
        gray: np.ndarray,
        alpha: np.ndarray,
        initial_pos: Sequence[float],
        anchor: Sequence[float] | None = None,
        keyframe_anchor: Sequence[float] | None = None,
        keyframe_anchor_weight: float = 0.0,
    ) -> VisualAnchorChainResult:
        """加入一拍并返回最新节点的优化位置。

        Args:
            time: 本拍时间戳（仅诊断，不参与求解）。
            gray: 当前小地图环带灰度（与 ``alpha`` 同尺寸）。
            alpha: 当前圆环 mask，0..1。
            initial_pos: 当前融合位置换算到小地图像素后的初值。
            anchor: 本拍如果发生静止校准，传 WS 绝对位置（同样是小地图像素）。
        """

        raw = np.asarray(initial_pos, dtype=np.float64).reshape(2)
        node = VisualAnchorChainNode(
            uid=self._next_uid,
            index=len(self._nodes),
            time=float(time),
            gray=gray,
            alpha=alpha,
            initial_pos=raw,
            anchor=None if anchor is None else np.asarray(anchor, dtype=np.float64).reshape(2),
            anchor_weight=self._anchor_weight if anchor is not None else 0.0,
            extra_anchor=(
                None if keyframe_anchor is None else np.asarray(keyframe_anchor, dtype=np.float64).reshape(2)
            ),
            extra_anchor_weight=float(keyframe_anchor_weight) if keyframe_anchor is not None else 0.0,
        )
        self._next_uid += 1
        if anchor is not None:
            self._ws_anchor_uid = node.uid
            self._ws_anchor_pos = node.anchor.copy()
        new_edges = self._edges_to_previous(node)
        self._edges.extend(new_edges)
        self._nodes.append(node)
        self._drop_excess()

        nodes = list(self._nodes)
        initial_guess = self._initial_guess(nodes, raw)
        anchor_node = None
        anchor_weight = self._anchor_weight
        if self._ws_anchor_uid is not None:
            anchor_node = next(
                (item for item in nodes if item.uid == self._ws_anchor_uid),
                None,
            )
            if anchor_node is None and nodes:
                anchor_node = nodes[0]
                anchor_weight = self._anchor_weight * 0.5
        if anchor_node is None and nodes and self._window_anchor_pos is not None:
            anchor_node = nodes[0]
            anchor_weight = self._window_anchor_weight
            if anchor_node.anchor is None:
                anchor_node.anchor = self._window_anchor_pos
                anchor_node.anchor_weight = anchor_weight
        saved_anchor = anchor_node.anchor if anchor_node is not None else None
        saved_weight = anchor_node.anchor_weight if anchor_node is not None else 0.0
        if anchor_node is not None and self._ws_anchor_pos is not None:
            anchor_node.anchor = self._ws_anchor_pos
            anchor_node.anchor_weight = anchor_weight
        extra_anchors = [
            (index, node.extra_anchor, node.extra_anchor_weight)
            for index, node in enumerate(nodes)
            if node.extra_anchor is not None and node.extra_anchor_weight > 0.0
        ]
        try:
            result = solve_pose_graph(
                nodes,
                self._edges,
                anchor_weight=self._anchor_weight,
                prior_weight=self._prior_weight,
                robust_delta_px=self._robust_delta_px,
                robust_iterations=self._robust_iterations,
                max_iter=self._max_iter,
                tol=self._tolerance,
                initial_positions=initial_guess,
                extra_anchors=extra_anchors or None,
            )
            anchors = sum(1 for item in nodes if item.anchor is not None)
        finally:
            if anchor_node is not None:
                anchor_node.anchor = saved_anchor
                anchor_node.anchor_weight = saved_weight

        positions = result.positions
        optimized = positions[-1]
        correction = optimized - raw
        correction_m = float(np.linalg.norm(correction)) * (self._scale_m_per_px or 1.0)
        responses = [edge.response for edge in new_edges]
        min_response = float(min(responses)) if responses else 0.0
        median_response = float(np.median(responses)) if responses else 0.0
        residuals = result.residual_after
        residual_median = float(np.median(residuals)) if residuals.size else 0.0
        residual_max = float(np.max(residuals)) if residuals.size else 0.0
        if not new_edges:
            valid = False
            reason = "no_edges"
        elif correction_m > self._max_correction_m:
            valid = False
            reason = "correction_too_large"
        else:
            valid = True
            reason = "ok"
        if valid:
            self._prev_positions = positions
            self._window_anchor_pos = positions[0].copy()
        else:
            # 拒绝求解结果时不能把它留作下一拍初值，否则会连续放大异常。
            self._prev_positions = None
            self._window_anchor_pos = None
        return VisualAnchorChainResult(
            valid=valid,
            reason=reason,
            position=optimized if valid else raw.copy(),
            raw_position=raw.copy(),
            correction_px=correction.copy(),
            correction_m=correction_m,
            nodes=len(nodes),
            edges=len(self._edges),
            anchors=anchors,
            iterations=int(result.iterations),
            min_response=min_response,
            median_response=median_response,
            residual_median=residual_median,
            residual_max=residual_max,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _edges_to_previous(self, node: VisualAnchorChainNode) -> list[PoseGraphEdge]:
        edges: list[PoseGraphEdge] = []
        nodes = list(self._nodes)
        previous = [nodes[-gap] for gap in time_gaps(self._edge_gap) if gap <= len(nodes)]
        for prev in previous:
            delta = node.initial_pos - prev.initial_pos
            base = np.floor(delta).astype(np.int64)
            measured = measure_edge(
                prev,
                node,
                base=base,
                alpha_threshold=self._alpha_threshold,
                min_overlap_px=self._min_overlap_px,
                max_correlation_size=160,
            )
            if measured is None:
                continue
            dx, dy, response, overlap_fraction, residual = measured
            if response < self._min_response or residual > self._max_residual_px:
                continue
            gap = max(1, node.index - prev.index)
            time_weight = 1.0 / (1.0 + gap / max(1.0, float(self._edge_gap)))
            weight = (response**2) * overlap_fraction * time_weight
            if weight <= 0.0:
                continue
            edges.append(
                PoseGraphEdge(
                    i=prev.index,
                    j=node.index,
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

    def _drop_excess(self) -> None:
        if len(self._nodes) <= self._window_size:
            return
        if self._prev_positions is not None and len(self._prev_positions) >= 2:
            self._window_anchor_pos = self._prev_positions[1].copy()
        else:
            self._window_anchor_pos = None
        dropped = self._nodes.popleft()
        if self._ws_anchor_uid == dropped.uid:
            if (
                self._prev_positions is not None
                and len(self._prev_positions) >= 2
                and self._nodes
                and self._ws_anchor_pos is not None
            ):
                self._ws_anchor_pos = self._ws_anchor_pos + (self._prev_positions[1] - self._prev_positions[0])
                self._ws_anchor_uid = self._nodes[0].uid
            else:
                self._ws_anchor_uid = None
                self._ws_anchor_pos = None
        self._edges = [edge for edge in self._edges if edge.i != dropped.index and edge.j != dropped.index]
        for edge in self._edges:
            if edge.i > dropped.index:
                edge.i -= 1
            if edge.j > dropped.index:
                edge.j -= 1
        for node in self._nodes:
            if node.index > dropped.index:
                node.index -= 1
        if self._prev_positions is not None:
            if len(self._prev_positions) > 1:
                self._prev_positions = self._prev_positions[1:].copy()
            else:
                self._prev_positions = None

    def _initial_guess(
        self,
        nodes: Sequence[VisualAnchorChainNode],
        raw: np.ndarray,
    ) -> np.ndarray:
        if self._prev_positions is not None and len(self._prev_positions) == len(nodes) - 1:
            return np.vstack([self._prev_positions, raw])
        return np.asarray([node.initial_pos for node in nodes], dtype=np.float64)


@dataclass(slots=True)
class KeyframeNode:
    """关键帧视觉锚定链校正中的一个关键帧节点。"""

    uid: int
    index: int
    time: float
    gray: np.ndarray
    alpha: np.ndarray
    initial_pos: np.ndarray
    anchor: np.ndarray | None = None
    anchor_weight: float = 0.0
    optimized_pos: np.ndarray | None = None


@dataclass(slots=True)
class KeyframeResult:
    """关键帧图每新增一个关键帧后的输出。"""

    valid: bool
    reason: str
    position: np.ndarray
    correction_px: np.ndarray
    correction_m: float
    nodes: int
    edges: int
    anchors: int
    iterations: int
    min_response: float
    median_response: float
    residual_median: float
    residual_max: float


class MinimapKeyframeVisualAnchorChain:
    """跨整个运行的关键帧视觉锚定链校正，用 WS 锚定起点，用视觉边传播绝对基准。"""

    def __init__(
        self,
        *,
        max_keyframes: int = 300,
        max_edges: int = 8,
        min_response: float = 0.3,
        min_overlap_px: int = 400,
        max_residual_px: float = 6.0,
        alpha_threshold: float = 0.2,
        anchor_weight: float = 50.0,
        prior_weight: float = 0.001,
        robust_delta_px: float = 3.0,
        robust_iterations: int = 2,
        max_iter: int = 300,
        tolerance: float = 1e-6,
        scale_m_per_px: float | None = None,
        max_correction_m: float = 30.0,
        keep_patches: int | None = None,
    ):
        self._max_keyframes = max(8, int(max_keyframes))
        self._max_edges = max(1, int(max_edges))
        self._min_response = float(min_response)
        self._min_overlap_px = max(1, int(min_overlap_px))
        self._max_residual_px = float(max_residual_px)
        self._alpha_threshold = float(alpha_threshold)
        self._anchor_weight = float(anchor_weight)
        self._prior_weight = float(prior_weight)
        self._robust_delta_px = float(robust_delta_px)
        self._robust_iterations = max(1, int(robust_iterations))
        self._max_iter = max(1, int(max_iter))
        self._tolerance = float(tolerance)
        self._scale_m_per_px = None if scale_m_per_px is None else float(scale_m_per_px)
        self._max_correction_m = max(0.0, float(max_correction_m))
        self._keep_patches = max(
            self._max_edges * 2,
            int(keep_patches) if keep_patches is not None else self._max_edges * 2,
        )
        self._nodes: list[KeyframeNode] = []
        self._edges: list[PoseGraphEdge] = []
        self._prev_positions: np.ndarray | None = None
        self._persistent_anchor_uid: int | None = None
        self._persistent_anchor_pos: np.ndarray | None = None

    def reset(self) -> None:
        self._nodes.clear()
        self._edges.clear()
        self._prev_positions = None
        self._persistent_anchor_uid = None
        self._persistent_anchor_pos = None

    def add(
        self,
        *,
        uid: int,
        time: float,
        gray: np.ndarray,
        alpha: np.ndarray,
        initial_pos: Sequence[float],
        anchor: Sequence[float] | None = None,
    ) -> KeyframeResult:
        """加入一个关键帧，求解整条视觉链并返回最新关键帧的优化位置。"""

        raw = np.asarray(initial_pos, dtype=np.float64).reshape(2)
        node = KeyframeNode(
            uid=int(uid),
            index=len(self._nodes),
            time=float(time),
            gray=gray,
            alpha=alpha,
            initial_pos=raw,
            anchor=None if anchor is None else np.asarray(anchor, dtype=np.float64).reshape(2),
            anchor_weight=self._anchor_weight if anchor is not None else 0.0,
            optimized_pos=raw.copy(),
        )
        if anchor is not None:
            self._persistent_anchor_uid = node.uid
            self._persistent_anchor_pos = node.anchor.copy()

        new_edges = self._edges_to_previous(node)
        self._edges.extend(new_edges)
        self._nodes.append(node)
        self._drop_excess()

        nodes = list(self._nodes)
        initial_guess = self._initial_guess(nodes, raw)
        anchor_node = None
        anchor_weight = self._anchor_weight
        if self._persistent_anchor_uid is not None:
            anchor_node = next(
                (item for item in nodes if item.uid == self._persistent_anchor_uid),
                None,
            )
            if anchor_node is None and nodes:
                anchor_node = nodes[0]
                anchor_weight = self._anchor_weight * 0.5
        saved_anchor = anchor_node.anchor if anchor_node is not None else None
        saved_weight = anchor_node.anchor_weight if anchor_node is not None else 0.0
        if anchor_node is not None and self._persistent_anchor_pos is not None:
            anchor_node.anchor = self._persistent_anchor_pos
            anchor_node.anchor_weight = anchor_weight
        try:
            result = solve_pose_graph(
                nodes,
                self._edges,
                anchor_weight=self._anchor_weight,
                prior_weight=self._prior_weight,
                robust_delta_px=self._robust_delta_px,
                robust_iterations=self._robust_iterations,
                max_iter=self._max_iter,
                tol=self._tolerance,
                initial_positions=initial_guess,
            )
            anchors = sum(1 for item in nodes if item.anchor is not None)
        finally:
            if anchor_node is not None:
                anchor_node.anchor = saved_anchor
                anchor_node.anchor_weight = saved_weight

        positions = result.positions
        node = nodes[-1]
        optimized = positions[-1]
        correction = optimized - node.initial_pos
        correction_m = float(np.linalg.norm(correction)) * (self._scale_m_per_px or 1.0)
        responses = [edge.response for edge in new_edges]
        residuals = result.residual_after
        if not new_edges:
            valid = False
            reason = "no_edges"
        elif correction_m > self._max_correction_m:
            valid = False
            reason = "correction_too_large"
        else:
            valid = True
            reason = "ok"
        if valid:
            self._prev_positions = positions
            for item, position in zip(nodes, positions, strict=True):
                item.optimized_pos = position.copy()
        else:
            self._prev_positions = None
            node.optimized_pos = node.initial_pos.copy()
        keep_from = max(0, len(self._nodes) - self._keep_patches)
        for item in self._nodes[:keep_from]:
            item.gray = None
            item.alpha = None
        return KeyframeResult(
            valid=valid,
            reason=reason,
            position=node.optimized_pos.copy() if valid else node.initial_pos.copy(),
            correction_px=correction.copy(),
            correction_m=correction_m,
            nodes=len(nodes),
            edges=len(self._edges),
            anchors=anchors,
            iterations=int(result.iterations),
            min_response=float(min(responses)) if responses else 0.0,
            median_response=float(np.median(responses)) if responses else 0.0,
            residual_median=float(np.median(residuals)) if residuals.size else 0.0,
            residual_max=float(np.max(residuals)) if residuals.size else 0.0,
        )

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _edges_to_previous(self, node: KeyframeNode) -> list[PoseGraphEdge]:
        edges: list[PoseGraphEdge] = []
        previous = self._nodes[-self._max_edges :]
        for prev in previous:
            if prev.gray is None or prev.alpha is None:
                continue
            delta = node.initial_pos - prev.initial_pos
            base = np.floor(delta).astype(np.int64)
            measured = measure_edge(
                prev,
                node,
                base=base,
                alpha_threshold=self._alpha_threshold,
                min_overlap_px=self._min_overlap_px,
                max_correlation_size=256,
            )
            if measured is None:
                continue
            dx, dy, response, overlap_fraction, residual = measured
            if response < self._min_response or residual > self._max_residual_px:
                continue
            gap = max(1, node.index - prev.index)
            weight = (response**2) * overlap_fraction / math.sqrt(gap)
            if weight <= 0.0:
                continue
            edges.append(
                PoseGraphEdge(
                    i=prev.index,
                    j=node.index,
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

    def _drop_excess(self) -> None:
        if len(self._nodes) <= self._max_keyframes:
            return
        dropped = self._nodes.pop(0)
        if self._persistent_anchor_uid == dropped.uid:
            if (
                self._prev_positions is not None
                and len(self._prev_positions) >= 2
                and self._nodes
                and self._persistent_anchor_pos is not None
            ):
                self._persistent_anchor_pos = self._persistent_anchor_pos + (
                    self._prev_positions[1] - self._prev_positions[0]
                )
                self._persistent_anchor_uid = self._nodes[0].uid
            else:
                self._persistent_anchor_uid = None
                self._persistent_anchor_pos = None
        for node in self._nodes:
            if node.index > dropped.index:
                node.index -= 1
        self._edges = [edge for edge in self._edges if edge.i != dropped.index and edge.j != dropped.index]
        for edge in self._edges:
            if edge.i > dropped.index:
                edge.i -= 1
            if edge.j > dropped.index:
                edge.j -= 1
        if self._prev_positions is not None:
            if len(self._prev_positions) > 1:
                self._prev_positions = self._prev_positions[1:].copy()
            else:
                self._prev_positions = None

    def _initial_guess(
        self,
        nodes: Sequence[KeyframeNode],
        raw: np.ndarray,
    ) -> np.ndarray:
        if self._prev_positions is not None and len(self._prev_positions) == len(nodes) - 1:
            return np.vstack([self._prev_positions, raw])
        return np.asarray([node.initial_pos for node in nodes], dtype=np.float64)
