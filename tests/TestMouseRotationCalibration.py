"""鼠标视角旋转系数标定任务的单元测试。"""

import unittest

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


class TestPairLeftRight(unittest.TestCase):
    """_pair_left_right 纯函数测试。"""

    def test_pairs_positive_angles(self):
        self.assertEqual(
            MouseRotationCalibration._pair_left_right([44.0, 55.0]),
            [44.0, -44.0, 55.0, -55.0],
        )

    def test_mixed_angles_deduplicated(self):
        # 已含相反方向的不重复添加，顺序保留
        self.assertEqual(
            MouseRotationCalibration._pair_left_right([44.0, -44.0, 77.0]),
            [44.0, -44.0, 77.0, -77.0],
        )

    def test_single_angle(self):
        self.assertEqual(
            MouseRotationCalibration._pair_left_right([-90.0]),
            [-90.0, 90.0],
        )

    def test_empty(self):
        self.assertEqual(MouseRotationCalibration._pair_left_right([]), [])


if __name__ == "__main__":
    unittest.main()
