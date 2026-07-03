import re
from pathlib import Path

import cv2
from ok.test.TaskTestCase import TaskTestCase

from src.config import config
from src.tasks.WarehouseTransferTask import WarehouseTransferTask


class TestWarehouseSwitchOCR(TaskTestCase):
    task_class = WarehouseTransferTask
    config = config

    _CONFIRM_RE = re.compile("\u786e\u8ba4")
    _CONNECTED_RE = re.compile("\u5df2\u8fde\u63a5")
    _WULING_RE = re.compile("\u6b66\u9675")
    _VALLEY_RE = re.compile("\u56db\u53f7\u8c37\u5730|\u8c37\u5730")

    _DEBUG_DIR = Path("tests/debug")

    def _bottom_right_box(self):
        return self.task.box_of_screen(0.79, 0.79, 0.84, 0.82, name="bottom_right")

    def _switch_menu_box(self):
        return self.task.box_of_screen(0.4, 0.35, 0.75, 0.65, name="switch_menu")

    def _save_debug_image(self, name: str, confirm_hits, connected_hits, wuling_hits, valley_hits):
        frame = getattr(self.task, "frame", None)
        if frame is None:
            return

        img = frame.copy()

        def _draw_ocr_hits(hits, color, tag):
            for hit in hits or []:
                x = int(getattr(hit, "x", 0))
                y = int(getattr(hit, "y", 0))
                w = int(getattr(hit, "width", 0))
                h = int(getattr(hit, "height", 0))
                if w <= 0 or h <= 0:
                    continue
                cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
                text = f"{tag}:{getattr(hit, 'name', '')}"
                cv2.putText(
                    img,
                    text,
                    (x, max(16, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    color,
                    2,
                )

        br = self._bottom_right_box()
        br_x1, br_y1 = int(br.x), int(br.y)
        br_x2, br_y2 = int(br.x + br.width), int(br.y + br.height)
        cv2.rectangle(img, (br_x1, br_y1), (br_x2, br_y2), (0, 255, 255), 2)

        sm = self._switch_menu_box()
        sm_x1, sm_y1 = int(sm.x), int(sm.y)
        sm_x2, sm_y2 = int(sm.x + sm.width), int(sm.y + sm.height)
        cv2.rectangle(img, (sm_x1, sm_y1), (sm_x2, sm_y2), (255, 128, 0), 2)

        cv2.putText(
            img,
            f"confirm={len(confirm_hits)} connected={len(connected_hits)}",
            (max(10, br_x1), max(20, br_y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )
        cv2.putText(
            img,
            f"wuling={len(wuling_hits)} valley={len(valley_hits)}",
            (max(10, sm_x1), max(20, sm_y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 128, 0),
            2,
        )
        _draw_ocr_hits(wuling_hits, (0, 255, 0), "wuling")
        _draw_ocr_hits(valley_hits, (255, 0, 255), "valley")

        self._DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self._DEBUG_DIR / f"{name}.png"), img)

    def _assert_switch_options_present(self):
        switch_box = self._switch_menu_box()
        wuling_hits = self.task.ocr(box=switch_box, match=self._WULING_RE)
        valley_hits = self.task.ocr(box=switch_box, match=self._VALLEY_RE)
        self.assertTrue(wuling_hits, "Expected to detect '武陵' option in switch menu")
        self.assertTrue(valley_hits, "Expected to detect '四号谷地/谷地' option in switch menu")
        return wuling_hits, valley_hits

    def test_confirm_pending_image(self):
        image_path = Path("tests/images/warehouse_switch_confirm_pending.png")
        if not image_path.exists():
            self.skipTest(f"Missing image: {image_path}")

        self.set_image(str(image_path))

        bottom_right_box = self._bottom_right_box()
        confirm_hits = self.task.ocr(box=bottom_right_box, match=self._CONFIRM_RE)
        connected_hits = self.task.ocr(box=bottom_right_box, match=self._CONNECTED_RE)
        wuling_hits, valley_hits = self._assert_switch_options_present()

        self._save_debug_image(
            "warehouse_switch_confirm_pending_box",
            confirm_hits,
            connected_hits,
            wuling_hits,
            valley_hits,
        )

        self.assertTrue(confirm_hits, "Expected to detect '确认' in confirm-pending image")
        self.assertFalse(connected_hits, "Did not expect to detect '已连接' in confirm-pending image")

    def test_connected_image(self):
        image_path = Path("tests/images/warehouse_switch_connected.png")
        if not image_path.exists():
            self.skipTest(f"Missing image: {image_path}")

        self.set_image(str(image_path))

        bottom_right_box = self._bottom_right_box()
        confirm_hits = self.task.ocr(box=bottom_right_box, match=self._CONFIRM_RE)
        connected_hits = self.task.ocr(box=bottom_right_box, match=self._CONNECTED_RE)
        wuling_hits, valley_hits = self._assert_switch_options_present()

        self._save_debug_image(
            "warehouse_switch_connected_box",
            confirm_hits,
            connected_hits,
            wuling_hits,
            valley_hits,
        )

        self.assertTrue(connected_hits, "Expected to detect '已连接' in connected image")
        self.assertFalse(confirm_hits, "Did not expect to detect '确认' in connected image")
