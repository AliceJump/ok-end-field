# -*- coding: utf-8 -*-
"""小地图位移里程计（minimap odometry）。

在"地图固定不随视角转、玩家箭头位于小地图中心"的前提下，通过比较小地图
纹理（当前帧 vs 锚帧）用相位相关估计玩家在世界 XZ 平面（地图系、北向上）
的位移，并做长期积分。设计用于送货/找交互点等需要位移反馈的场景。

约定（对外的坐标系）：
- 图像/屏幕系：x 向右、y 向下（OpenCV 图像坐标）。
- 地图系（世界 XZ，北向上）：以地图纹理移动方向为基准，玩家位移与纹理
  位移相反。默认假设：图像 x 向右 ≈ 世界东，图像 y 向下 ≈ 世界南。
- 相位相关约定：``phaseCorrelate(A, B)`` 返回内容从 A 到 B 的图像位移
  ``(dx, dy)``（A 中 (x,y) 的像素在 B 中出现在 (x+dx, y+dy)）。
  因此玩家位移（地图系像素）= ``(-dx, -dy)``。

单位：位移以"地图系像素"为原生单位（``position_px()``），换算米需要
``scale_m_per_px``（该值应由真值标定得到）。标注 ``m`` 的方法在未提供
比例尺时仍返回像素值并记录警告。

本模块为纯视觉/数学实现，不依赖 ``ok`` 框架，便于做单元测试；任务层通过
传入一个"任务对象"（提供 ``next_frame`` / ``width`` / ``height`` /
``active_time`` / ``log_*`` 等）来驱动。
"""

from __future__ import annotations

import math

import cv2
import numpy as np

__all__ = [
    "DEFAULT_CENTER_RATIO",
    "DEFAULT_R_OUTER_RATIO",
    "DEFAULT_R_INNER_RATIO",
    "MinimapOdometry",
    "annulus_mask",
    "phase_shift",
    "body_axes_from_heading",
    "decompose_body",
    "region_geometry",
    "wrap_deg",
]

# 默认小地图几何（来自 get_arrow_angle 默认中心 + 真机截图实测）：
# 圆心归一化 (0.084, 0.154)，外圈半径约 0.044 * width，内圈(盖住箭头扫掠)约 0.014 * width。
DEFAULT_CENTER_RATIO = (0.084, 0.154)
DEFAULT_R_OUTER_RATIO = 0.044
DEFAULT_R_INNER_RATIO = 0.014


def region_geometry(
    width: int,
    height: int,
    center_ratio: tuple[float, float] = DEFAULT_CENTER_RATIO,
    r_outer_ratio: float = DEFAULT_R_OUTER_RATIO,
    r_inner_ratio: float = DEFAULT_R_INNER_RATIO,
) -> tuple[float, float, float, float]:
    """由画面尺寸与比例算出小地图圆心与内外半径（像素）。

    单一事实来源：里程计建掩膜（``MinimapOdometry._mask``）与「小地图区域检查」
    任务都调用这里，保证"检查任务圈出来的区域"与"实际参与相位相关的像素"
    永远一致——否则这个检查就没有意义。

    Returns:
        (cx, cy, r_inner, r_outer)：圆心像素坐标与内/外半径（像素）。
        半径均按 *宽* 的同一比例换算（圆不为椭圆的假设）。
    """
    cx = float(center_ratio[0]) * float(width)
    cy = float(center_ratio[1]) * float(height)
    r_inner = float(r_inner_ratio) * float(width)
    r_outer = float(r_outer_ratio) * float(width)
    return cx, cy, r_inner, r_outer


def wrap_deg(deg: float) -> float:
    """把角度归一化到 [0, 360)。"""
    return float(deg) % 360.0


def _reraise_control_flow(e: BaseException) -> None:
    """若 e 是框架的控制流异常则原样抛出，否则什么也不做。

    这些异常在 ok 里直接继承 ``Exception``，所以 ``except Exception`` 会把它们
    一起吞掉——而 ``TaskExecutor.execute`` 正是靠它们给任务正常收尾。吞掉的后果是
    任务停不下来、executor 状态错乱。这里只放行这几类，其余仍按业务异常处理。

    延迟导入 ok：本模块的纯数学部分不依赖框架，便于在无框架环境下做单测。
    """
    try:
        from ok import FinishedException, TaskDisabledException, WaitFailedException
    except Exception:  # pragma: no cover - 无框架环境下无从判断，按业务异常处理
        return
    if isinstance(e, (FinishedException, TaskDisabledException, WaitFailedException)):
        raise e


