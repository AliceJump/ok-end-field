"""推荐技能按钮「白色圆周」脉冲检测器。

游戏内推荐释放时机表现为：按钮同心环从内向外为——

1. 最内层：深色细描边（约 hsl(220, 16%, 11%)）；
2. 中间信号层：常态灰蓝（约 hsl(222, 4%, 45%)）细环，
   推荐释放时被带辉光的粗纯白环（hsl(0, 0%, 100%)）覆盖；
3. 最外层：淡灰细环。

注意按钮面内部含白色图标与亮色扇形段，因此任何「内侧锚定」
（要求信号层以内保持深色）都不可靠——本检测器只依赖信号层本身：

- **信号层白色占比**：在 ``_SIGNAL_BAND`` 半径带内统计白色角度占比，
  白色判定为 HSV ``S ≤ _WHITE_S_MAX`` 且 ``V ≥ _WHITE_V_MIN``
  （常态 V≈118、彩色特效高饱和均被排除）；
- 真脉冲时白环带辉光、占比接近 1.0；局部飞过的特效难以盖满半圈，
  ``_ON_RATIO`` 的全圆覆盖要求即可过滤；
- 迟滞去抖：确认后保持到占比连续回落才复位，仅在上升沿返回 True。

线程安全：提供进程级共享实例 :func:`get_recommend_skill_detector`。
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

__all__ = [
    "PULSE_ON_RATIO",
    "RecommendSkillDetector",
    "get_recommend_skill_detector",
]

_WHITE_S_MAX = 70  # 饱和度上限：白色低饱和；彩色 VFX 高饱和被排除
_WHITE_V_MIN = 200  # 明度下限：常态圆周 V≈118 被排除，脉冲白接近 255
_ANGLE_SAMPLES = 64  # 圆周角度采样数
_SIGNAL_BAND = (0.93, 1.12)  # 中间信号层半径带（相对配置半径，含辉光外扩）
_ON_RATIO = 0.80  # 白角占比确认阈值（定稿值；实测脉冲相位占比 0.59~0.94）
PULSE_ON_RATIO = _ON_RATIO  # 公开确认阈值：供调用方复用当帧占比做全屏闪光判定，避免重复计算
_OFF_FRAMES = 3  # 连续低于确认阈值帧数后复位（防止噪声占比卡在迟滞中区）


class _Track:
    """单个按钮的迟滞去抖状态。"""

    __slots__ = ("active", "hits", "misses")

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.active = False


class RecommendSkillDetector:
    """维护各按钮去抖状态；:meth:`detect` 仅在新脉冲周期的上升沿返回 True。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._tracks: dict[str, _Track] = {}

    def reset(self) -> None:
        """清空全部按钮状态。"""
        with self._lock:
            self._tracks.clear()

    def reset_label(self, label: str) -> None:
        """清除单个按钮状态。

        用于全屏闪光过滤等场景：detect 已把该标签置为 active 但上层决定忽略
        本次命中，复位后紧接出现的真实脉冲仍能重新产生上升沿。
        """
        with self._lock:
            self._tracks.pop(label, None)

    @staticmethod
    def _white_ratio(frame: np.ndarray, cx_n: float, cy_n: float, r_n: float) -> float:
        """计算信号层半径带内的白色角度占比（0~1）。"""
        height, width = frame.shape[:2]
        min_wh = min(width, height)
        cx = cx_n * width
        cy = cy_n * height
        r0 = r_n * min_wh

        outer = _SIGNAL_BAND[1] * r0 + 3
        half = int(outer) + 3
        x0 = max(0, int(cx) - half)
        y0 = max(0, int(cy) - half)
        x1 = min(width, int(cx) + half)
        y1 = min(height, int(cy) + half)
        if x1 - x0 < 6 or y1 - y0 < 6:
            return 0.0
        roi = frame[y0:y1, x0:x1]
        if roi.ndim == 2:
            return 0.0
        if roi.shape[2] == 4:
            roi = cv2.cvtColor(roi, cv2.COLOR_BGRA2BGR)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        white = (hsv[:, :, 1] <= _WHITE_S_MAX) & (hsv[:, :, 2] >= _WHITE_V_MIN)

        angles = np.linspace(0.0, 2.0 * np.pi, _ANGLE_SAMPLES, endpoint=False)
        cos_a, sin_a = np.cos(angles), np.sin(angles)
        lx, ly = cx - x0, cy - y0
        h, w = white.shape
        hit = np.zeros(_ANGLE_SAMPLES, dtype=bool)
        for rr in np.arange(_SIGNAL_BAND[0] * r0, _SIGNAL_BAND[1] * r0 + 1.0, 1.0):
            xs = np.round(lx + rr * cos_a).astype(np.int32)
            ys = np.round(ly + rr * sin_a).astype(np.int32)
            valid = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
            if not valid.any():
                continue
            hit |= valid & white[ys.clip(0, h - 1), xs.clip(0, w - 1)]
        return float(np.count_nonzero(hit)) / _ANGLE_SAMPLES

    def white_ratio(self, frame: np.ndarray, cx_n: float, cy_n: float, r_n: float) -> float:
        """查询当帧信号层白色角度占比（不改变任何状态，供诊断日志使用）。"""
        return self._white_ratio(frame, cx_n, cy_n, r_n)

    def is_pulsing(self, frame: np.ndarray, cx_n: float, cy_n: float, r_n: float) -> bool:
        """当帧该按钮信号层是否达到白色脉冲确认占比（不改变任何状态）。

        用于全屏闪光判定：需要「当前是否全白」而非「是否新上升沿」，
        已 active 的标签不会产生上升沿，不能用 detect 的返回值判断。
        """
        return self._white_ratio(frame, cx_n, cy_n, r_n) >= _ON_RATIO

    def detect_ratio(self, white_ratio: float, label: str) -> bool:
        """基于已计算的占比推进迟滞去抖状态机。

        供同帧多用途场景（诊断 / 上升沿 / 闪光过滤共用一次占比计算）复用，
        与 :meth:`detect` 的状态机语义完全一致。

        :param white_ratio: 该区域当帧白色角度占比（0~1，见 :meth:`white_ratio`）
        :param label: 状态键（通常为区域名，如「批次1」）
        :return: 仅当本次调用构成新脉冲周期（上升沿）时为 True
        """
        with self._lock:
            track = self._tracks.setdefault(label, _Track())
            if white_ratio >= _ON_RATIO:
                track.hits += 1
                track.misses = 0
                if not track.active:
                    track.active = True
                    return True
            else:
                # 只要低于确认阈值累计数帧即复位：实战中特效会把占比托在
                # 迟滞中区，若要求降到更低阈值会导致 active 卡死、漏掉后续脉冲。
                track.misses += 1
                track.hits = 0
                if track.misses >= _OFF_FRAMES:
                    track.active = False
            return False

    def detect(self, frame: np.ndarray, cx_n: float, cy_n: float, r_n: float, label: str) -> bool:
        """检测指定按钮是否出现白色圆周脉冲上升沿。

        :param frame: BGR 截图帧
        :param cx_n: 按钮中心 X（pixel / 宽）
        :param cy_n: 按钮中心 Y（pixel / 高）
        :param r_n: 按钮参考半径（pixel / 短边）
        :param label: 状态键（通常为区域名，如「批次1」）
        :return: 仅当本次调用构成新脉冲周期（上升沿）时为 True
        """
        return self.detect_ratio(self._white_ratio(frame, cx_n, cy_n, r_n), label)


_shared_lock = threading.Lock()
_shared_detector: RecommendSkillDetector | None = None


def get_recommend_skill_detector() -> RecommendSkillDetector:
    """返回进程级共享实例，状态随应用生命周期存活。"""
    global _shared_detector
    if _shared_detector is None:
        with _shared_lock:
            if _shared_detector is None:
                _shared_detector = RecommendSkillDetector()
    return _shared_detector
