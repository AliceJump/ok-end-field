import os
from collections import OrderedDict

import cv2
import numpy as np

ARROW_TEMPLATE_FILENAME = "arrow.png"

# 量化精度
_SCALE_FACTOR = 10000  # 4 位小数
_ANGLE_FACTOR = 1000  # 3 位小数（deg）


def _quantize_scale(scale: float) -> int:
    """将浮点缩放量化为整数 key，避免浮点字典键误差。"""
    return int(round(float(scale) * _SCALE_FACTOR))


def _dequantize_scale(scale_q: int) -> float:
    return scale_q / _SCALE_FACTOR


def _quantize_angle(angle: float) -> int:
    """归一化到 [0,360) 后量化为整数 key。"""
    return int(round(float(angle % 360) * _ANGLE_FACTOR))


def _dequantize_angle(angle_q: int) -> float:
    return angle_q / _ANGLE_FACTOR


def _is_identity_scale(scale: float) -> bool:
    return abs(float(scale) - 1.0) < 1e-9


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


def _safe_roi(img: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray | None:
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
    if _is_identity_scale(scale):
        return template_rgba
    h, w = template_rgba.shape[:2]
    new_h = max(1, int(round(h * scale)))
    new_w = max(1, int(round(w * scale)))

    rgb_scaled = cv2.resize(template_rgba[:, :, :3], (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    alpha_scaled = cv2.resize(template_rgba[:, :, 3], (new_w, new_h), interpolation=cv2.INTER_NEAREST)

    return np.dstack([rgb_scaled, alpha_scaled])


def _scale_point(point: tuple[float, float], scale: float) -> tuple[float, float]:
    if _is_identity_scale(scale):
        return point
    return (point[0] * scale, point[1] * scale)


class ArrowAngleMatcher:
    """
    高性能旋转箭头角度匹配器（推荐使用版本）
    """

    def __init__(
        self,
        template_path: str | None = None,
        template_center: tuple[int, int] = None,
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

        # LRU 缓存
        # scale_q(int) -> (tpl_rgba_scaled, center_scaled)
        self._scaled_template_cache: OrderedDict[int, tuple[np.ndarray, tuple[float, float]]] = OrderedDict()
        # (scale_q, angle_q) -> (bgr_cropped, alpha_255, bbox, rel_center)
        self._rotation_cache: dict[
            tuple[int, int], tuple[np.ndarray, np.ndarray, tuple[int, int, int, int], tuple[float, float]]
        ] = {}

    def _get_scaled_template(self, scale: float):
        """获取缩放模板，内部使用量化整数 key 实现 LRU。"""
        scale_q = _quantize_scale(scale)
        if scale_q in self._scaled_template_cache:
            self._scaled_template_cache.move_to_end(scale_q)
            return self._scaled_template_cache[scale_q]

        scale_eff = _dequantize_scale(scale_q)
        tpl_rgba = _scale_template(self.tpl_rgba_orig, scale_eff)
        center = _scale_point(self.template_center_orig, scale_eff)

        self._scaled_template_cache[scale_q] = (tpl_rgba, center)
        # LRU 清理
        if len(self._scaled_template_cache) > self.max_cache_scales:
            oldest_q, _ = self._scaled_template_cache.popitem(last=False)
            # 同步清理该 scale 下的所有旋转缓存
            self._rotation_cache = {k: v for k, v in self._rotation_cache.items() if k[0] != oldest_q}

        return tpl_rgba, center

    def _ensure_cache_for_scale_angle(self, scale: float, angle: float):
        """确保某个角度的旋转结果已缓存；scale 可为原始浮点或已量化值。"""
        # 兼容调用方传入已量化的 scale_key(float) 或原始 scale
        scale_q = _quantize_scale(scale)
        angle_q = _quantize_angle(angle)
        cache_key = (scale_q, angle_q)

        if cache_key in self._rotation_cache:
            return

        tpl_rgba, template_center = self._get_scaled_template(scale)
        th, tw = tpl_rgba.shape[:2]
        angle_eff = _dequantize_angle(angle_q)

        M = cv2.getRotationMatrix2D(template_center, angle_eff, 1.0)

        rotated_bgr = cv2.warpAffine(
            tpl_rgba[:, :, :3], M, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
        rotated_alpha_chan = cv2.warpAffine(
            tpl_rgba[:, :, 3], M, (tw, th), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        # 基于 alpha 计算紧致 bbox
        alpha_mask = rotated_alpha_chan > 8
        ys, xs = np.nonzero(alpha_mask)

        if ys.size == 0:
            bgr_cropped = rotated_bgr
            alpha_mask_final = np.zeros((th, tw), dtype=np.uint8)
            rel_center = template_center
            bbox = (0, 0, tw, th)
        else:
            y0, y1 = ys.min(), ys.max()
            x0, x1 = xs.min(), xs.max()
            bgr_cropped = rotated_bgr[y0 : y1 + 1, x0 : x1 + 1].copy()
            alpha_mask_final = ((rotated_alpha_chan[y0 : y1 + 1, x0 : x1 + 1] > 8) * 255).astype(np.uint8)
            rel_center = (template_center[0] - x0, template_center[1] - y0)
            bbox = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

        self._rotation_cache[cache_key] = (bgr_cropped, alpha_mask_final, bbox, rel_center)

    def _get_angles_with_wrap(self, center_angle: float, radius: float, step: float) -> list[float]:
        """生成环绕角度列表，量化去重后排序。"""
        n = int(round(2 * radius / step)) + 1
        raw = [center_angle - radius + i * step for i in range(n)]
        # 量化去重（处理浮点累积误差与跨 0/360 边界）
        seen: dict[int, float] = {}
        for a in raw:
            q = _quantize_angle(a)
            if q not in seen:
                seen[q] = _dequantize_angle(q)
        return sorted(seen.values())

    def _search(
        self, tgt: np.ndarray, center: tuple[float, float], scale: float, angles: list[float]
    ) -> tuple[float, float]:
        """在给定角度列表中搜索最佳匹配"""
        best_angle = 0.0
        best_score = -float("inf")
        scale_q = _quantize_scale(scale)

        for ang in angles:
            angle_q = _quantize_angle(ang)
            cache_key = (scale_q, angle_q)

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

            ang_eff = _dequantize_angle(angle_q)
            if score > best_score:
                best_angle = ang_eff
                best_score = score

        return best_angle, best_score if best_score > -float("inf") else 0.0

    def match(self, screenshot: np.ndarray, center: tuple[float, float], two_stage: bool = True) -> tuple[float, float]:
        """主匹配接口"""
        tgt = _to_rgba(screenshot)
        H, W = tgt.shape[:2]

        scale = W / self.benchmark_width

        # 标准化中心点
        cx, cy = center
        if isinstance(cx, float) and 0.0 <= cx <= 1.0 and isinstance(cy, float) and 0.0 <= cy <= 1.0:
            center = (cx * W, cy * H)
        else:
            center = (float(cx), float(cy))

        # 粗搜索
        coarse_angles = [float(a) for a in range(0, 360, 10)]
        for ang in coarse_angles:
            self._ensure_cache_for_scale_angle(scale, ang)

        best_angle, best_score = self._search(tgt, center, scale, coarse_angles)

        if not two_stage or best_score < 0.3:  # 阈值可根据实际情况调整
            return best_angle, best_score

        # 精搜索
        fine_angles = self._get_angles_with_wrap(best_angle, 10.0, 0.5)
        for ang in fine_angles:
            self._ensure_cache_for_scale_angle(scale, ang)

        fine_angle, fine_score = self._search(tgt, center, scale, fine_angles)

        if fine_score > best_score:
            return fine_angle, fine_score
        return best_angle, best_score
