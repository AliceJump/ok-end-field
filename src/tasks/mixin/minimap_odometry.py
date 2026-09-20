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
- 朝向角约定（对外）：``read_yaw()`` / ``forward_strafe_m()`` /
  ``body_axes_from_heading()`` 一律用**罗盘方位角**——正北 0°、正东 90°、
  正南 180°、正西 270°，顺时针增大。底层 ``Task.get_arrow_angle()`` 返回的是
  "屏幕上从正北逆时针"的角度（``cv2.getRotationMatrix2D`` 的正角表现为屏幕上
  逆时针），与方位角差一个镜像，由 :func:`arrow_angle_to_bearing` 在入口处统一
  转换，下游不再接触原始箭头角。

单位：位移以"地图系像素"为原生单位（``position_px()``），换算米需要
``scale_m_per_px``（该值应由真值标定得到）。标注 ``m`` 的方法在未提供
比例尺时仍返回像素值并记录警告。

性能：相位相关只用到环带那几个像素，因此采样时先把帧裁到环带外接框
（:func:`minimap_crop_box`，2560x1440 下约占全帧 8.4%）再做 FFT，
位移结果与整帧一致而计算量按面积比下降（实测相位相关 100ms -> 12ms）。
注意裁剪会改变相位相关的 FFT 窗，外扩比例见 ``DEFAULT_CROP_PAD_RATIO``。

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
    "DEFAULT_CROP_PAD_RATIO",
    "DEFAULT_R_OUTER_RATIO",
    "DEFAULT_R_INNER_RATIO",
    "MinimapOdometry",
    "annulus_mask",
    "angle_delta",
    "arrow_angle_to_bearing",
    "bearing_to_arrow_angle",
    "body_axes_from_heading",
    "decompose_body",
    "minimap_crop_box",
    "phase_shift",
    "region_geometry",
    "wrap_deg",
]

# 默认小地图几何（来自 get_arrow_angle 默认中心 + 真机截图实测）：
# 圆心归一化 (0.084, 0.154)，外圈半径约 0.044 * width，内圈(盖住箭头扫掠)约 0.014 * width。
DEFAULT_CENTER_RATIO = (0.084, 0.154)
DEFAULT_R_OUTER_RATIO = 0.044
DEFAULT_R_INNER_RATIO = 0.014
# 环带外接框在外半径之外再留的余量（占外半径比例）。
# 注意：这个值是"安全下限"，不能随便调小。裁剪相当于给相位相关换了个 FFT 窗，
# 窗太小时"完全相同两帧"会解出 ±0.5px 的偏置（实测 pad=1.3 在 1920x1080/1280x720
# 静止时是 (0,-0.5)，pad=5.0 又会出现）；0.5px/0.5s ≈ 0.67 m/s 的假速度会超过
# 融合层的静止阈值 0.2 m/s，导致永远判不了"静止"、再也不重新校准。
# pad=2.0 在 2560x1440 / 1920x1080 / 1280x720 / 640x360 上静止均为严格 0，
# 框面积约占全帧 8.4%（相比整帧仍是约 12 倍的计算量下降）。
DEFAULT_CROP_PAD_RATIO = 2.0


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


def angle_delta(after: float, before: float) -> float:
    """两角之间的最短有向差，归一化到 (-180, 180]。

    例如 ``angle_delta(2, 358) == 4.0``（正确跨越 0/360 边界）。
    """
    return (float(after) - float(before) + 180.0) % 360.0 - 180.0


def minimap_crop_box(
    width: int,
    height: int,
    center_ratio: tuple[float, float] = DEFAULT_CENTER_RATIO,
    r_outer_ratio: float = DEFAULT_R_OUTER_RATIO,
    pad_ratio: float = DEFAULT_CROP_PAD_RATIO,
) -> tuple[int, int, int, int]:
    """小地图环带的外接框 ``(x0, y0, x1, y1)``（整帧坐标，已夹到画面内）。

    相位相关只用到环带那几千个像素，却要在整帧上做 FFT；先按这个框裁剪再相关，
    结果与整帧一致——环带的真实位移（几十像素）远小于框尺寸，不会出现环绕歧义——
    但计算量按面积比下降（2560x1440 实测：框占 8.4%，相位相关 100ms -> 12ms）。

    夹取到画面内是安全的：被夹掉的只是画面外的空白，环带本身仍在框内
    （小地图圆心在外接框内，任何落在画面外的环带像素本来就不存在）。

    Args:
        pad_ratio: 外半径之外再留的余量（占外半径比例），给羽化边缘留空间。
    """
    cx, cy, _r_inner, r_outer = region_geometry(
        width, height, center_ratio, r_outer_ratio)
    pad = r_outer * max(0.0, float(pad_ratio))
    x0 = max(0, int(math.floor(cx - r_outer - pad)))
    y0 = max(0, int(math.floor(cy - r_outer - pad)))
    x1 = min(int(width), int(math.ceil(cx + r_outer + pad)))
    y1 = min(int(height), int(math.ceil(cy + r_outer + pad)))
    if x1 <= x0 or y1 <= y0:
        # 尺寸异常（宽/高为 0 等）：退回整帧，让上层按原来的方式报错/降级
        return 0, 0, int(width), int(height)
    return x0, y0, x1, y1


