"""Cheap fixed-position geometry probe for the tracking-arrival toast."""

import cv2


def has_tracking_arrival_band(frame):
    """Detect the white band in the Chinese arrival toast, without reading text.

    Geometry is normalized from the recorded 1080p sample. A hit only slows
    navigation; interaction OCR must still confirm the actual destination.
    """
    if frame is None or getattr(frame, "size", 0) == 0 or frame.ndim != 3:
        return False
    height, width = frame.shape[:2]
    crop = frame[round(height * 0.158) : round(height * 0.183), round(width * 0.455) : round(width * 0.545)]
    if crop.size == 0:
        return False
    mask = cv2.inRange(crop, (180, 180, 180), (255, 255, 255))
    points = cv2.findNonZero(mask)
    if points is None:
        return False
    x, y, text_width, text_height = cv2.boundingRect(points)
    rows, columns = mask.shape
    return (
        0.70 <= text_width / columns <= 0.95
        and 0.55 <= text_height / rows <= 0.90
        and 0.08 <= cv2.countNonZero(mask) / mask.size <= 0.40
        and 0.03 <= x / columns <= 0.20
        and 0.82 <= (x + text_width) / columns <= 0.98
        and 0.03 <= y / rows <= 0.25
        and 0.65 <= (y + text_height) / rows <= 0.95
    )
