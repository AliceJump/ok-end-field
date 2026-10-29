"""影拓丰碑本轮已选择灰条对应的海报动态模板：截取、匹配，不持久化。"""

import cv2
import numpy as np

# 1920×1080 中灰条中心 (124,828)，对应海报裁剪 [x89:159, y562:753]。
# 仅取海报纹理，排除灰条自身、图标与下面的动态状态。
_HALF_WIDTH_RATIO = 35 / 1920
_TOP_OFFSET_RATIO = 266 / 1080
_BOTTOM_GAP_RATIO = 75 / 1080
_MATCH_THRESHOLD = 0.93
_MIN_TEXTURE_STD = 4.0


def crop_poster_template(frame: np.ndarray, center_x: float, center_y: float) -> np.ndarray | None:
    """按每个灰条的中心截取该组合的固定海报纹理。"""
    if frame is None or frame.size == 0:
        return None
    height, width = frame.shape[:2]
    cx, cy = round(center_x), round(center_y)
    half_width = max(2, round(width * _HALF_WIDTH_RATIO))
    x1, x2 = cx - half_width, cx + half_width
    y1 = round(cy - height * _TOP_OFFSET_RATIO)
    y2 = round(cy - height * _BOTTOM_GAP_RATIO)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        return None
    patch = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return gray.copy()


def matches_entered_template(candidate: np.ndarray, entered_templates: list[np.ndarray]) -> bool:
    """匹配任一历史模板即为本轮已进入；弱纹理或尺寸不符时不判已进入。"""
    if candidate is None or not entered_templates or float(candidate.std()) < _MIN_TEXTURE_STD:
        return False
    height, width = candidate.shape[:2]
    margin = min(2, (height - 1) // 2, (width - 1) // 2)
    if margin < 1:
        return False
    for template in entered_templates:
        if template.shape != candidate.shape or float(template.std()) < _MIN_TEXTURE_STD:
            continue
        # 用历史模板内部在候选图里做局部匹配，允许灰条中心约 ±2px 抖动。
        inner = template[margin : height - margin, margin : width - margin]
        score = cv2.matchTemplate(candidate, inner, cv2.TM_CCOEFF_NORMED).max()
        if float(score) >= _MATCH_THRESHOLD:
            return True
    return False


def nearby_poster_templates(candidates: list[tuple], frame_width: int) -> list[np.ndarray]:
    """从第一条灰条向右收集同组近邻；遇到大间隔或跨海报宽度即停。

    candidates 按灰条 x 升序排列，元素为 (GrayBar, 已裁剪模板)。
    选择第一条灰条时，这些模板立即一并加入本轮已尝试集合。
    """
    if not candidates:
        return []
    first_x = previous_x = candidates[0][0].center_x
    max_gap = frame_width * 0.06
    max_span = frame_width * 0.23
    group = []
    for bar, patch in candidates:
        if bar.center_x - previous_x > max_gap or bar.center_x - first_x > max_span:
            break
        group.append(patch.copy())
        previous_x = bar.center_x
    return group


def poster_view_changed(before: np.ndarray, after: np.ndarray) -> bool:
    """判断海报页是否因滚动发生实际变化，忽略灰条检测带。

    仅比较上方海报纹理区域的低分辨率采样以限制 4K CPU 开销。
    阈值是保守初值，需实际录屏确认滚动幅度及动态特效的影响。
    """
    if before is None or after is None or before.shape != after.shape:
        return False
    h, w = before.shape[:2]
    y1, y2 = round(h * 0.16), round(h * 0.72)
    x1, x2 = round(w * 0.03), round(w * 0.97)
    if y2 <= y1 or x2 <= x1:
        return False

    def sample(frame):
        area = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(area, cv2.COLOR_BGR2GRAY) if area.ndim == 3 else area
        return cv2.resize(gray, (160, 80), interpolation=cv2.INTER_AREA).astype(np.float32)

    delta = np.abs(sample(after) - sample(before))
    return bool(delta.mean() >= 2.5 and np.count_nonzero(delta > 8) / delta.size >= 0.05)