def annulus_mask(
    height: int,
    width: int,
    center: tuple[float, float],
    r_inner: float,
    r_outer: float,
    feather: int = 2,
) -> np.ndarray:
    """构建小地图圆环 mask（0/1 float32，边缘羽化）。

    - 内圈 (r < r_inner)：置 0，遮住玩家箭头扫掠。
    - 环带 (r_inner <= r <= r_outer)：置 1，即做相关的地图纹理区。
    - 外圈 (r > r_outer)：置 0，排除小地图圆盘外的静止场景/HUD。
    - 内外边缘做 ``feather`` 像素的线性过渡，抑制 FFT 泄漏。

    Args:
        height: 图像高度。
        width: 图像宽度。
        center: 圆心 (cx, cy)（图像系像素）。
        r_inner: 内圈半径（像素）。
        r_outer: 外圈半径（像素）。
        feather: 边缘羽化宽度（像素），>=0。

    Returns:
        np.ndarray: float32 的 HxW mask。
    """
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    dist = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2)

    mask = np.zeros((height, width), dtype=np.float32)
    ring = (dist >= r_inner) & (dist <= r_outer)
    mask[ring] = 1.0

    if feather > 0:
        # 内圈硬边 -> 羽化
        inner_soft = np.clip((dist - (r_inner - feather)) / feather, 0.0, 1.0)
        # 外圈硬边 -> 羽化
        outer_soft = np.clip((r_outer + feather - dist) / feather, 0.0, 1.0)
        soft = np.minimum(inner_soft, outer_soft)
        mask = np.where((dist >= r_inner - feather) & (dist <= r_outer + feather), soft, 0.0)
    return mask.astype(np.float32)


def _to_gray_norm_masked(frame: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """把 BGR 帧裁剪到 mask 尺寸并返回归一化的灰度（mask 区域外置 0）。

    归一化：减去环带内均值、除以环带内标准差，抑制光照/迷雾变化对相位相关的干扰。
    """
    if frame is None:
        return None
    if mask is None:
        return None
    h, w = mask.shape[:2]
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    if gray.shape[0] != h or gray.shape[1] != w:
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
    gray = gray.astype(np.float32)
    masked = gray * mask
    # 仅以 mask 环带区域统计
    sel = mask > 0
    if sel.sum() == 0:
        return masked
    mean = float(masked[sel].mean())
    std = float(masked[sel].std())
    if std < 1e-6:
        std = 1.0
    norm = (masked - mean) / std
    return norm * mask


def phase_shift(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    mask: np.ndarray,
) -> tuple[float, float, float]:
    """计算两帧小地图内容位移（图像系像素）。

    两帧都会先经 :func:`_to_gray_norm_masked` 统一处理（转灰度、缩放到 mask 尺寸、
    按环带均值方差归一化）；任一帧为 None 时返回 ``(0, 0, 0)``。

    Returns:
        (dx, dy, response)：内容从 A 到 B 的位移 + 相关响应。
        dx, dy 满足"内容从 A 移动到 B"（A 中 (x,y) -> B 中 (x+dx, y+dy)）。
    """
    a = _to_gray_norm_masked(gray_a, mask)
    b = _to_gray_norm_masked(gray_b, mask)
    if a is None or b is None:
        return 0.0, 0.0, 0.0
    (dx, dy), response = cv2.phaseCorrelate(a, b)
    return float(dx), float(dy), float(response)


def body_axes_from_heading(
    heading_deg: float,
    convention: str = "north_up",
) -> tuple[tuple[float, float], tuple[float, float]]:
    """根据朝向角（小地图箭头角度，度）返回地图系下的前向/右向单位向量。

    地图系轴：x 向右（东）、y 向下（南）。heading_deg 为箭头指向的方位角。

    ``convention`` 定义箭头角度如何映射到世界方向（符号须由 E3 实测后固化）：
      - ``"north_up"`` 默认：0°=正北(图像 y 向上)，90°=正东(图像 x 向右)。
        即 前向 = (sin, -cos)（x=sin, y=-cos），0° 时 (0,-1) = 北(图像上)。
      - ``"y_down_north"``：0°=正北(图像 y 向下轴取负)。语义相同，备用。

    无论哪种约定，返回的 (fwd, right) 都满足：
      - 单位向量（长度 1）
      - 相互正交（right = fwd 顺时针旋转 90°，即 (fwd_y, -fwd_x) 或镜像，保持一致）

    Returns:
        (fwd, right)：各为 (x, y) 元组。
    """
    if convention == "north_up":
        # x=sin(theta), y=-cos(theta)：0° -> (0,-1) 北；90° -> (1,0) 东
        fwd = (math.sin(math.radians(heading_deg)), -math.cos(math.radians(heading_deg)))
        # 右向 = 前向顺时针转 90°（图像 y 向下，顺时针即 (fwd_y, -fwd_x)）
        right = (fwd[1], -fwd[0])
    else:
        # 兜底约定，x=cos, y=sin
        fwd = (math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg)))
        right = (fwd[1], -fwd[0])
    return (float(fwd[0]), float(fwd[1])), (float(right[0]), float(right[1]))


