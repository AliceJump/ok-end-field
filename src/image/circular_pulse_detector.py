"""圆形 UI 循环扩散光圈检测器。

两阶段架构：

阶段 A（校准/缓存）：在调用方给出的“源搜索圆”内模糊定位真实圆形 UI 按钮，
    校准出真实圆心与半径后写入缓存列表。后续调用只要源区域包含某个已缓存
    圆心即直接复用（cache hit），禁止重新搜索圆形；只有缓存明显失效时才重新校准。

阶段 B（脉冲检测）：以真实圆心为原点，逐帧测量按钮的「表观半径」（测量窗口内
    圆周支持度达标的最外层半径）。脉冲辉光出现时按钮包络向外胀大，表观半径
    达到输入推导的阈值（_PULSE_GROW_SCALE × 光圈最大扩散半径）并持续确认帧数，
    即判定脉冲；每次上升沿计一个周期。该标准直接对应真实 UI 动画（辉光贴
    按钮边缘亮起并胀大），不依赖“白色像素”，对 3D 场景背景鲁棒：背景杂乱
    边缘无法在完整圆周上形成高支持度。

对外唯一入口是 :func:`detect_circular_pulse`（模块级共享实例，已校准缓存
跟随应用进程生命周期），只接受归一化四个参数；
所有阈值、采样数、ROI 尺寸均为内部实现细节。坐标按 ``pixel / 边长``、半径按
``pixel / min(width, height)`` 归一化，不绑定任何具体分辨率。
"""

from __future__ import annotations

import enum
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import cv2
import numpy as np

__all__ = [
    "detect_circular_pulse",
    "get_circular_pulse_detector",
    "CalibratedCircle",
    "DetectionState",
    "DetectionResult",
    "CircularPulseDetector",
]

# ---------------------------------------------------------------------------
# 内部常量（全部相对量，禁止出现具体屏幕尺寸）
# ---------------------------------------------------------------------------

# ROI 下采样上限：测量窗口半径在缩放空间不超过该像素数，保证每帧开销恒定。
_MAX_ROI_HALF_PX = 180.0
# 脉冲判定阈值：threshold = _PULSE_GROW_SCALE * effect_max_radius。
# 即「按钮表观半径扩大到接近光圈最大扩散半径」才确认脉冲；
# 阈值完全由调用方输入（normalized_button_radius / normalized_effect_max_radius）推导。
_PULSE_GROW_SCALE = 0.8
_PULSE_ABOVE_FRAMES = 2          # 连续达标帧数 → 确认脉冲
_PULSE_BELOW_FRAMES = 4          # 连续低于帧数 → 清除脉冲
# 周期重武装：表观半径回落到按钮基线的该比例以下视为「已缩回」，立即允许计新周期。
# 光圈动画每圈都从按钮边缘重新胀起；仅靠固定低于帧数会在间隙很短时漏计周期，
# 而基线回落判定对抖动更稳（未真正缩回就不会重复计数）。
_PULSE_REARM_RATIO = 1.12
# 阈值倒挂保护：仅当调用方推导的判定阈值不高于已校准按钮基线（即输入的
# 光圈最大扩散半径比按钮本身还小，脉冲判定在几何上不可能成立）时，
# 把阈值抬升到基线的该比例。生产实测踩坑：effect_max_radius 给小了导致
# 静止表观半径恒高于阈值，所有批次常驻误报 expanding。
_PULSE_MIN_GROW_RATIO = 1.08
# 表观半径测量：在 [min_ratio*r0, 窗口上限] 内取圆周支持度最大的半径。
_PULSE_WINDOW_MIN_RATIO = 0.70
_PULSE_SUPPORT_MIN = 0.60        # 半径入围的最低圆周支持度
_PULSE_ANGLE_SAMPLES = 32
_PULSE_RADII_STEP_RATIO = 0.04   # 半径扫描步长（相对 r0）
_PULSE_CANNY_LO = 20
_PULSE_CANNY_HI = 60
_PULSE_HIST_MAX = 30             # 观测滑动窗口（诊断用途）
_STALE_FRAMES = 40               # 连续测不到按钮多少帧后重置脉冲状态
# 缓存失效判定：按钮内部相对校准参考持续偏移视为 UI 已移动/消失/重绘。
_INVALIDATE_INTERIOR_DIFF = 18.0
_INVALIDATE_SUSTAIN_FRAMES = 15
# 首次圆形定位参数。
_CALIB_SCALE_SAMPLES = 36
_CALIB_MIN_RADIUS_RATIO = 0.30
_CALIB_MAX_RADIUS_RATIO = 3.0
_CALIB_MIN_EDGE_SUPPORT = 0.62
_CALIB_MIN_CIRCULARITY = 0.55    # 4*pi*A/P^2
_CALIB_MIN_FILL = 0.55           # contourArea / (pi*r^2)
_CALIB_MIN_SCORE = 0.62


