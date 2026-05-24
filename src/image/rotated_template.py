from typing import Tuple, Optional, List
import math
import cv2
import numpy as np
import os


ARROW_TEMPLATE_FILENAME = "arrow.png"


def _to_rgba(img: np.ndarray) -> np.ndarray:
    if img is None:
        raise ValueError("image is None")
    if img.ndim == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    if img.shape[2] == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    if img.shape[2] == 4:
        return img.copy()
    raise ValueError(f"Unsupported image shape: {img.shape}")


def _safe_roi(img: np.ndarray, x: int, y: int, w: int, h: int) -> Optional[np.ndarray]:
    """安全 ROI 提取：正常返回 view，越界时 padding copy"""
    H, W = img.shape[:2]
    x0 = int(round(x))
    y0 = int(round(y))
    x1 = x0 + int(round(w))
    y1 = y0 + int(round(h))

    if x1 <= 0 or y1 <= 0 or x0 >= W or y0 >= H:
        return None

    x0c = max(0, x0)
    y0c = max(0, y0)
    x1c = min(W, x1)
    y1c = min(H, y1)

    if x0c == x0 and y0c == y0 and x1c == x1 and y1c == y1:
        return img[y0c:y1c, x0c:x1c]

    # 越界 padding
    roi = img[y0c:y1c, x0c:x1c].copy()
    pad_top = y0c - y0
    pad_bottom = y1 - y1c
    pad_left = x0c - x0
    pad_right = x1 - x1c

    if any(p > 0 for p in (pad_top, pad_bottom, pad_left, pad_right)):
        roi = cv2.copyMakeBorder(roi, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_CONSTANT, value=0)
    return roi


