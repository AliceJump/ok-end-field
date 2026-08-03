# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from src.patches.screenshot_sidecar import (
    build_sidecar,
    rebuild_boxed_images,
    render_boxed_image,
    serialize_boxes,
    sidecar_path_of,
)


class _FakeColor:
    def getRgb(self):
        return 255, 60, 60, 255


def _fake_box(x, y, width, height, name=None, confidence=0):
    return SimpleNamespace(x=x, y=y, width=width, height=height,
                           name=name, confidence=confidence)


class TestScreenshotSidecar(unittest.TestCase):

    def test_serialize_boxes_records_fixed_pixels(self):
        ui_dict = {
            "feature_a": ([_fake_box(10, 20, 80, 40, name="btn", confidence=0.85)], 1.0,
                          _FakeColor()),
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

    def test_build_sidecar_structure(self):
        sidecar = build_sidecar("a_original.png", [{"x": 1}])
        self.assertEqual(sidecar["format"], 1)
        self.assertEqual(sidecar["image"], "a_original.png")
        self.assertEqual(sidecar["boxes"], [{"x": 1}])

    def test_render_boxed_image_draws_rectangle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            original = tmp / "a_original.png"
            Image.new("RGB", (200, 100), "white").save(original)
            sidecar = build_sidecar("a_original.png", [
                {"x": 10, "y": 20, "width": 80, "height": 40,
                 "color": [255, 60, 60], "text": "btn_85"},
            ])
            output = tmp / "a_boxed.png"
            render_boxed_image(sidecar, original, output)

            with Image.open(output) as img:
                self.assertEqual(img.size, (200, 100))
                self.assertEqual(img.getpixel((50, 20)), (255, 60, 60))  # 上边框
                self.assertEqual(img.getpixel((50, 59)), (255, 60, 60))  # 下边框
                self.assertEqual(img.getpixel((10, 40)), (255, 60, 60))  # 左边框
                self.assertEqual(img.getpixel((89, 40)), (255, 60, 60))  # 右边框
                self.assertEqual(img.getpixel((50, 40)), (255, 255, 255))  # 框内部保持原色

    def test_sidecar_path_of_derives_name(self):
        self.assertEqual(
            sidecar_path_of("20260728_152007_日常任务_original.png").name,
            "20260728_152007_日常任务_boxes.json",
        )

    def test_rebuild_boxed_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            screenshots = tmp / "screenshots"
            screenshots.mkdir()
            original = screenshots / "a_original.png"
            Image.new("RGB", (50, 50), "white").save(original)
            (screenshots / "a_boxes.json").write_text(json.dumps(build_sidecar(
                "a_original.png",
                [{"x": 5, "y": 5, "width": 20, "height": 10,
                  "color": [255, 60, 60], "text": "btn"}],
            )), encoding="utf-8")
            # 无对应 original 的侧车应跳过
            (screenshots / "ghost_boxes.json").write_text(json.dumps(build_sidecar(
                "ghost_original.png", [])), encoding="utf-8")

            rebuilt = rebuild_boxed_images(tmp)

            self.assertEqual(rebuilt, ["a_boxed.png"])
            self.assertTrue((screenshots / "a_boxed.png").is_file())
            with Image.open(screenshots / "a_boxed.png") as img:
                self.assertEqual(img.getpixel((5, 5)), (255, 60, 60))
            self.assertFalse((screenshots / "ghost_boxed.png").exists())


if __name__ == "__main__":
    unittest.main()