class DetectionState(enum.Enum):
    """脉冲状态。"""

    IDLE = "idle"                # 无脉冲（按钮处于基线尺寸）
    APPEARING = "appearing"      # 包络开始胀大（未达确认帧数）
    EXPANDING = "expanding"      # 脉冲确认（按钮明显变大）
    NEAR_MAX = "near_max"        # 保留
    RESETTING = "resetting"      # 保留


@dataclass(frozen=True)
class CalibratedCircle:
    """已校准的真实圆形 UI（归一化坐标）。

    ``x``/``y`` 为圆心（``pixel / width``、``pixel / height``），
    ``radius`` 为半径（``pixel / min(width, height)``）。
    """

    x: float
    y: float
    radius: float


@dataclass
class DetectionResult:
    """脉冲检测结果。

    ``detected`` 是上层主要使用的字段；实例支持 ``bool(result)`` 直接等价于
    ``result.detected``。``peak_radius`` 为当帧表观包络半径（归一化）。
    ``actual_*`` 为调试/观察用的已校准圆形参数，调用方无需传入；
    ``pulse_threshold``/``above``/``below`` 为阈值与状态机计数（调试观察用）。
    """

    detected: bool = False
    confidence: float = 0.0
    state: DetectionState = DetectionState.IDLE
    peak_radius: float | None = None     # 表观包络半径（相对实际圆心，归一化）
    cycle_id: int = 0                    # 已确认的脉冲周期数（每次上升沿 +1）
    actual_x: float | None = None
    actual_y: float | None = None
    actual_radius: float | None = None
    angular_consistency: float = 0.0     # 当帧表观半径的圆周支持度
    pulse_threshold: float | None = None  # 当帧脉冲判定阈值（归一化，调试观察用）
    above: int = 0                       # 连续高于阈值帧数（调试观察用）
    below: int = 0                       # 连续低于阈值帧数（调试观察用）
    armed: bool = True                   # 已缩回基线、可计下一个周期（调试观察用）

    def __bool__(self) -> bool:
        return self.detected


@dataclass
class _Stats:
    hits: int = 0
    misses: int = 0
    recalibrations: int = 0
    invalidations: int = 0


class _Tracker:
    """单个已校准圆的脉冲跟踪状态。"""

    __slots__ = (
        "state", "cycle_id", "above", "below", "hist", "armed",
        "last_valid_fidx", "bad_interior",
    )

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = DetectionState.IDLE
        self.cycle_id = 0
        self.above = 0                 # 连续达标帧数
        self.below = 0                 # 连续低于帧数
        self.armed = True              # 是否已缩回基线（允许计下一个周期）
        self.hist: list[tuple[int, float, float]] = []  # (frame_idx, r_px, support)
        self.last_valid_fidx = -10**9
        self.bad_interior = 0


class _Geometry:
    """单个已校准圆的预计算 ROI 几何映射。"""

    __slots__ = (
        "key", "scale", "x0", "y0", "roi_w", "roi_h", "roi_sw", "roi_sh",
        "cx_s", "cy_s", "r0_s", "rmax_s", "interior_flat",
    )

    def __init__(self) -> None:
        self.key: tuple | None = None


class _Entry:
    """缓存列表元素：已校准圆 + 运行时状态。"""

    def __init__(self, circle: CalibratedCircle) -> None:
        self.circle = circle
        self.tracker = _Tracker()
        self.geometry = _Geometry()
        self.ref_interior: np.ndarray | None = None
        self.last_source: tuple[float, float, float] | None = None


def _to_gray(frame: np.ndarray) -> np.ndarray:
    """BGR/BGRA/灰度输入统一转为单通道灰度。"""
    if frame.ndim == 2:
        return frame
    if frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