def decompose_body(
    dmap_px: tuple[float, float],
    heading_deg: float,
    scale_m_per_px: float | None = None,
    convention: str = "north_up",
) -> tuple[float, float]:
    """把"地图系位移(像素)"按朝向角分解为 前向/右向（米或像素）。

    Args:
        dmap_px: 地图系位移 (dx, dy)（玩家在世界 XZ 平面的位移，地图系像素）。
        heading_deg: 小地图箭头角度（度）。
        scale_m_per_px: 比例尺（米/像素）。None 时返回像素值。
        convention: 见 :func:`body_axes_from_heading`。

    Returns:
        (forward, right)：沿朝向的前向/右向分量。scale 为 None 时单位为像素。
    """
    fwd, right = body_axes_from_heading(heading_deg, convention)
    forward = dmap_px[0] * fwd[0] + dmap_px[1] * fwd[1]
    strafe = dmap_px[0] * right[0] + dmap_px[1] * right[1]
    if scale_m_per_px is not None and scale_m_per_px > 0:
        return float(forward * scale_m_per_px), float(strafe * scale_m_per_px)
    return float(forward), float(strafe)


class MinimapOdometry:
    """小地图位移里程计：采样 + 锚帧相位相关 + 长期积分。

    需要传入一个"任务对象"``task``，提供：
      - ``width`` / ``height``：游戏帧尺寸（用于确定圆心与 mask）。
      - ``next_frame(*args)``：返回若干 BGR 帧（或 None）。
      - ``active_time()``：当前时间（秒），用于锚帧间隔与速度守卫。
      - 可选 ``log_info`` / ``log_warning`` / ``log_error``。
      - 可选 ``get_arrow_angle(...)``：用于把位移分解为前向/右向。

    纯视觉部分（mask / phase_shift / decompose）为可独立测试的函数。
    """

    def __init__(
        self,
        task,
        *,
        center_ratio: tuple[float, float] = DEFAULT_CENTER_RATIO,
        r_outer_ratio: float = DEFAULT_R_OUTER_RATIO,
        r_inner_ratio: float = DEFAULT_R_INNER_RATIO,
        feather: int = 2,
        sample_min_dt: float = 0.15,
        sample_max_dt: float = 1.5,
        response_low: float = 0.12,
        max_shift_ratio: float = 0.35,
        max_speed_px_s: float | None = None,
        scale_m_per_px: float | None = None,
        heading_convention: str = "north_up",
    ):
        self._task = task
        self._center_ratio = center_ratio
        self._r_outer_ratio = r_outer_ratio
        self._r_inner_ratio = r_inner_ratio
        self._feather = feather
        self._sample_min_dt = sample_min_dt
        self._sample_max_dt = sample_max_dt
        self._response_low = response_low
        self._max_shift_ratio = max_shift_ratio
        self._max_speed_px_s = max_speed_px_s
        self._scale_m_per_px = scale_m_per_px
        self._heading_convention = heading_convention

        self._mask_cache: dict[tuple[int, int], np.ndarray] = {}
        self._anchor_gray: np.ndarray | None = None
        self._anchor_t: float | None = None
        self._pos_px = np.zeros(2, dtype=np.float64)  # 地图系累计位移（像素）
        self._last: dict | None = None
        self._last_result: dict | None = None
        self._scale_warned = False

    # ------------------------------------------------------------------ #
    # 几何
    # ------------------------------------------------------------------ #
    def _dimensions(self) -> tuple[int, int]:
        w = int(getattr(self._task, "width", 0))
        h = int(getattr(self._task, "height", 0))
        return w, h

    def _mask(self) -> np.ndarray:
        w, h = self._dimensions()
        key = (w, h)
        if key not in self._mask_cache:
            cx, cy, r_inner, r_outer = region_geometry(
                w, h, self._center_ratio, self._r_outer_ratio, self._r_inner_ratio
            )
            self._mask_cache[key] = annulus_mask(
                h, w, (cx, cy), r_inner, r_outer, feather=self._feather
            )
        return self._mask_cache[key]

    def _crop_gray(self, frame: np.ndarray) -> np.ndarray | None:
        return _to_gray_norm_masked(frame, self._mask())

    def _log(self, meth, msg: str):
        fn = getattr(self._task, meth, None)
        if callable(fn):
            try:
                fn(msg)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 状态操作
    # ------------------------------------------------------------------ #
    def reset(self, frame: np.ndarray | None = None, *, reset_position: bool = True):
        """重置锚帧，可选清空积分位置。"""
        if reset_position:
            self._pos_px = np.zeros(2, dtype=np.float64)
        self._anchor_gray = None
        self._anchor_t = None
        self._last = None
        if frame is not None:
            self._arm_anchor(frame, None)

    def reset_position(self):
        self._pos_px = np.zeros(2, dtype=np.float64)

    def _arm_anchor(self, frame: np.ndarray, now: float | None):
        gray = self._crop_gray(frame)
        self._anchor_gray = gray
        if now is None:
            now = self._now()
        self._anchor_t = now
        self._last = None

    def _now(self) -> float:
        fn = getattr(self._task, "active_time", None)
        if callable(fn):
            try:
                return float(fn())
            except Exception:
                pass
        return 0.0

    # ------------------------------------------------------------------ #
    # 采样
    # ------------------------------------------------------------------ #
    def sample(self, frame: np.ndarray | None = None, *, now: float | None = None) -> dict:
        """采样一拍位移并积分，并把本次结果记录到 :meth:`last_result`。

        薄包装：调用方需要在不改变返回值的前提下回看"本拍到底采到没有"，用它区分
        「位置在动」和「位置源已经没有数据了」。
        """
        out = self._sample_impl(frame=frame, now=now)
        self._last_result = out
        return out

    def last_result(self) -> dict | None:
        """最近一次采样结果（含 ``sampled=False``/``ok=False`` 的失败拍）；从未采样为 None。"""
        return self._last_result

    def _sample_impl(self, frame: np.ndarray | None = None, *, now: float | None = None) -> dict:
        """采样一拍位移并积分。返回本次结果字典，永不抛出。

        返回字典字段：
          - ok: 本次是否得到有效的位移样本并积分。
          - sampled: 是否实际做了相位相关（False 表示因时间间隔/锚帧未就绪而跳过）。
          - reason: ``ok=False`` 或 ``sampled=False`` 的原因标记。
          - dx_px / dy_px: 内容从锚帧到当前帧的图像系位移（像素）。
          - dmap_px: 玩家地图系位移 (dx, dy) = (-dx_px, -dy_px)。
          - response: 相位相关响应。
          - dt: 采样间隔（秒）。
          - reanchored: 本次是否因守卫触发而重置锚帧。
          - dmap_m: 玩家地图系位移（米，未配置比例尺时为像素值）。
        """
        if frame is None:
            try:
                frame = self._task.next_frame()
            except Exception as e:  # noqa: BLE001
                _reraise_control_flow(e)   # 任务被禁用/结束必须放行，不能吞
                self._log("log_warning", f"minimap_odometry next_frame 失败: {e}")
                frame = None
        if frame is None:
            return self._result(ok=False, sampled=False, reason="no_frame", reanchored=False)

        gray = self._crop_gray(frame)
        if gray is None:
            return self._result(ok=False, sampled=False, reason="no_gray", reanchored=False)

        now = self._now() if now is None else now

        if self._anchor_gray is None:
            self._arm_anchor(frame, now)
            return self._result(
                ok=True, sampled=False, reason="anchor_init",
                dx_px=0.0, dy_px=0.0, response=1.0, dmap_px=(0.0, 0.0),
                dt=0.0, reanchored=False,
            )

        anchor_t = self._anchor_t if self._anchor_t is not None else now
        dt = now - anchor_t
        if dt < self._sample_min_dt:
            return self._result(ok=True, sampled=False, reason="too_soon", dt=dt)

        dx_px, dy_px, response = phase_shift(self._anchor_gray, gray, self._mask())

        shift_len = math.hypot(dx_px, dy_px)
        w, _ = self._dimensions()
        max_shift = self._max_shift_ratio * (self._r_outer_ratio * w)
        reanchor_reason = None
        if not math.isfinite(response) or response < self._response_low:
            reanchor_reason = "low_response"
        elif dt >= self._sample_max_dt:
            reanchor_reason = "too_long_dt"
        elif shift_len > max_shift:
            reanchor_reason = "exceed_max_shift"
        elif self._max_speed_px_s is not None and dt > 1e-6:
            px_speed = shift_len / dt
            if px_speed > self._max_speed_px_s:
                reanchor_reason = "speed_anomaly"

        if reanchor_reason is not None:
            self._arm_anchor(frame, now)
            return self._result(
                ok=False, sampled=True, reason=reanchor_reason,
                dx_px=dx_px, dy_px=dy_px, response=response,
                dmap_px=(-dx_px, -dy_px), dt=dt, reanchored=True,
            )

        # 有效样本：积分玩家地图系位移（像素）-> (-dx, -dy)
        dmap = (-dx_px, -dy_px)
        self._pos_px += np.array(dmap, dtype=np.float64)

        # 更新锚帧为当前帧
        self._anchor_gray = gray
        self._anchor_t = now

        out = self._result(
            ok=True, sampled=True, reason="ok",
            dx_px=dx_px, dy_px=dy_px, response=response,
            dmap_px=dmap, dt=dt, reanchored=False,
        )
        out["dmap_m"] = self._to_m(dmap)
        self._last = out
        return out

    def _to_m(self, dmap_px: tuple[float, float]) -> tuple[float, float]:
        if self._scale_m_per_px is not None and self._scale_m_per_px > 0:
            return (dmap_px[0] * self._scale_m_per_px, dmap_px[1] * self._scale_m_per_px)
        if not self._scale_warned:
            self._scale_warned = True
            self._log("log_warning", "minimap_odometry 未配置比例尺 scale_m_per_px，返回像素位移")
        return (float(dmap_px[0]), float(dmap_px[1]))

    @staticmethod
    def _result(**kwargs) -> dict:
        base = {
            "ok": False,
            "sampled": False,
            "reason": "ok",
            "dx_px": 0.0,
            "dy_px": 0.0,
            "response": 0.0,
            "dmap_px": (0.0, 0.0),
            "dmap_m": (0.0, 0.0),
            "dt": 0.0,
            "reanchored": False,
        }
        base.update(kwargs)
        return base

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def position_px(self) -> tuple[float, float]:
        """返回累计玩家位移（地图系，像素）。"""
        return (float(self._pos_px[0]), float(self._pos_px[1]))

    def position_m(self) -> tuple[float, float]:
        """返回累计玩家位移（地图系，米）。未配置比例尺时返回像素并告警。"""
        return self._to_m((float(self._pos_px[0]), float(self._pos_px[1])))

    def read_yaw(self) -> tuple[float | None, float]:
        """读取小地图箭头角度（方向）。返回 (angle, score)。"""
        fn = getattr(self._task, "get_arrow_angle", None)
        if not callable(fn):
            return None, 0.0
        try:
            angle, score = fn(smoothing_threshold=None)
            return angle, 0.0 if score is None else float(score)
        except Exception as e:  # noqa: BLE001
            self._log("log_warning", f"read_yaw 失败: {e}")
            return None, 0.0

    def forward_strafe_m(self, heading_deg: float | None = None) -> tuple[float, float]:
        """把累计位移按朝向角分解为 前向/右向（米或像素）。

        Args:
            heading_deg: 小地图箭头角度。None 时用最近一次 read_yaw()。
        """
        if heading_deg is None:
            angle, _ = self.read_yaw()
            if angle is None:
                return self.position_m()
            heading_deg = angle
        return decompose_body(self.position_px(), heading_deg, self._scale_m_per_px, self._heading_convention)

    def last_sample(self) -> dict | None:
        return self._last

    def set_scale(self, scale_m_per_px: float | None):
        self._scale_m_per_px = scale_m_per_px
        self._scale_warned = False

    def scale(self) -> float | None:
        return self._scale_m_per_px
