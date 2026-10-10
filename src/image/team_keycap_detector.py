"""Probe the common skill keycaps, independently of digit color/templates."""

import cv2
import numpy as np

_RIGHT_OFFSETS = (343, 247.5, 150.75, 54)
_BODY = np.zeros((42, 44), dtype=bool)
_BODY[12:31, 13:32] = True
_BODY[13:30, 15:30] = False
_GLYPH = np.zeros((42, 44), dtype=bool)
_GLYPH[14:29, 16:30] = True


def _has_outline(gray: np.ndarray) -> bool:
    dx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    dy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    vertical = (dx >= 24) & (dx >= dy * 1.3)
    horizontal = (dy >= 24) & (dy >= dx * 1.3)
    # Ignore rounded corners and require three independently supported sides.
    sides = (
        vertical[14:28, 8:14].mean(axis=0).max(),
        vertical[14:28, 29:35].mean(axis=0).max(),
        horizontal[7:13, 15:29].mean(axis=1).max(),
        horizontal[29:35, 15:29].mean(axis=1).max(),
    )
    return sum(score >= 0.65 for score in sides) >= 3


def _has_light_glyph(crop: np.ndarray) -> bool:
    """Accept dim neutral lettering that is brighter than its neutral body."""
    channels = crop.astype(np.int16)
    value = channels.max(axis=2)
    spread = value - channels.min(axis=2)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if np.mean((spread[_BODY] <= 40) & (value[_BODY] <= 175)) < 0.65:
        return False
    background = float(np.median(gray[_BODY]))
    background_value = float(np.median(value[_BODY]))
    neutral = spread <= np.maximum(12, value * 0.25)
    text_values = gray[_GLYPH & neutral]
    peak = float(np.percentile(text_values, 95)) if text_values.size else background
    cutoff = background + max(16, 0.55 * (peak - background))
    foreground = neutral & (gray >= cutoff) & (value >= background_value + 16) & _GLYPH
    count, labels, stats, _ = cv2.connectedComponentsWithStats(foreground.astype(np.uint8), 8)
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if not (1 <= width <= 12 and 7 <= height <= 15 and 7 <= area <= 100):
            continue
        # Clipped menu labels, filled buttons and the keycap rim are not glyphs.
        if x <= 16 or x + width >= 30 or y <= 14 or y + height >= 29:
            continue
        if abs(x + width / 2 - 22) > 4 or abs(y + height / 2 - 21) > 4:
            continue
        text_peak = float(np.percentile(gray[labels == index], 90))
        if np.mean(gray[_BODY] <= text_peak - 16) >= 0.65:
            return True
    return False


def detect_team_keycaps(frame: np.ndarray | None) -> tuple[bool, ...]:
    """Return four physical slots, left to right; this does not identify digits.

    Text can repair an obscured outline only after two outlines confirm the HUD.
    Use the same bottom/right anchoring and uniform scale as the skill templates.
    """
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        return (False,) * 4
    height, width = frame.shape[:2]
    if not height or not width:
        return (False,) * 4
    scale = min(height / 1080, width / 1920)
    crops = []
    found = []
    for offset in _RIGHT_OFFSETS:
        cx, cy = width - offset * scale, height - 60 * scale
        x1, y1 = round(cx - 22 * scale), round(cy - 21 * scale)
        x2, y2 = round(cx + 22 * scale), round(cy + 21 * scale)
        crop = None
        if 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height:
            crop = cv2.resize(frame[y1:y2, x1:x2], (44, 42), interpolation=cv2.INTER_AREA)
        crops.append(crop)
        found.append(crop is not None and _has_outline(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)))
    if sum(found) >= 2:
        for index, crop in enumerate(crops):
            if not found[index] and crop is not None:
                found[index] = _has_light_glyph(crop)
    return tuple(bool(slot) for slot in found)
