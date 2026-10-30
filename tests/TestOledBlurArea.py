"""OLED 模糊遮罩的区域回调。

ok 框架只在项目配置提供 ``blur_area`` 回调时才注册「启用模糊 / 模糊算法 /
模糊间隔」这三个基础选项，并把回调返回的矩形用前景叠加窗盖在游戏画面上。
这里锁定回调的契约，避免以后改区域时把「比例 → 像素」算错。
"""

import unittest

from ok.feature.Box import Box

from src.config import OLED_BLUR_RECTS, blur_area, config


class TestOledBlurArea(unittest.TestCase):
    def test_config_exposes_the_callback(self):
        self.assertIs(config["blur_area"], blur_area)

    def test_callback_returns_boxes_inside_the_frame(self):
        boxes = blur_area(1920, 1080)
        self.assertEqual(len(boxes), len(OLED_BLUR_RECTS))
        for box in boxes:
            self.assertIsInstance(box, Box)
            self.assertGreaterEqual(box.x, 0)
            self.assertGreaterEqual(box.y, 0)
            self.assertLessEqual(box.x + box.width, 1920)
            self.assertLessEqual(box.y + box.height, 1080)

    def test_uid_region_is_the_bottom_left_corner(self):
        box = blur_area(1920, 1080)[0]
        self.assertGreater(box.y, 1080 * 0.9)
        self.assertLess(box.x, 1920 * 0.2)
        self.assertGreater(box.width, 0)
        self.assertGreater(box.height, 0)

    def test_region_scales_with_resolution(self):
        small = blur_area(1280, 720)[0]
        large = blur_area(2560, 1440)[0]
        self.assertAlmostEqual(small.x * 2, large.x, delta=2)
        self.assertAlmostEqual(small.width * 2, large.width, delta=2)
        self.assertAlmostEqual(small.y * 2, large.y, delta=2)

    def test_invalid_size_returns_empty(self):
        self.assertEqual(blur_area(0, 0), [])
        self.assertEqual(blur_area(-10, 1080), [])


if __name__ == "__main__":
    unittest.main()
