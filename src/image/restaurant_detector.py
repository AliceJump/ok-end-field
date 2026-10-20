"""Visual probes for the 16:9 Inn kitchen panel (1280 x 720 reference).

Adapted from MaaEnd contributors' AGPL-3.0 restaurant recognition in
MaaEnd/MaaEnd#6676, a6b3ead2429a86d382be0a8fef6c4831113087c6.
The game computes the yellow demand marker after subtracting finished and queued
food. Read that marker instead of estimating production with wall-clock timers.
"""

from dataclasses import dataclass

import cv2
import numpy as np

KITCHEN_TABS = ((174, 145, 209, 46), (386, 145, 217, 46), (605, 145, 220, 46))


def reference_frame(frame):
    """Normalize captured game pixels, never desktop/DPI logical coordinates."""
    height, width = frame.shape[:2]
    if height < 720 or abs(width / height - 16 / 9) > 0.015:
        raise ValueError("Restaurant requires a 16:9 game capture of at least 1280x720")
    return cv2.resize(frame[:, :, :3], (1280, 720), interpolation=cv2.INTER_AREA)


@dataclass(frozen=True)
class KitchenQueue:
    index: int
    used: int
    capacity: int
    selected: bool


def kitchen_queues(frame):
    """Require two or three distinct queue dots; uncertain tabs are unavailable."""
    result = []
    for index, (x, y, width, _) in enumerate(KITCHEN_TABS):
        # Only inspect the dot band, excluding text, tags and tab borders.
        roi = frame[y + 33 : y + 42, x + width - 32 : x + width - 3]
        pixels = roi.astype(np.int16)
        blue, green, red = cv2.split(pixels)
        occupied = (green > 140) & (green - red > 60) & (green - blue > 30)
        neutral = (pixels.min(axis=2) >= 100) & (pixels.max(axis=2) <= 205)
        neutral &= pixels.max(axis=2) - pixels.min(axis=2) < 25
        mask = (occupied | neutral).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        dots = []
        for label in range(1, count):
            _, _, w, h, area = stats[label]
            if 2 <= w <= 6 and 2 <= h <= 6 and 4 <= area <= 30:
                dots.append(bool(np.count_nonzero(occupied & (labels == label)) >= area / 2))
        if len(dots) not in (2, 3):
            continue
        title = frame[y + 5 : y + 30, x + 6 : x + 91].astype(np.int16)
        selected = float(np.mean(title.sum(axis=2) < 270)) > 0.35
        result.append(KitchenQueue(index, sum(dots), len(dots), selected))
    return result


@dataclass(frozen=True)
class RestockCard:
    left: int
    top: int

    @property
    def click_point(self):
        return self.left + 120, self.top + 45


def restock_cards(frame):
    """Locate only complete visible recipe cards, including after list scrolling."""
    cards = []
    for left in (488, 797):
        roi = frame[207:480, left + 3 : left + 27].astype(np.int16)
        blue, green, red = cv2.split(roi)
        yellow = (red > 165) & (green > 105) & (red - blue > 35) & (green - blue > 20)
        count, _, stats, centers = cv2.connectedComponentsWithStats(yellow.astype(np.uint8), 8)
        for label in range(1, count):
            _, _, w, h, area = stats[label]
            if not (6 <= w <= 24 and 6 <= h <= 26 and area >= 20):
                continue
            top = round(207 + centers[label][1] - 50)
            # Partially scrolled cards are revisited on the overlapping page.
            if top >= 205 and top + 88 <= 482:
                cards.append(RestockCard(left, top))
    return sorted(cards, key=lambda card: (card.top // 20, card.left))
