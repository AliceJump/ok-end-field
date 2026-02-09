from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import cv2
from ok import Box
from ok.test.TaskTestCase import TaskTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import config  # noqa: E402
from src.tasks.WarehouseTransferTask import WarehouseTransferTask  # noqa: E402
from src.tasks.WarehouseTransferTask import (  # noqa: E402
    calc_count_ocr_rect,
    find_best_template_match,
)

DEFAULT_IMAGE = Path("tests/images/warehouse_sample.png")
DEFAULT_TEMPLATE_DIR = Path("assets/items/images")
DEFAULT_OUTPUT_DIR = Path("tests/debug")
ITEM_ICON_THRESHOLD = 0.82
ITEM_SEARCH_BOX = (0.12, 0.30, 0.55, 0.68)
ITEM_TEMPLATE_SCALES = (1.10, 1.15, 1.20, 1.25, 1.30)
DEFAULT_ITEMS = ("item_iron_bottle", "item_iron_ore", "item_proc_battery_3")
BOX_CURRENT_LOCATION = (0.15, 0.18, 0.26, 0.22)
BOX_SWITCH_BUTTON = (0.48, 0.18, 0.52, 0.215)
BOX_ONEKEY_STORE = (0.64, 0.705, 0.69, 0.735)
_RE_COUNT = re.compile(r"(\d+(?:\.\d+)?)\s*\u4e07|(\d{1,5})")


class _WarehouseOCRHarness(TaskTestCase):
    task_class = WarehouseTransferTask
    config = config


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Warehouse template matching debug image exporter.")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE, help="Input screenshot path")
    parser.add_argument(
        "--items",
        type=str,
        nargs="*",
        default=list(DEFAULT_ITEMS),
        help="Item keys to test simultaneously",
    )
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR, help="Template png directory")
    parser.add_argument("--threshold", type=float, default=ITEM_ICON_THRESHOLD, help="Match threshold")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output folder")
    return parser.parse_args()


def _draw_search_box(frame, search_xyxy):
    x1, y1, x2, y2 = search_xyxy
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)


def _parse_count(text: str):
    cleaned = text.replace(",", "").replace(" ", "").replace("\n", "")
    m = _RE_COUNT.search(cleaned)
    if not m:
        return None
    if m.group(1):
        return int(float(m.group(1)) * 10000)
    if m.group(2):
        return int(m.group(2))
    return None


def _box_to_xyxy(frame, rel_box):
    h, w = frame.shape[:2]
    x1 = int(w * rel_box[0])
    y1 = int(h * rel_box[1])
    x2 = int(w * rel_box[2])
    y2 = int(h * rel_box[3])
    return x1, y1, x2, y2


def _ocr_texts(task, x: int, y: int, w: int, h: int, name: str):
    roi = Box(int(x), int(y), int(w), int(h), name=name)
    results = task.ocr(box=roi)
    return [str(getattr(t, "name", "")).strip() for t in (results or []) if str(getattr(t, "name", "")).strip()]


def _ocr_detect_location(frame, task, rel_box):
    x1, y1, x2, y2 = _box_to_xyxy(frame, rel_box)
    texts = _ocr_texts(task, x1, y1, x2 - x1, y2 - y1, "current_location_ocr")
    normalized = "".join(texts).replace(" ", "").replace("\n", "")
    if "武陵" in normalized:
        return "wuling", normalized
    if ("四号谷地" in normalized) or ("谷地" in normalized):
        return "valley", normalized
    return "unknown", normalized


