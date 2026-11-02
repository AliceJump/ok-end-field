import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image

from src.patches.screenshot_sidecar import (
    build_sidecar,
    rebuild_boxed_images,
    render_boxed_image,
    serialize_boxes,
)


class _FakeColor:
    def getRgb(self):
        return 255, 60, 60, 255


def _fake_box(x, y, width, height, name=None, confidence=0):
    return SimpleNamespace(x=x, y=y, width=width, height=height, name=name, confidence=confidence)


class TestScreenshotSidecar(unittest.TestCase):
    def test_upstream_queue_accepts_project_timestamp(self):
        from ok.core.screenshot import Screenshot, screenshot_file_stem

        from src.core import BaseEfTask  # noqa: F401 - installs the timestamp format

        self.assertEqual(screenshot_file_stem("capture", datetime(2026, 11, 2, 12, 34, 56)), "20261102_123456_capture")
        capture = Screenshot.__new__(Screenshot)
        capture.task_queue = Queue()
        capture.ui_dict = {}
        capture.add_task(object(), "screenshots", "capture")
        self.assertTrue(capture.task_queue.get_nowait()[3].endswith("_capture"))

    def test_capture_preserves_duplicate_images_and_sidecars(self):
        from ok.core.screenshot import Screenshot

        from src.patches.screenshot_sidecar_patch import _patched_generate_screen_shot

        capture = Screenshot.__new__(Screenshot)
        ui_dict = {"btn": ([_fake_box(10, 20, 5, 5)], 1.0, (255, 60, 60, 255))}

        def convert(*args, **kwargs):
            ui_dict.clear()  # Simulate an event updating the live mapping during conversion.
            return Image.new("RGB", (30, 30), "white")

        capture.to_pil_image = Mock(side_effect=convert)
        with tempfile.TemporaryDirectory() as folder:
            first = _patched_generate_screen_shot(
                capture, object(), ui_dict, folder, "capture", True, SimpleNamespace(x=3, y=4)
            )
            second = _patched_generate_screen_shot(capture, object(), {}, folder, "capture", False, None)
            self.assertNotEqual(first, second)
            self.assertTrue(Path(first).is_file())
            self.assertTrue(Path(second).is_file())
            sidecar = json.loads((Path(folder) / "capture_boxes.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["boxes"][0]["x"], 7)
            self.assertEqual(sidecar["boxes"][0]["y"], 16)
            self.assertFalse(list(Path(folder).glob("*_boxed.png")))
        capture.to_pil_image.reset_mock()
        self.assertIsNone(_patched_generate_screen_shot(capture, object(), {}, None, "capture", True, None))
        capture.to_pil_image.assert_not_called()

    def test_serialize_boxes_records_fixed_pixels(self):
        ui_dict = {
            "feature_a": ([_fake_box(10, 20, 80, 40, name="btn", confidence=0.85)], 1.0, _FakeColor()),
            "feature_b": ([_fake_box(5, 5, 0, 0)], 1.0, _FakeColor()),  # 非法尺寸应跳过
        }
        boxes = serialize_boxes(ui_dict, x_offset=100, y_offset=200)
        self.assertEqual(len(boxes), 1)
        box = boxes[0]
        self.assertEqual(box["name"], "btn")
        self.assertEqual(box["x"], 110)
        self.assertEqual(box["y"], 220)
        self.assertEqual(box["width"], 80)
        self.assertEqual(box["height"], 40)
        self.assertEqual(box["confidence"], 0.85)
        self.assertEqual(box["color"], [255, 60, 60, 255])
        self.assertEqual(box["text"], "btn_85")

    def test_serialize_boxes_uses_key_as_name_fallback(self):
        box = _fake_box(0, 0, 10, 10)
        boxes = serialize_boxes({"无名字段": ([box], 1.0, _FakeColor())})
        self.assertEqual(boxes[0]["name"], "无名字段")
        self.assertEqual(boxes[0]["text"], "无名字段")

    def test_serialize_boxes_accepts_sequence_colors_alongside_qcolor(self):
        for color in ((255, 60, 60), [255, 60, 60, 128]):
            with self.subTest(color=color):
                box = _fake_box(10, 20, 80, 40, name="btn")
                boxes = serialize_boxes({"seq": ([box], 1.0, color), "qt": ([box], 1.0, _FakeColor())})
                restored = json.loads(json.dumps(build_sidecar("a_original.png", boxes)))
                self.assertEqual(restored["boxes"][0]["color"], list(color))
                self.assertEqual(restored["boxes"][1]["color"], [255, 60, 60, 255])

    def test_render_boxed_image_draws_rectangle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original = tmp / "a_original.png"
            Image.new("RGB", (200, 100), "white").save(original)
            sidecar = build_sidecar(
                "a_original.png",
                [
                    {"x": 10, "y": 20, "width": 80, "height": 40, "color": [255, 60, 60], "text": "btn_85"},
                ],
            )
            output = tmp / "a_boxed.png"
            render_boxed_image(sidecar, original, output)

            with Image.open(output) as img:
                self.assertEqual(img.size, (200, 100))
                self.assertEqual(img.getpixel((50, 20)), (255, 60, 60))  # 上边框
                self.assertEqual(img.getpixel((50, 59)), (255, 60, 60))  # 下边框
                self.assertEqual(img.getpixel((10, 40)), (255, 60, 60))  # 左边框
                self.assertEqual(img.getpixel((89, 40)), (255, 60, 60))  # 右边框
                self.assertEqual(img.getpixel((50, 40)), (255, 255, 255))  # 框内部保持原色

    def test_rebuild_boxed_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            screenshots = tmp / "screenshots"
            screenshots.mkdir()
            original = screenshots / "a_original.png"
            Image.new("RGB", (50, 50), "white").save(original)
            (screenshots / "a_boxes.json").write_text(
                json.dumps(
                    build_sidecar(
                        "a_original.png",
                        [{"x": 5, "y": 5, "width": 20, "height": 10, "color": [255, 60, 60], "text": "btn"}],
                    )
                ),
                encoding="utf-8",
            )
            # 无对应 original 的侧车应跳过
            (screenshots / "ghost_boxes.json").write_text(
                json.dumps(build_sidecar("ghost_original.png", [])), encoding="utf-8"
            )

            rebuilt = rebuild_boxed_images(tmp)

            self.assertEqual(rebuilt, ["a_boxed.png"])
            self.assertTrue((screenshots / "a_boxed.png").is_file())
            with Image.open(screenshots / "a_boxed.png") as img:
                self.assertEqual(img.getpixel((5, 5)), (255, 60, 60))
            self.assertFalse((screenshots / "ghost_boxed.png").exists())


if __name__ == "__main__":
    unittest.main()