def _scale_template(template_rgba: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-9:
        return template_rgba
    h, w = template_rgba.shape[:2]
    new_h = max(1, int(round(h * scale)))
    new_w = max(1, int(round(w * scale)))

    rgb_scaled = cv2.resize(template_rgba[:, :, :3], (new_w, new_h), cv2.INTER_LINEAR)
    alpha_scaled = cv2.resize(template_rgba[:, :, 3], (new_w, new_h), cv2.INTER_NEAREST)

    return cv2.merge([rgb_scaled, alpha_scaled])


def _scale_point(point: Tuple[float, float], scale: float) -> Tuple[float, float]:
    if abs(scale - 1.0) < 1e-9:
        return point
    return (point[0] * scale, point[1] * scale)


class ArrowAngleMatcher:
    """
    高性能旋转箭头角度匹配器（推荐使用版本）
    """

    def __init__(
            self,
            template_path: str | None = None,
            template_center: Tuple[int, int] = None,
            benchmark_width: int = 2560,
            max_cache_scales: int = 12,
    ):

        # 加载模板
        if template_path is None:
            default_paths = [
                os.path.join(os.getcwd(), ARROW_TEMPLATE_FILENAME),
                os.path.join(os.path.dirname(__file__), "..", "..", ARROW_TEMPLATE_FILENAME),
                os.path.join(os.path.dirname(__file__), "..", "..", "assets", ARROW_TEMPLATE_FILENAME),
                os.path.join(os.path.dirname(__file__), "..", "..", "icons", ARROW_TEMPLATE_FILENAME),
            ]
            for p in default_paths:
                if os.path.exists(p):
                    template_path = p
                    break

        if isinstance(template_path, str):
            tpl = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
            if tpl is None:
                raise FileNotFoundError(f"无法加载模板: {template_path}")
        else:
            raise ValueError("必须提供有效的 template_path")

        self.tpl_rgba_orig = _to_rgba(tpl)
        th, tw = self.tpl_rgba_orig.shape[:2]

        # 自动设置模板中心
        if template_center is None or not (0 <= template_center[0] <= tw and 0 <= template_center[1] <= th):
            self.template_center_orig = (tw // 2, th // 2)
        else:
            self.template_center_orig = template_center

        self.benchmark_width = benchmark_width
        self.max_cache_scales = max_cache_scales

        # 缓存
        self._scaled_template_cache = {}  # scale_key -> (tpl_rgba_scaled, center_scaled)
        self._scaled_access = []  # LRU
        self._rotation_cache = {}  # (scale_key, angle_norm) -> (bgr_cropped, alpha_255, bbox, rel_center)

    def _get_scale_key(self, scale: float) -> float:
        return round(scale, 4)

    def _normalize_angle(self, angle: float) -> float:
        return float(angle % 360)

    def _get_scaled_template(self, scale: float):
        scale_key = self._get_scale_key(scale)
        if scale_key in self._scaled_template_cache:
            if scale_key in self._scaled_access:
                self._scaled_access.remove(scale_key)
            self._scaled_access.append(scale_key)
            return self._scaled_template_cache[scale_key]

        tpl_rgba = _scale_template(self.tpl_rgba_orig, scale)
        center = _scale_point(self.template_center_orig, scale)

        self._scaled_template_cache[scale_key] = (tpl_rgba, center)
        self._scaled_access.append(scale_key)

        # LRU 清理
        if len(self._scaled_template_cache) > self.max_cache_scales:
            oldest = self._scaled_access.pop(0)
            del self._scaled_template_cache[oldest]
            self._rotation_cache = {k: v for k, v in self._rotation_cache.items() if k[0] != oldest}

        return tpl_rgba, center

    def _ensure_cache_for_scale_angle(self, scale_key: float, angle: float):
        """确保某个角度的旋转结果已缓存"""
        angle_norm = self._normalize_angle(angle)
        cache_key = (scale_key, angle_norm)

        if cache_key in self._rotation_cache:
            return

        tpl_rgba, template_center = self._get_scaled_template(scale_key)  # 注意这里传入 scale_key 而非 scale
        th, tw = tpl_rgba.shape[:2]

        M = cv2.getRotationMatrix2D(template_center, angle_norm, 1.0)

        rotated_bgr = cv2.warpAffine(
            tpl_rgba[:, :, :3], M, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
        rotated_alpha_chan = cv2.warpAffine(
            tpl_rgba[:, :, 3], M, (tw, th), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        # 基于 alpha 计算紧致 bbox
        alpha_mask = rotated_alpha_chan > 8
        ys, xs = np.where(alpha_mask)

        if ys.size == 0:
            bgr_cropped = rotated_bgr
            alpha_mask_final = np.zeros((th, tw), dtype=np.uint8)
            rel_center = template_center
            bbox = (0, 0, tw, th)
        else:
            y0, y1 = ys.min(), ys.max()
            x0, x1 = xs.min(), xs.max()
            bgr_cropped = rotated_bgr[y0: y1 + 1, x0: x1 + 1].copy()
            alpha_mask_final = ((rotated_alpha_chan[y0: y1 + 1, x0: x1 + 1] > 8) * 255).astype(np.uint8)
            rel_center = (template_center[0] - x0, template_center[1] - y0)
            bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

        self._rotation_cache[cache_key] = (bgr_cropped, alpha_mask_final, bbox, rel_center)

    def _get_angles_with_wrap(self, center_angle: float, radius: float, step: float) -> List[float]:
        """生成环绕角度列表"""
        angles = []
        a = center_angle - radius
        while a <= center_angle + radius + 1e-6:
            angles.append(self._normalize_angle(a))
            a += step
        return sorted(set(angles))

    def _search(
            self, tgt: np.ndarray, center: Tuple[float, float], scale_key: float, angles: List[float]
    ) -> Tuple[float, float]:
        """在给定角度列表中搜索最佳匹配"""
        best_angle = 0.0
        best_score = -float("inf")

        for ang in angles:
            ang_norm = self._normalize_angle(ang)
            cache_key = (scale_key, ang_norm)

            if cache_key not in self._rotation_cache:
                continue

            rotated_bgr, mask, bbox, rel_center = self._rotation_cache[cache_key]
            rw, rh = bbox[2], bbox[3]
            tx = center[0] - rel_center[0]
            ty = center[1] - rel_center[1]

            target_patch = _safe_roi(tgt, tx, ty, rw, rh)
            if target_patch is None:
                continue

            try:
                res = cv2.matchTemplate(target_patch[:, :, :3], rotated_bgr, cv2.TM_CCORR_NORMED, mask=mask)
                score = float(res[0, 0])
            except Exception:
                score = -1.0

            if score > best_score:
                best_angle = ang_norm
                best_score = score

        return best_angle, best_score if best_score > -float("inf") else 0.0

    def match(self, screenshot: np.ndarray, center: Tuple[float, float], two_stage: bool = True) -> Tuple[float, float]:
        """主匹配接口"""
        tgt = _to_rgba(screenshot)
        H, W = tgt.shape[:2]

        scale = W / self.benchmark_width
        scale_key = self._get_scale_key(scale)

        # 标准化中心点
        cx, cy = center
        if isinstance(cx, float) and 0.0 <= cx <= 1.0 and isinstance(cy, float) and 0.0 <= cy <= 1.0:
            center = (cx * W, cy * H)
        else:
            center = (float(cx), float(cy))

        # 粗搜索
        coarse_angles = [float(a) for a in range(0, 360, 10)]
        for ang in coarse_angles:
            self._ensure_cache_for_scale_angle(scale_key, ang)

        best_angle, best_score = self._search(tgt, center, scale_key, coarse_angles)

        if not two_stage or best_score < 0.3:  # 阈值可根据实际情况调整
            return best_angle, best_score

        # 精搜索
        fine_angles = self._get_angles_with_wrap(best_angle, 10.0, 0.5)
        for ang in fine_angles:
            self._ensure_cache_for_scale_angle(scale_key, ang)

        fine_angle, fine_score = self._search(tgt, center, scale_key, fine_angles)

        if fine_score > best_score:
            return fine_angle, fine_score
        return best_angle, best_score


# ====================== 兼容旧接口 ======================
def rotated_template_match(
        target_image: np.ndarray,
        template_image: np.ndarray,
        target_center: Tuple[float, float],
        template_center: Tuple[float, float],
        angle_start: float = 0.0,
        angle_end: float = 360.0,
        angle_step: float = 5.0,
        roi: Optional[Tuple[int, int, int, int]] = None,
        method: int = cv2.TM_CCORR_NORMED,
        template_scale: float = 1.0,
) -> Tuple[float, float, Optional[Tuple[float, float]]]:
    """
    通用旋转模板匹配函数（兼容旧接口）
    
    参数:
      target_image: 目标图像 (BGR/RGBA/Grayscale)
      template_image: 模板图像 (BGR/RGBA/Grayscale)
      target_center: 目标中心 (cx, cy)，支持绝对坐标或 [0..1] 归一化
      template_center: 模板中心 (cx, cy)，支持绝对坐标或 [0..1] 归一化
      angle_start: 搜索起始角度
      angle_end: 搜索结束角度
      angle_step: 搜索步长
      roi: 可选的搜索 ROI (x, y, w, h)
      method: OpenCV 匹配方法，默认 TM_CCORR_NORMED
      template_scale: 模板缩放因子
    
    返回: (best_angle, best_score, best_position)
    """
    tgt = _to_rgba(target_image)
    tpl = _to_rgba(template_image)

    # 按缩放系数调整模板
    if abs(template_scale - 1.0) > 1e-6:
        tpl = _scale_template(tpl, template_scale)
        template_center = _scale_point(template_center, template_scale)

    # ROI 处理
    if roi is not None:
        x, y, w, h = roi
        tgt = _safe_roi(tgt, x, y, w, h)
        if tgt is None:
            return 0.0, 0.0, None
        # 调整目标中心相对于 ROI
        target_center = (target_center[0] - x, target_center[1] - y)

    th, tw = tpl.shape[:2]
    best_angle = 0.0
    best_score = -float("inf")
    best_pos = None

    # 转换中心为绝对坐标
    H, W = tgt.shape[:2]
    tx, ty = target_center
    if isinstance(tx, float) and 0.0 <= tx <= 1.0 and isinstance(ty, float) and 0.0 <= ty <= 1.0:
        tx, ty = tx * W, ty * H

    # 遍历所有角度
    for angle in np.arange(angle_start, angle_end + angle_step / 2, angle_step):
        angle_norm = float(angle % 360)

        # 完成旋转
        M = cv2.getRotationMatrix2D(template_center, angle_norm, 1.0)
        rotated_bgr = cv2.warpAffine(
            tpl[:, :, :3], M, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
        rotated_alpha = cv2.warpAffine(
            tpl[:, :, 3], M, (tw, th), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        # 提取 bbox
        alpha_mask = rotated_alpha > 8
        ys, xs = np.where(alpha_mask)

        if ys.size == 0:
            continue

        y0, y1 = ys.min(), ys.max()
        x0, x1 = xs.min(), xs.max()

        rotated_bgr_crop = rotated_bgr[y0: y1 + 1, x0: x1 + 1].copy()
        alpha_mask_crop = ((rotated_alpha[y0: y1 + 1, x0: x1 + 1] > 8) * 255).astype(np.uint8)
        rel_center = (template_center[0] - x0, template_center[1] - y0)

        # 计算搜索位置
        search_x = tx - rel_center[0]
        search_y = ty - rel_center[1]

        # 提取目标 ROI
        target_patch = _safe_roi(tgt, search_x, search_y, x1 - x0 + 1, y1 - y0 + 1)
        if target_patch is None:
            continue

        try:
            res = cv2.matchTemplate(target_patch[:, :, :3], rotated_bgr_crop, method, mask=alpha_mask_crop)
            score = float(res[0, 0])
        except Exception:
            score = -1.0

        if score > best_score:
            best_angle = angle_norm
            best_score = score
            best_pos = (search_x, search_y)

    return best_angle, best_score if best_score > -float("inf") else 0.0, best_pos


def get_arrow_angle(
        screenshot: np.ndarray,
        center: tuple,
        template_path: str | None = None,
        template_center: tuple = (12, 12),
        angle_start: float = 0.0,
        angle_end: float = 360.0,
        angle_step: float = 5.0,
        benchmark_width: int = 2560,
) -> Tuple[float, float]:
    matcher = ArrowAngleMatcher(
        template_path=template_path, template_center=template_center, benchmark_width=benchmark_width
    )
    return matcher.match(screenshot, center, two_stage=True)