def arrow_angle_to_bearing(arrow_deg: float) -> float:
    """箭头角（屏幕上从正北逆时针）-> 罗盘方位角（从正北顺时针）。

    ``Task.get_arrow_angle()`` 用 ``cv2.getRotationMatrix2D`` 旋转朝上的箭头模板，
    cv2 正角在屏幕上表现为逆时针，所以它给的 90° 是正西；而日常说的方位角
    90° 是正东。两者互为镜像：``bearing = (360 - arrow) % 360``。
    """
    return (360.0 - float(arrow_deg)) % 360.0


def bearing_to_arrow_angle(bearing_deg: float) -> float:
    """罗盘方位角 -> 箭头角（与 :func:`arrow_angle_to_bearing` 互逆）。"""
    return (360.0 - float(bearing_deg)) % 360.0


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
    convention: str = "compass",
) -> tuple[tuple[float, float], tuple[float, float]]:
    """根据朝向角（罗盘方位角，度）返回地图系下的前向/右向单位向量。

    地图系轴：x 向右（东）、y 向下（南）。``heading_deg`` 是**罗盘方位角**：
    正北 0°、正东 90°、正南 180°、正西 270°，顺时针增大。

    该约定由实机数据钉死（2026-09-10「小地图实时位置」跑测，两个朝向恒定窗口，
    箭头角经 :func:`arrow_angle_to_bearing` 换算为方位角后比对）：
      - 方位 76.5° 时地图系位移方向 80.0°（预测与实测差 3.5°）
      - 方位 289.0° 时地图系位移方向 287.7°（差 1.3°）

    ``convention``：``"compass"``（默认，顺时针方位角）；``"north_up"`` 是历史
    别名，语义相同（保留以免旧调用方失效）。其他值按 ``"compass"`` 处理。

    Returns:
        (fwd, right)：各为 (x, y) 元组，均为单位向量且相互正交；
        right 是玩家**右手**方向（朝北时右手在东，朝东时右手在南）。
    """
    # 罗盘方位：0°=北 -> (0,-1)；90°=东 -> (1,0)；180°=南 -> (0,1)
    fwd = (math.sin(math.radians(heading_deg)), -math.cos(math.radians(heading_deg)))
    # 右手侧：地图系 y 向下，(x,y) -> (-y,x)（朝北时得东，朝东时得南）
    right = (-fwd[1], fwd[0])
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
        crop_pad_ratio: float = DEFAULT_CROP_PAD_RATIO,
        sample_min_dt: float = 0.15,
        # 转向、停车校准等正常控制动作会阻塞数秒。只要相位相关仍然可信，
        # 就不应该仅因为 dt 略长而丢掉整段位移；更大的异常间隔仍会重新锚定。
        sample_max_dt: float = 5.0,
        response_low: float = 0.12,
        max_shift_ratio: float = 0.35,
        max_speed_px_s: float | None = None,
        commit_min_shift_px: float = 0.0,
        scale_m_per_px: float | None = None,
        heading_convention: str = "compass",
    ):
        """创建里程计实例。

        Args:
            task: 提供 ``width``/``height``/``next_frame``/``active_time`` 的任务对象。
            center_ratio: 小地图圆心相对整帧的位置。
            r_outer_ratio: 参与相关的外圈半径，相对画面宽度。
            r_inner_ratio: 排除中心箭头扫掠区的内圈半径，相对画面宽度。
            feather: 环带边缘羽化像素数。
            crop_pad_ratio: crop 框相对外半径额外留出的比例。
            sample_min_dt: 小于该间隔的采样直接跳过，避免对同一帧重复计算。
            sample_max_dt: 超过该间隔视为锚帧过期并重新锚定；正常阻塞时不应触发。
            response_low: 相位相关最低响应，低于该值认为位移不可信。
            max_shift_ratio: 单次位移上限，相对外圈半径。
            max_speed_px_s: 可选速度守卫；``None`` 表示不启用。
            commit_min_shift_px: **位移提交阈值**（像素），``0`` = 关闭（按时间提交，旧行为）。
                开启后只有"锚帧到当前帧的位移"攒够该值才提交一次积分并推进锚帧；
                没攒够就只更新 :meth:`position_px` 里的待提交量、**不换锚帧**。
                误差是**按采样次数**累积的（每样本误差大致是绝对量），而每样本位移 =
                速度 × 采样间隔，所以慢走时每米要积更多样本、漂移更大。阈值让
                "每米提交次数"与走路快慢脱钩，原地小步走则完全不提交。
            scale_m_per_px: 地图像素到世界米的换算比例。
            heading_convention: 朝向约定，当前稳定使用 ``"compass"``。
        """
        self._task = task
        self._center_ratio = center_ratio
        self._r_outer_ratio = r_outer_ratio
        self._r_inner_ratio = r_inner_ratio
        self._feather = feather
        self._crop_pad_ratio = crop_pad_ratio
        self._sample_min_dt = sample_min_dt
        self._sample_max_dt = sample_max_dt
        self._response_low = response_low
        self._max_shift_ratio = max_shift_ratio
        self._max_speed_px_s = max_speed_px_s
        self._commit_min_shift_px = max(0.0, float(commit_min_shift_px))
        self._scale_m_per_px = scale_m_per_px
        self._heading_convention = heading_convention

        self._mask_cache: dict[tuple[int, int], np.ndarray] = {}
        self._box_cache: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        self._anchor_gray: np.ndarray | None = None
        self._anchor_t: float | None = None
        self._pos_px = np.zeros(2, dtype=np.float64)  # 地图系累计位移（像素）
        # 相对当前锚帧、还没攒够 commit_min_shift_px 而不进积分的那部分位移。
        # 只用于对外报告位置（否则慢走时位置会一格一格跳），不计入漂移累积。
        self._pending_px = np.zeros(2, dtype=np.float64)
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
        """环带掩膜——尺寸是**裁剪框**，不是整帧（见 :func:`minimap_crop_box`）。"""
        w, h = self._dimensions()
        key = (w, h)
        if key not in self._mask_cache:
            cx, cy, r_inner, r_outer = region_geometry(
                w, h, self._center_ratio, self._r_outer_ratio, self._r_inner_ratio
            )
            x0, y0, x1, y1 = self._box()
            # 框内建掩膜：圆心换成框内坐标，其余几何参数与整帧版完全一致
            self._mask_cache[key] = annulus_mask(
                y1 - y0, x1 - x0, (cx - x0, cy - y0), r_inner, r_outer,
                feather=self._feather,
            )
        return self._mask_cache[key]

    def _box(self) -> tuple[int, int, int, int]:
        """环带外接框（整帧坐标，按任务分辨率算，与掩膜同一坐标系）。"""
        w, h = self._dimensions()
        key = (w, h)
        if key not in self._box_cache:
            self._box_cache[key] = minimap_crop_box(
                w, h, self._center_ratio, self._r_outer_ratio, self._crop_pad_ratio)
        return self._box_cache[key]

    def _crop_gray(self, frame: np.ndarray) -> np.ndarray | None:
        """把帧裁到环带外接框，返回归一化灰度（框外不参与，省掉整帧 FFT）。"""
        if frame is None:
            return None
        x0, y0, x1, y1 = self._box()
        w, h = self._dimensions()
        if w > 0 and h > 0 and frame.shape[:2] != (h, w):
            # 帧尺寸与任务记录不一致：先整帧缩放到任务尺寸，框与掩膜才是同一坐标系
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        return _to_gray_norm_masked(crop, self._mask())

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
        self._pending_px = np.zeros(2, dtype=np.float64)
        self._anchor_gray = None
        self._anchor_t = None
        self._last = None
        if frame is not None:
            self._arm_anchor(frame, None)

    def reset_position(self):
        """只清零累计位移，保留当前锚帧。"""
        self._pos_px = np.zeros(2, dtype=np.float64)
        # 待提交量也清零：调用方（静止重锚）要的是"位置正好等于锚点"，留着它位置会立刻跳一下
        self._pending_px = np.zeros(2, dtype=np.float64)

    def _arm_anchor(self, frame: np.ndarray, now: float | None):
        gray = self._crop_gray(frame)
        self._anchor_gray = gray
        if now is None:
            now = self._now()
        self._anchor_t = now
        # 换锚帧了：待提交量记的是"相对旧锚帧"的位移，已经失效
        self._pending_px = np.zeros(2, dtype=np.float64)
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
          - ok: 本拍是否得到**可信的**位移测量。注意"可信"不等于"已进积分"——见 committed。
          - sampled: 是否实际做了相位相关（False 表示因时间间隔/锚帧未就绪而跳过）。
          - reason: 采样结果标记：``ok`` / ``shift_too_small``（位移没攒够
            ``commit_min_shift_px``，测量有效但未进积分）/ ``too_soon`` / ``no_frame`` /
            ``no_gray`` / ``anchor_init`` / ``low_response`` / ``exceed_max_shift`` /
            ``too_long_dt`` / ``speed_anomaly``。
          - dx_px / dy_px: 内容从锚帧到当前帧的图像系位移（像素）。
          - dmap_px: 玩家地图系位移 (dx, dy) = (-dx_px, -dy_px)；``shift_too_small``
            时是"相对当前锚帧、还没提交"的那一段。
          - response: 相位相关响应。
          - dt: 距当前锚帧的时间（秒）。``shift_too_small`` 时窗口是完整锚帧区间，
            正好与 dmap_px 配对，所以拿它算平均速度是对的。
          - reanchored: 本次是否因守卫触发而重置锚帧。
          - committed: 本次的 dmap_px 是否真的进了累计积分（``_pos_px``）。
            ``shift_too_small`` 恒为 False；守卫退出路径只有"相关可信"时才 True。
          - benign_reanchor: 换锚是否**良性**（目前仅 ``too_long_dt``）：相关是可信的，
            只是基线到了上限。上层据此决定要不要重新建立信任——把它当成"测坏了"会让
            导航停车等重新校准，而阈值开启后这条路径会经常走到。
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
        # 「相关可不可信」与「锚帧是不是太老」是两件事，退出路径要分开处理（见下）
        trustworthy = math.isfinite(response) and response >= self._response_low
        reanchor_reason = None
        if not trustworthy:
            reanchor_reason = "low_response"
        elif dt >= self._sample_max_dt:
            reanchor_reason = "too_long_dt"
        elif shift_len > max_shift:
            reanchor_reason = "exceed_max_shift"
        elif self._max_speed_px_s is not None and dt > 1e-6:
            px_speed = shift_len / dt
            if px_speed > self._max_speed_px_s:
                reanchor_reason = "speed_anomaly"

        dmap = (-dx_px, -dy_px)

        if reanchor_reason is not None:
            # 开启位移提交阈值后锚帧会活得更久（慢走时要等位移攒够），所以退出路径必须分清：
            #   - 测不准（low_response / exceed_max_shift / speed_anomaly）：这段位移本身就
            #     不可信，丢掉才是对的；
            #   - 只是基线太长（too_long_dt）：相关是好的，**先把这段收下再换锚**。否则位置
            #     会悄悄停止前进，导航会把它当成"卡住"去重规划——这比漂移更难查。
            commit_on_exit = trustworthy and (
                reanchor_reason == "too_long_dt"
                or (self._commit_min_shift_px > 0 and shift_len >= self._commit_min_shift_px)
            )
            if commit_on_exit:
                self._pos_px += np.array(dmap, dtype=np.float64)
            self._arm_anchor(frame, now)
            # too_long_dt 是**良性**换锚：守卫链先判 low_response，能走到它说明相关是
            # 可信的，只是基线到了上限（阈值开启后锚帧会被扣住等位移，这条路径会经常走到）。
            # 上层用它区分"需要重新建立信任"和"只是换了个基线"。
            benign = reanchor_reason == "too_long_dt"
            return self._result(
                ok=False, sampled=True, reason=reanchor_reason,
                dx_px=dx_px, dy_px=dy_px, response=response,
                dmap_px=dmap, dt=dt, reanchored=True,
                committed=commit_on_exit, benign_reanchor=benign,
            )

        # 位移门：没攒够一个"采样单位"就不提交、**也不换锚帧**。
        #
        # 关键在于不换锚帧：待提交量留在"锚帧相对量"里继续攒，下一拍测的还是同一锚帧到
        # 当前的位移，所以这一段不会丢。于是每个**被提交**的样本位移都≈阈值，
        # "每米提交次数"与走路快慢脱钩；原地小步走则完全不提交，漂移不累积。
        #
        # 注意这里必须报 ``sampled=True`` 并更新 ``_last``：这确实是一次**有效测量**
        # （只是没进积分），而 ``is_rest()`` 是靠 ``last_sample()`` 的位移/时间窗算速度的
        # ——报成"没采样"会让静止判定永远拿不到数据，WS 静止校准整个失效。
        # ``dt`` 是"距锚帧"的完整窗口，正好与待提交量配对，算出来的才是真实平均速度。
        if self._commit_min_shift_px > 0 and shift_len < self._commit_min_shift_px:
            self._pending_px = np.array(dmap, dtype=np.float64)
            out = self._result(
                ok=True, sampled=True, reason="shift_too_small",
                dx_px=dx_px, dy_px=dy_px, response=response,
                dmap_px=dmap, dt=dt, reanchored=False,
            )
            out["dmap_m"] = self._to_m(dmap)
            self._last = out
            return out

        # 有效样本：积分玩家地图系位移（像素）-> (-dx, -dy)
        self._pos_px += np.array(dmap, dtype=np.float64)

        # 更新锚帧为当前帧（待提交量随之清零）
        self._anchor_gray = gray
        self._anchor_t = now
        self._pending_px = np.zeros(2, dtype=np.float64)

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
        """返回累计玩家位移（地图系，像素），**含尚未提交的那部分**。

        ``_pos_px`` 只在位移攒够 ``commit_min_shift_px`` 时才前进；把待提交量一起报出去，
        位置才是连续平滑的——否则慢走时位置会一格一格跳，跟随器会画龙。
        待提交量是**同一次**相关的结果、每拍刷新，误差只有一份样本量级（厘米级），
        不进入积分所以也不累积：平滑与"少累积"两者可以兼得。
        """
        total = self._pos_px + self._pending_px
        return (float(total[0]), float(total[1]))

    def position_m(self) -> tuple[float, float]:
        """返回累计玩家位移（地图系，米）。未配置比例尺时返回像素并告警。"""
        total = self._pos_px + self._pending_px
        return self._to_m((float(total[0]), float(total[1])))

    def read_yaw(self) -> tuple[float | None, float]:
        """读取朝向，返回 ``(bearing, score)``。

        ``bearing`` 是**罗盘方位角**（正北 0°、正东 90°、顺时针），由
        :func:`arrow_angle_to_bearing` 从底层箭头角换算而来。
        """
        fn = getattr(self._task, "get_arrow_angle", None)
        if not callable(fn):
            return None, 0.0
        try:
            angle, score = fn(smoothing_threshold=None)
            score = 0.0 if score is None else float(score)
            if angle is None:
                return None, score
            return arrow_angle_to_bearing(angle), score
        except Exception as e:  # noqa: BLE001
            self._log("log_warning", f"read_yaw 失败: {e}")
            return None, 0.0

    def forward_strafe_m(self, heading_deg: float | None = None) -> tuple[float, float]:
        """把累计位移按朝向分解为 前向/右向（米或像素）。

        Args:
            heading_deg: 罗盘方位角（正北 0°、正东 90°）。None 时用最近一次 read_yaw()。
        """
        if heading_deg is None:
            angle, _ = self.read_yaw()
            if angle is None:
                return self.position_m()
            heading_deg = angle
        return decompose_body(self.position_px(), heading_deg, self._scale_m_per_px, self._heading_convention)

    def last_sample(self) -> dict | None:
        """返回最近一次**有效测量**的样本；失败或跳过采样（``too_soon``）时不覆盖该值。

        静止判定依赖这里的字段，因此它与 :meth:`last_result` 的用途不同：
        前者回答"最近一次有效位移是什么"，后者回答"最近一拍尝试结果是什么"。

        开启 ``commit_min_shift_px`` 后，``reason="shift_too_small"`` 的样本也算有效测量
        （位移有效、只是没攒够而不进积分），会出现在这里——否则静止判定拿不到数据，
        WS 静止校准会整个失效。
        """
        return self._last
