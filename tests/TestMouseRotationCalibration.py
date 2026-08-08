# -*- coding: utf-8 -*-
"""鼠标视角旋转系数标定任务的单元测试。"""
import unittest

from src.config import config
from src.tasks.test.MouseRotationCalibration import (
    MouseRotationCalibration,
    angle_delta,
)


class TestAngleDelta(unittest.TestCase):
    """angle_delta 纯函数测试（含 0/360 边界）。"""

    def test_wrap_positive(self):
        self.assertAlmostEqual(angle_delta(2, 358), 4.0)

    def test_wrap_negative(self):
        self.assertAlmostEqual(angle_delta(358, 2), -4.0)

    def test_positive_delta(self):
        self.assertAlmostEqual(angle_delta(100, 90), 10.0)

    def test_negative_delta(self):
        self.assertAlmostEqual(angle_delta(90, 100), -10.0)

    def test_half_turn(self):
        self.assertAlmostEqual(angle_delta(180, 0), -180.0)


class TestMouseRotationCalibrationTask(unittest.TestCase):
    """验证新任务能够被任务系统正常发现。"""

    def test_registered_in_onetime_tasks(self):
        modules = [module for module, _ in config.get("onetime_tasks", [])]
        self.assertIn("src.tasks.test.MouseRotationCalibration", modules)

    def test_task_class_importable(self):
        self.assertTrue(issubclass(MouseRotationCalibration, object))
        self.assertTrue(callable(angle_delta))

    def test_calibration_defaults(self):
        self.assertEqual(MouseRotationCalibration.CALIBRATION_DX, 100)
        self.assertEqual(MouseRotationCalibration.REPEAT_COUNT, 4)
        self.assertEqual(MouseRotationCalibration.ANGLE_REFRESH_DELAY, 0.1)
        self.assertEqual(MouseRotationCalibration.MIN_SCORE, 0.6)


if __name__ == "__main__":
    unittest.main()
