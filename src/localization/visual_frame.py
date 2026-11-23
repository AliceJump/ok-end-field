"""Pixel normalization shared by visual localization and its recordings."""

import cv2

VISUAL_FRAME_WIDTH = 1280


def normalize_visual_frame(frame):
    if frame is None:
        return None
    height, width = frame.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("empty visual frame")
    if width == VISUAL_FRAME_WIDTH:
        return frame
    return cv2.resize(
        frame,
        (VISUAL_FRAME_WIDTH, round(height * VISUAL_FRAME_WIDTH / width)),
        interpolation=cv2.INTER_AREA,
    )