class CircularPulseDetector:
    """圆形 UI 循环扩散光圈检测器。

    一般应通过 :func:`detect_circular_pulse` 使用进程级共享实例；
    直接实例化仅用于测试隔离::

        detector = CircularPulseDetector()
        result = detector.detect(frame, nx, ny, n_button_r, n_effect_r)
        if result:
            ...
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        # clock 仅用于内部过期判断，可在测试中注入假时钟（默认墙钟）。
        self._clock = clock or time.monotonic
        self._entries: list[_Entry] = []
        self._frame_idx = 0
        self._last_clock = self._clock()
        self.stats = _Stats()
        # 共享实例可能被多个任务线程并发调用，串行化以保护缓存与跟踪状态。
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------

    def detect(
        self,
        frame: np.ndarray,
        normalized_x: float,
        normalized_y: float,
        normalized_button_radius: float,
        normalized_effect_max_radius: float,
    ) -> DetectionResult:
        """检测源区域对应圆形 UI 周围的循环扩散光圈。

        :param frame: BGR/BGRA/灰度截图帧
        :param normalized_x: 源区域中心 X，范围 ``[0, 1]``
        :param normalized_y: 源区域中心 Y，范围 ``[0, 1]``
        :param normalized_button_radius: 源搜索圆半径（归一化，相对短边）
        :param normalized_effect_max_radius: 光圈最大扩散半径（相对真实圆心）
        """
        with self._lock:
            return self._detect_locked(
                frame, normalized_x, normalized_y,
                normalized_button_radius, normalized_effect_max_radius,
            )

    def _detect_locked(
        self,
        frame: np.ndarray,
        normalized_x: float,
        normalized_y: float,
        normalized_button_radius: float,
        normalized_effect_max_radius: float,
    ) -> DetectionResult:
        if frame is None or frame.size == 0:
            raise ValueError("frame must be a non-empty image")
        if not all(0.0 <= v <= 1.0 for v in (normalized_x, normalized_y)):
            raise ValueError("normalized_x/y must be in [0, 1]")
        if normalized_button_radius <= 0 or normalized_effect_max_radius <= 0:
            raise ValueError("radii must be positive")

        height, width = frame.shape[:2]
        min_wh = min(width, height)
        source_cx = normalized_x * width
        source_cy = normalized_y * height
        source_r = normalized_button_radius * min_wh
        effect_r = max(normalized_effect_max_radius * min_wh, source_r * 1.15)
        # 脉冲阈值完全由调用方输入推导（比例见 _PULSE_GROW_SCALE）。
        pulse_threshold = _PULSE_GROW_SCALE * effect_r

        # 时钟推进（FPS 波动仅影响过期判断，不影响序列逻辑）。
        self._frame_idx += 1
        self._last_clock = self._clock()

        entry = self._find_cached(source_cx, source_cy, source_r, width, height)
        if entry is None:
            self.stats.misses += 1
            entry = self._calibrate(frame, source_cx, source_cy, source_r, width, height)
            if entry is None:
                return DetectionResult()
        else:
            self.stats.hits += 1
        entry.last_source = (source_cx, source_cy, source_r)

        return self._detect_pulse(entry, frame, effect_r, pulse_threshold, width, height)

    @property
    def calibrated_circles(self) -> tuple[CalibratedCircle, ...]:
        """当前缓存列表（调试/测试观察用）。"""
        return tuple(entry.circle for entry in self._entries)

    def reset_cache(self) -> None:
        """清空缓存与全部跟踪状态。"""
        self._entries.clear()

    # ------------------------------------------------------------------
    # 阶段 A：缓存查询与首次圆形定位
    # ------------------------------------------------------------------

    def _find_cached(
        self, sx: float, sy: float, sr: float, width: int, height: int,
    ) -> _Entry | None:
        """缓存命中规则：缓存圆心位于源搜索圆内即命中。

        多个命中时选距源中心最近者，半径差作第二级 tie-break。
        """
        best: _Entry | None = None
        best_key: tuple[float, float] | None = None
        # 相对容差吸收归一化往返的浮点误差，保证“圆心恰在源圆圆周上”仍命中。
        limit = sr * (1.0 + 1e-9)
        for entry in self._entries:
            cx = entry.circle.x * width
            cy = entry.circle.y * height
            dist = math.hypot(cx - sx, cy - sy)
            if dist > limit:
                continue
            key = (dist, abs(entry.circle.radius * min(width, height) - sr))
            if best_key is None or key < best_key:
                best, best_key = entry, key
        return best

    def _calibrate(
        self, frame: np.ndarray, sx: float, sy: float, sr: float,
        width: int, height: int,
    ) -> _Entry | None:
        """首次模糊圆形定位：在源搜索圆内寻找稳定、不透明、圆形、连续的 UI 轮廓。"""
        # 真实按钮允许偏离源中心最多 sr、半径最大 _CALIB_MAX_RADIUS_RATIO*sr，
        # 因此窗口必须覆盖 sr*(1+ratio)+margin，否则大按钮轮廓会被裁断。
        needed_half = sr * (1.0 + _CALIB_MAX_RADIUS_RATIO) + 4.0
        scale = min(1.0, _MAX_ROI_HALF_PX / max(needed_half, 1.0))
        x0 = max(0, int(math.floor(sx - needed_half)))
        y0 = max(0, int(math.floor(sy - needed_half)))
        x1 = min(width, int(math.ceil(sx + needed_half)))
        y1 = min(height, int(math.ceil(sy + needed_half)))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None

        roi = _to_gray(frame[y0:y1, x0:x1])
        if scale < 1.0:
            roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(roi, (3, 3), 0)
        edges = cv2.Canny(blurred, 40, 120)

        # 距离变换：每个像素到最近边缘像素的距离，用于向量化的圆周支持度采样。
        dist_map = cv2.distanceTransform((edges > 0).astype(np.uint8) ^ 1, cv2.DIST_L2, 3)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        # 源中心在缩放后 ROI 局部坐标系中的位置（轮廓坐标均为此坐标系）。
        cx_s = (sx - x0) * scale
        cy_s = (sy - y0) * scale
        sr_s = sr * scale
        min_r = max(4.0, _CALIB_MIN_RADIUS_RATIO * sr_s)
        max_r = min(_CALIB_MAX_RADIUS_RATIO * sr_s, 0.9 * max(roi.shape))
        angles = np.linspace(0.0, 2.0 * np.pi, _CALIB_SCALE_SAMPLES, endpoint=False)
        cos_a, sin_a = np.cos(angles), np.sin(angles)
        h_roi, w_roi = roi.shape

        best_score, best = 0.0, None
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < math.pi * min_r * min_r:
                continue
            (ccx, ccy), radius = cv2.minEnclosingCircle(contour)
            radius = float(radius)
            if not min_r <= radius <= max_r:
                continue
            center_dist = math.hypot(ccx - cx_s, ccy - cy_s)
            if center_dist > sr_s:
                continue
            perimeter = cv2.arcLength(contour, True)
            circularity = min(1.0, 4.0 * math.pi * area / max(perimeter * perimeter, 1e-6))
            fill = min(1.0, area / max(math.pi * radius * radius, 1e-6))
            if circularity < _CALIB_MIN_CIRCULARITY or fill < _CALIB_MIN_FILL:
                continue

            # 圆周连续性：沿候选圆周采样，统计落在边缘附近的比例。
            support = self._edge_support(dist_map, ccx, ccy, radius, cos_a, sin_a, w_roi, h_roi)
            if support < _CALIB_MIN_EDGE_SUPPORT:
                continue
            score = (
                0.52 * support
                + 0.22 * circularity
                + 0.08 * fill
                + 0.09 * (1.0 - min(abs(radius - sr_s) / sr_s, 1.0))
                + 0.09 * (1.0 - min(center_dist / sr_s, 1.0))
            )
            if score > best_score:
                best_score = score
                best = (ccx, ccy, radius)

        if best is None or best_score < _CALIB_MIN_SCORE:
            return None

        ccx, ccy, radius = best
        circle = CalibratedCircle(
            x=(x0 + ccx / scale) / width,
            y=(y0 + ccy / scale) / height,
            radius=(radius / scale) / min(width, height),
        )
        # 与既有缓存去重：同一圆形重复校准时刷新原条目而非追加。
        for entry in self._entries:
            ex = entry.circle.x * width
            ey = entry.circle.y * height
            er = entry.circle.radius * min(width, height)
            if math.hypot(ex - circle.x * width, ey - circle.y * height) < 0.6 * max(er, radius / scale):
                entry.circle = circle
                entry.tracker.reset()
                entry.geometry.key = None
                entry.prev_roi = None
                entry.ref_interior = None
                self.stats.recalibrations += 1
                return entry
        entry = _Entry(circle)
        self._entries.append(entry)
        return entry

    @staticmethod
    def _edge_support(
        dist_map: np.ndarray, cx: float, cy: float, radius: float,
        cos_a: np.ndarray, sin_a: np.ndarray, w: int, h: int,
    ) -> float:
        tol = max(1.5, radius * 0.08)
        xs = np.round(cx + radius * cos_a).astype(np.int32)
        ys = np.round(cy + radius * sin_a).astype(np.int32)
        valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        if not valid.any():
            return 0.0
        distances = dist_map[ys[valid], xs[valid]]
        return float(np.count_nonzero(distances <= tol) / len(distances))

    # ------------------------------------------------------------------
    # 阶段 B：按钮包络尺寸（脉冲）检测
    # ------------------------------------------------------------------

    def _detect_pulse(
        self, entry: _Entry, frame: np.ndarray, effect_r: float,
        pulse_threshold: float, width: int, height: int,
    ) -> DetectionResult:
        geometry = self._build_geometry(entry, effect_r, width, height)
        # 先裁小 ROI 再转灰度，避免每帧全图 cvtColor 开销。
        roi = _to_gray(frame[geometry.y0:geometry.y0 + geometry.roi_h,
                             geometry.x0:geometry.x0 + geometry.roi_w])
        if geometry.scale < 1.0:
            roi = cv2.resize(roi, (geometry.roi_sw, geometry.roi_sh),
                             interpolation=cv2.INTER_AREA)
        fidx = self._frame_idx
        tracker = entry.tracker

        # 阈值倒挂保护：调用方推导阈值不高于按钮基线时按基线比例兜底，
        # 否则静止表观半径恒在阈值之上，状态机常驻 expanding（见常量注释）。
        baseline = entry.circle.radius * min(width, height)
        if pulse_threshold <= baseline:
            pulse_threshold = baseline * _PULSE_MIN_GROW_RATIO

        # 过期处理：长时间测不到按钮则重置脉冲状态。
        if fidx - tracker.last_valid_fidx > _STALE_FRAMES:
            tracker.reset()

        # 缓存失效检测：按钮内部相对校准参考持续偏移 → UI 已移动/消失/重绘。
        interior_vals = self._interior_values(roi, geometry)
        if entry.ref_interior is None or entry.ref_interior.shape != interior_vals.shape:
            entry.ref_interior = interior_vals.copy()
        mismatch = float(np.mean(np.abs(interior_vals - entry.ref_interior)))
        if mismatch > _INVALIDATE_INTERIOR_DIFF:
            tracker.bad_interior += 1
        else:
            tracker.bad_interior = 0

        # 表观半径测量与脉冲判定。
        r_t, support = self._measure_radius(geometry, roi)
        if r_t is not None:
            tracker.hist.append((fidx, r_t, support))
            del tracker.hist[:-_PULSE_HIST_MAX]
            tracker.last_valid_fidx = fidx
            self._update_pulse(tracker, r_t, pulse_threshold,
                               baseline * _PULSE_REARM_RATIO)
        else:
            tracker.above = 0
            tracker.below += 1
            if tracker.below >= _PULSE_BELOW_FRAMES and tracker.state is not DetectionState.IDLE:
                tracker.state = DetectionState.IDLE

        # 缓存失效：丢弃缓存条目并用原源区域当帧重新校准。
        if tracker.bad_interior >= _INVALIDATE_SUSTAIN_FRAMES and entry.last_source is not None:
            try:
                self._entries.remove(entry)
            except ValueError:
                pass
            self.stats.invalidations += 1
            sx, sy, sr = entry.last_source
            replacement = self._calibrate(frame, sx, sy, sr, width, height)
            if replacement is not None:
                replacement.last_source = entry.last_source
                entry = replacement

        return self._compose(entry, r_t, support, pulse_threshold, effect_r, width, height)

    def _build_geometry(
        self, entry: _Entry, effect_r: float, width: int, height: int,
    ) -> _Geometry:
        """构建/复用 ROI 几何。key 变化（分辨率或半径变化）时重建。"""
        geometry = entry.geometry
        button_r = entry.circle.radius * min(width, height)
        key = (width, height, round(effect_r), round(button_r, 1))
        if geometry.key == key:
            return geometry

        window_r = max(effect_r, button_r * 1.5)
        scale = min(1.0, _MAX_ROI_HALF_PX / max(window_r, 1.0))
        half = math.ceil(window_r) + 3
        cx = entry.circle.x * width
        cy = entry.circle.y * height
        x0 = max(0, int(cx) - half)
        y0 = max(0, int(cy) - half)
        x1 = min(width, int(cx) + half)
        y1 = min(height, int(cy) + half)
        roi_h, roi_w = y1 - y0, x1 - x0
        roi_sh = max(2, int(round(roi_h * scale)))
        roi_sw = max(2, int(round(roi_w * scale)))

        geometry.key = key
        geometry.scale = scale
        geometry.x0, geometry.y0 = x0, y0
        geometry.roi_w, geometry.roi_h = roi_w, roi_h
        geometry.roi_sw, geometry.roi_sh = roi_sw, roi_sh
        geometry.cx_s = (cx - x0) * scale
        geometry.cy_s = (cy - y0) * scale
        geometry.r0_s = button_r * scale
        geometry.rmax_s = window_r * scale
        ys, xs = np.mgrid[0:roi_sh, 0:roi_sw].astype(np.float32)
        r_map = np.sqrt((xs - geometry.cx_s) ** 2 + (ys - geometry.cy_s) ** 2)
        geometry.interior_flat = (r_map <= button_r * scale * 0.6).ravel()
        return geometry

    def _measure_radius(
        self, geometry: _Geometry, roi: np.ndarray,
    ) -> tuple[float | None, float]:
        """测量按钮表观半径：窗口内圆周支持度达标的最外层半径。

        辉光出现时按钮包络向外胀大，最外层支持半径随之增大。
        返回 (表观半径像素值或 None, 对应支持度)。
        """
        blurred = cv2.GaussianBlur(roi, (3, 3), 0)
        edges = cv2.Canny(blurred, _PULSE_CANNY_LO, _PULSE_CANNY_HI)
        if not np.any(edges):
            return None, 0.0
        dist_map = cv2.distanceTransform((edges > 0).astype(np.uint8) ^ 1, cv2.DIST_L2, 3)

        r0 = geometry.r0_s
        r_lo = r0 * _PULSE_WINDOW_MIN_RATIO
        r_hi = max(r0 * 1.35, min(geometry.rmax_s * 1.05, r0 * 2.0))
        step = max(1.0, _PULSE_RADII_STEP_RATIO * r0)
        radii = np.arange(r_lo, r_hi + step, step)
        if radii.size < 3:
            return None, 0.0
        angles = np.linspace(0.0, 2.0 * np.pi, _PULSE_ANGLE_SAMPLES, endpoint=False)
        cos_a, sin_a = np.cos(angles), np.sin(angles)
        h, w = edges.shape

        xs = np.round(geometry.cx_s + radii[:, None] * cos_a[None, :]).astype(np.int32)
        ys = np.round(geometry.cy_s + radii[:, None] * sin_a[None, :]).astype(np.int32)
        valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        if not valid.any():
            return None, 0.0
        # 容差收紧到接近像素级：过大的相对容差会把采样圆「吸附」到数像素外的
        # 边缘上，使静止表观半径系统性偏大、贴着判定阈值，微扰即误报。
        tol = np.maximum(1.5, radii * 0.02)[:, None]
        d = dist_map[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)]
        hits = ((d <= tol) & valid).sum(axis=1)
        counts = valid.sum(axis=1)
        counts[counts == 0] = 1
        supports = hits / counts

        ok = supports >= _PULSE_SUPPORT_MIN
        if not ok.any():
            return None, 0.0
        idx = int(np.max(np.nonzero(ok)[0]))
        return float(radii[idx] / geometry.scale), float(supports[idx])

    def _update_pulse(
        self, tracker: _Tracker, r_t: float, threshold: float, rearm_r: float,
    ) -> None:
        """按表观半径相对输入推导阈值维护脉冲状态与周期计数。

        上升沿判定：连续达标帧数足够且当前未处于 EXPANDING，即计一个新周期。
        不能要求「处于 IDLE 才计数」——首帧达标时 IDLE 立即被提升为
        APPEARING，该条件永远不可达（历史缺陷：cycle_id 恒为 0）。
        周期计数以「武装」为唯一闸门：包络回落到基线附近（rearm_r）或连续
        多帧低于阈值后重新武装；同一圈膨胀内的反复确认不会重复计数。
        """
        if r_t >= threshold:
            tracker.above += 1
            tracker.below = 0
            if tracker.above >= _PULSE_ABOVE_FRAMES:
                if tracker.armed:
                    tracker.cycle_id += 1  # 武装状态下确认 → 新脉冲周期
                    tracker.armed = False
                tracker.state = DetectionState.EXPANDING
        else:
            tracker.below += 1
            tracker.above = 0
            if r_t <= rearm_r or tracker.below >= _PULSE_BELOW_FRAMES:
                tracker.armed = True
            if tracker.below >= _PULSE_BELOW_FRAMES and tracker.state is not DetectionState.IDLE:
                tracker.state = DetectionState.IDLE  # 回落：脉冲结束
        # 有达标观测但尚未达到确认帧数的中间态。
        if tracker.state is DetectionState.IDLE and tracker.above > 0:
            tracker.state = DetectionState.APPEARING

    @staticmethod
    def _interior_values(roi: np.ndarray, geometry: _Geometry) -> np.ndarray:
        """取按钮内部（r <= 0.6*基线半径）像素值，用于缓存失效比较。"""
        return roi.ravel()[geometry.interior_flat].astype(np.float32)

    def _compose(
        self, entry: _Entry, r_t: float | None, support: float, threshold: float,
        effect_r: float, width: int, height: int,
    ) -> DetectionResult:
        tracker = entry.tracker
        peak_norm = None
        if r_t is not None:
            peak_norm = r_t / min(width, height)
        detected = tracker.state is DetectionState.EXPANDING
        confidence = 0.0
        if detected and r_t is not None:
            span = max(effect_r - threshold, 1e-6)
            growth = min(max((r_t - threshold) / span, 0.0), 1.0)
            confidence = round(min(1.0, 0.5 * support + 0.5 * growth), 4)
        return DetectionResult(
            detected=bool(detected),
            confidence=confidence,
            state=tracker.state,
            peak_radius=peak_norm,
            cycle_id=tracker.cycle_id,
            actual_x=entry.circle.x,
            actual_y=entry.circle.y,
            actual_radius=entry.circle.radius,
            angular_consistency=round(support, 4),
            pulse_threshold=threshold / min(width, height),
            above=tracker.above,
            below=tracker.below,
            armed=tracker.armed,
        )


# ---------------------------------------------------------------------------
# 项目级共享实例：缓存跟随应用进程生命周期
# ---------------------------------------------------------------------------

_shared_lock = threading.Lock()
_shared_detector: CircularPulseDetector | None = None


def get_circular_pulse_detector() -> CircularPulseDetector:
    """返回进程级共享检测器实例。

    已校准圆形缓存保存在该实例内，随应用进程存活：
    一次校准长期复用，跨任务、跨账号执行不丢失。
    """
    global _shared_detector
    if _shared_detector is None:
        with _shared_lock:
            if _shared_detector is None:
                _shared_detector = CircularPulseDetector()
    return _shared_detector


def detect_circular_pulse(
    frame: np.ndarray,
    normalized_x: float,
    normalized_y: float,
    normalized_button_radius: float,
    normalized_effect_max_radius: float,
) -> DetectionResult:
    """圆形 UI 循环扩散光圈检测的项目唯一公开入口。

    在共享实例上执行「缓存查询 → 模糊校准（仅 cache miss）→ 光圈状态机」，
    缓存跟随应用生命周期，调用方无需管理实例。

    :param frame: BGR/BGRA/灰度截图帧
    :param normalized_x: 源区域中心 X，范围 ``[0, 1]``
    :param normalized_y: 源区域中心 Y，范围 ``[0, 1]``
    :param normalized_button_radius: 源搜索圆半径（归一化，相对短边）
    :param normalized_effect_max_radius: 光圈最大扩散半径（相对真实圆心）
    :return: :class:`DetectionResult`，支持 ``bool(result)`` 等价 ``result.detected``
    """
    return get_circular_pulse_detector().detect(
        frame, normalized_x, normalized_y,
        normalized_button_radius, normalized_effect_max_radius,
    )
