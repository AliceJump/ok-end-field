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
TABLE_LEFTS = (188, 282, 377, 471, 566, 660)


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


def menu_selected(frame, left, top):
    """A dark circular minus badge confirms a menu click, not its timing."""
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            x, y = left + 287 + dx, top - 3 + dy
            badge = frame[max(y, 0) : y + 29, x : x + 29]
            if badge.shape[:2] != (29, 29):
                continue
            gray = cv2.cvtColor(badge, cv2.COLOR_BGR2GRAY)
            # Check the central minus, with dark pixels above and below it.
            if (
                np.count_nonzero(gray < 120) >= 80
                and np.count_nonzero(gray[12:16, 5:24] > 180) >= 4
                and gray[7, 14] < 120
                and gray[21, 14] < 120
            ):
                return True
    return False


def menu_card_rows(boxes, quantity_pattern, *, width, height):
    """Map availability OCR boxes to complete, available menu card anchors."""
    cards = set()
    for box in boxes:
        match = quantity_pattern.search(box.name.replace(" ", ""))
        if not match or (match[1] != "∞" and int(match[1]) <= 0):
            continue
        left = 552 if box.x * 1280 / width < 860 else 870
        top = round((box.y + box.height / 2) * 720 / height - 78)
        if top >= 134 and top + 93 <= 537:
            cards.add((left, top))
    return sorted(cards, key=lambda point: (round(point[1] / 46), point[0]))


def icon_signature(image):
    """Discard the card background and retain the item's central appearance."""
    image = cv2.resize(image[:, :, :3], (40, 40), interpolation=cv2.INTER_AREA)
    return image[7:33, 7:33].astype(np.float32)


def match_menu_icon(image, templates, *, threshold=0.83, margin=0.06):
    """Only use icons learned in this run, with an ambiguity rejection margin."""
    target = icon_signature(image)
    if np.std(target) < 5:
        return None
    scores = []
    for name, template in templates.items():
        signature = icon_signature(template)
        if np.std(signature) < 5:
            continue
        score = float(cv2.matchTemplate(target, signature, cv2.TM_CCOEFF_NORMED)[0, 0])
        if np.isfinite(score):
            scores.append((score, name))
    scores.sort(reverse=True)
    if scores and scores[0][0] >= threshold and (len(scores) == 1 or scores[0][0] - scores[1][0] >= margin):
        return scores[0][1]
    return None


def table_icon_rois(frame, table):
    """Return both ordinary-order icons, or no icons for other table states."""
    left = TABLE_LEFTS[table]

    def white(x, y, w, h):
        patch = frame[y : y + h, x : x + w].astype(np.int16)
        return float(np.mean((patch.min(axis=2) > 210) & (np.ptp(patch, axis=2) < 27)))

    if white(left + 1, 43, 3, 38) < 0.9 or white(left + 84, 43, 3, 20) < 0.9:
        return ()
    if white(left + 40, 36, 11, 1) >= 0.5:
        return None
    borders = (white(left + 33, 32, 6, 6), white(left + 52, 32, 6, 6))
    if min(borders) >= 0.95:
        return ((left + 29, 43, 33, 33),)
    if max(borders) < 0.9:
        return ((left + 8, 43, 33, 33), (left + 51, 43, 33, 33))
    return None