def _draw_named_box(frame, rel_box, name, color):
    x1, y1, x2, y2 = _box_to_xyxy(frame, rel_box)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    cv2.putText(
        frame,
        name,
        (x1, max(18, y1 - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        2,
    )
    return x1, y1, x2, y2


def _ocr_count_near_match(frame, task, search_xyxy, loc, size):
    if loc is None or size is None:
        return None, None, "no_match"
    x1, y1, _, _ = search_xyxy
    rx = x1 + int(loc[0])
    ry = y1 + int(loc[1])
    rw, rh = int(size[0]), int(size[1])
    fh, fw = frame.shape[:2]
    cx, cy, cw, ch = calc_count_ocr_rect(rx, ry, rw, rh, fw, fh)
    raw_texts = _ocr_texts(task, cx, cy, cw, ch, "count_roi")

    best = None
    for text in raw_texts:
        val = _parse_count(text)
        if val is None:
            continue
        if best is None or val > best:
            best = val
    return best, (cx, cy, cw, ch), " | ".join(raw_texts)


def _draw_item_debug(frame, search_xyxy, label, score, scale, loc, size, threshold, color, count_value, count_rect):
    x1, y1, _, _ = search_xyxy
    status = "MISS"
    draw_color = (0, 0, 255)
    if loc is not None and size is not None:
        rx = x1 + int(loc[0])
        ry = y1 + int(loc[1])
        rw, rh = int(size[0]), int(size[1])
        if score >= threshold:
            draw_color = color
            status = "HIT"
        cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), draw_color, 2)
        if count_rect is not None:
            cx, cy, cw, ch = count_rect
            cv2.rectangle(frame, (cx, cy), (cx + cw, cy + ch), (255, 255, 255), 2)
        text_y = max(20, ry - 10)
        text_x = max(10, rx)
    else:
        text_y = 24
        text_x = max(10, x1)

    count_text = f" count={count_value}" if count_value is not None else " count=?"

    cv2.putText(
        frame,
        f"{label} {status} score={score:.3f} scale={scale:.2f} size={size}{count_text}",
        (text_x, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        draw_color,
        2,
    )


def main():
    args = _parse_args()
    img = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Image not found or unreadable: {args.image}")

    _WarehouseOCRHarness.setUpClass()
    try:
        harness = _WarehouseOCRHarness(methodName="runTest")
        harness.set_image(str(args.image))
        task = _WarehouseOCRHarness.task

        sx1, sy1, sx2, sy2 = _box_to_xyxy(img, ITEM_SEARCH_BOX)
        search = img[sy1:sy2, sx1:sx2]

        output = img.copy()
        _draw_search_box(output, (sx1, sy1, sx2, sy2))

        location_detected, location_text = _ocr_detect_location(output, task, BOX_CURRENT_LOCATION)
        _draw_named_box(output, BOX_CURRENT_LOCATION, f"current_location detected:{location_detected}", (255, 128, 0))
        _draw_named_box(output, BOX_SWITCH_BUTTON, "switch_button", (255, 128, 0))
        _draw_named_box(output, BOX_ONEKEY_STORE, "onekey_store", (255, 128, 0))
        if location_text:
            lx1, ly1, _, _ = _box_to_xyxy(output, BOX_CURRENT_LOCATION)
            cv2.putText(
                output,
                f"ocr:{location_text}",
                (lx1, ly1 + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 200, 0),
                1,
            )

        color_map = {
            "item_iron_bottle": (0, 255, 0),
            "item_iron_ore": (255, 128, 0),
            "item_proc_battery_3": (255, 0, 255),
        }

        results = []
        for idx, item_key in enumerate(args.items):
            template_path = args.template_dir / f"{item_key}.png"
            if not template_path.exists():
                results.append((item_key, "template_missing", 0.0, 1.0, None, None, "template_missing"))
                continue
            tpl = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
            if tpl is None:
                results.append((item_key, "template_unreadable", 0.0, 1.0, None, None, "template_unreadable"))
                continue

            score, scale, loc, size = find_best_template_match(search, tpl, ITEM_TEMPLATE_SCALES)
            count_value, count_rect, count_raw = _ocr_count_near_match(output, task, (sx1, sy1, sx2, sy2), loc, size)

            _draw_item_debug(
                output,
                (sx1, sy1, sx2, sy2),
                item_key,
                score,
                scale,
                loc,
                size,
                args.threshold,
                color_map.get(item_key, [(0, 255, 0), (255, 128, 0), (255, 0, 255)][idx % 3]),
                count_value,
                count_rect,
            )
            results.append((item_key, "ok", score, scale, size, count_value, count_raw))

        cv2.putText(
            output,
            f"location_detected={location_detected}",
            (20, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
        )

        args.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = args.output_dir / f"warehouse_match_debug_multi_{ts}.png"
        cv2.imwrite(str(out_path), output)

        def _safe_text(text):
            return str(text).encode("unicode_escape").decode("ascii")

        print(f"location_detected={location_detected}")
        print(f"location_ocr_raw={_safe_text(location_text)}")
        for item_key, state, score, scale, size, count_value, count_raw in results:
            print(
                f"item={item_key} state={state} score={score:.4f} scale={scale:.2f} "
                f"size={size} count={count_value} count_ocr_raw={_safe_text(count_raw)}"
            )
        print(f"output={out_path}")
    finally:
        _WarehouseOCRHarness.tearDownClass()


if __name__ == "__main__":
    main()
