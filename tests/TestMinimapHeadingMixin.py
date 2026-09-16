# -*- coding: utf-8 -*-
"""MinimapHeadingMixin 单元测试：读朝向 + 闭环转到指定方位。

用一个假任务模拟游戏机制：
- 鼠标相对位移 dx 只转**视角**（相机方位角 += dx × 实测系数）；
- 按一次 W 让**角色**转到视角方向；
- 读朝向返回角色当前方位角。

真机上"箭头角 -> 方位角"的换算已由 TestMinimapOdometry 覆盖，这里只测控制逻辑。
"""
import unittest

from src.tasks.mixin.minimap_heading_mixin import (
    CONFIG_MIN_SCORE,
    CONFIG_YAW_PER_PIXEL,
    MinimapHeadingMixin,
)


class _FakeTurnTask(MinimapHeadingMixin):
    """模拟"鼠标转视角 + 按 W 转身"的假任务，绕开真实鼠标/键盘/截图。"""

    def __init__(self, facing=0.0, per_px=0.0675, k_error=1.0, score=0.9,
                 arrow_fail=False, input_mode="foreground"):
        self.config = {
            CONFIG_YAW_PER_PIXEL: per_px,
            CONFIG_MIN_SCORE: 0.6,
        }
        self.facing = float(facing)      # 角色朝向（方位角）
        self.camera = float(facing)      # 视角朝向
        self.k_error = float(k_error)    # 实测系数 = 配置系数 × k_error（模拟标定偏差）
        self.score = float(score)
        self.arrow_fail = arrow_fail
        self._input_mode = input_mode
        self.sent: list[int] = []
        self.w_presses = 0
        self.logs: list[str] = []
        self._init_minimap_heading_mixin()

    # --- 输入/环境桩 ---
    def input_mode(self):
        return self._input_mode

    def sleep(self, seconds):
        pass

    def press_key(self, key, down_time=0.0):
        self.w_presses += 1

    def active_and_send_mouse_delta(self, dx=0, dy=0, steps=1, delay=0):
        self.sent.append(int(dx))
        self.camera = (self.camera + dx * self.config[CONFIG_YAW_PER_PIXEL] * self.k_error) % 360.0

    def log_info(self, msg, notify=False):
        self.logs.append(str(msg))

    def log_warning(self, msg, notify=False):
        self.logs.append("WARN " + str(msg))

    # --- 读取/转身桩 ---
    def _read_arrow(self, frame=None):
        if self.arrow_fail:
            return None, 0.0
        return self.facing, self.score

    def _arrow_after_w(self, *, min_score=None):
        self.press_key("w", down_time=0.3)
        self.facing = self.camera          # 角色转到视角方向
        return self.read_heading(min_score=min_score)


class TestReadHeading(unittest.TestCase):
    """覆盖箭头角到罗盘方位角的读取契约。"""

    def test_returns_bearing_and_score(self):
        task = _FakeTurnTask(facing=90.0)
        self.assertEqual(task.read_heading(), (90.0, 0.9))

    def test_low_score_is_unusable(self):
        task = _FakeTurnTask(facing=90.0, score=0.3)
        bearing, score = task.read_heading()
        self.assertIsNone(bearing)
        self.assertAlmostEqual(score, 0.3, delta=1e-6)

    def test_detection_failure_is_unusable(self):
        bearing, score = _FakeTurnTask(arrow_fail=True).read_heading()
        self.assertIsNone(bearing)
        self.assertAlmostEqual(score, 0.0, delta=1e-6)


class TestTurnToBearing(unittest.TestCase):
    """覆盖闭环转向的轮数、误差和失败行为。"""

    def test_already_at_target_does_nothing(self):
        task = _FakeTurnTask(facing=90.0)
        res = task.turn_to_bearing(90.0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["rounds"], 0)
        self.assertEqual(task.w_presses, 0)   # 一次 W 都不按
        self.assertEqual(task.sent, [])       # 一点鼠标都不动

    def test_converges_in_one_round_with_exact_coefficient(self):
        task = _FakeTurnTask(facing=0.0)
        res = task.turn_to_bearing(90.0, tolerance=5.0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["rounds"], 1)
        self.assertEqual(task.w_presses, 1)
        self.assertLessEqual(abs(res["error"]), 5.0)
        self.assertAlmostEqual(res["heading"], 90.0, delta=1.0)

    def test_converges_despite_coefficient_error(self):
        """标定系数偏 20% 时第二轮补正即可到位（闭环的意义）。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.8)
        res = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(res["ok"], res)
        self.assertGreaterEqual(res["rounds"], 2)
        self.assertLessEqual(abs(res["error"]), 5.0)
        # 每轮的实测系数应稳定反映真实比例（配置 0.0675 × 0.8 = 0.054）
        for e in res["history"]:
            self.assertAlmostEqual(e["ratio"], 0.0675 * 0.8, delta=0.002)

    def test_takes_shortest_path_across_north(self):
        """350° -> 10° 应该向东走 +20°，而不是向西绕 340°。"""
        task = _FakeTurnTask(facing=350.0)
        res = task.turn_to_bearing(10.0, tolerance=5.0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["rounds"], 1)
        self.assertGreater(task.sent[0], 0)            # 鼠标右移 = 方位角增大
        self.assertAlmostEqual(res["heading"], 10.0, delta=1.0)

    def test_gives_up_after_max_rounds(self):
        """系数差 10 倍时 2 轮内到不了位：必须如实报 FAIL，不能假成功。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.1)
        res = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=2)
        self.assertFalse(res["ok"])
        self.assertEqual(res["rounds"], 2)
        self.assertEqual(task.w_presses, 2)
        self.assertGreater(abs(res["error"]), 5.0)

    def test_large_turn_is_chunked_but_total_is_kept(self):
        """1333px 的位移要拆成 ≤200px 的多段，但总和不变（避免单次事件被截断）。"""
        task = _FakeTurnTask(facing=0.0)
        task.turn_to_bearing(90.0, tolerance=5.0)
        self.assertGreater(len(task.sent), 1)
        self.assertTrue(all(abs(step) <= 200 for step in task.sent), task.sent)
        self.assertEqual(sum(task.sent), 1333)
        self.assertAlmostEqual(task.facing, 90.0, delta=1.0)

    def test_refuses_without_valid_coefficient(self):
        task = _FakeTurnTask(facing=0.0, per_px=0.0)
        res = task.turn_to_bearing(90.0)
        self.assertFalse(res["ok"])
        self.assertEqual(task.w_presses, 0)
        self.assertEqual(task.sent, [])
        self.assertTrue(any("yaw_per_pixel" in m for m in task.logs))

    def test_refuses_in_background_mode(self):
        """后台模式鼠标位移会被静默丢弃：提前拦下，别做无用的 W 按键。"""
        task = _FakeTurnTask(facing=0.0, input_mode="background")
        res = task.turn_to_bearing(90.0)
        self.assertFalse(res["ok"])
        self.assertEqual(task.w_presses, 0)
        self.assertEqual(task.sent, [])

    def test_unreadable_heading_aborts_without_turning(self):
        task = _FakeTurnTask(arrow_fail=True)
        res = task.turn_to_bearing(90.0)
        self.assertFalse(res["ok"])
        self.assertIsNone(res["heading"])
        self.assertEqual(res["rounds"], 0)
        self.assertEqual(task.w_presses, 0)

    def test_last_turn_result_is_exposed(self):
        task = _FakeTurnTask(facing=0.0)
        res = task.turn_to_bearing(90.0)
        self.assertIs(task.last_turn_result(), res)

    def test_zero_tolerance_still_reports_honestly(self):
        """容差为 0 时基本不可能达标，但误差必须如实反映（不伪造成 0）。"""
        task = _FakeTurnTask(facing=0.0)
        res = task.turn_to_bearing(90.0, tolerance=0.0, max_rounds=1)
        self.assertFalse(res["ok"])
        self.assertIsNotNone(res["error"])
        self.assertLess(abs(res["error"]), 50.0)


class TestOneShotAndDiagnostics(unittest.TestCase):
    """覆盖一轮到位判定和比例尺诊断。"""

    """目标是**一轮到位**（只按一次 W）；没到位时要给出能照着改的提示。"""

    def test_one_shot_when_coefficient_is_good(self):
        task = _FakeTurnTask(facing=0.0)
        res = task.turn_to_bearing(90.0)
        self.assertTrue(res["ok"])
        self.assertTrue(res["one_shot"])
        self.assertEqual(task.w_presses, 1)      # 只按了一次 W

    def test_not_one_shot_when_correction_needed(self):
        task = _FakeTurnTask(facing=0.0, k_error=0.8)
        res = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(res["ok"])
        self.assertFalse(res["one_shot"])
        self.assertEqual(task.w_presses, 2)

    def test_hints_when_measured_ratio_much_lower(self):
        """实测系数明显偏小 = 位移可能被截断 / 配置系数偏大 -> 要提示怎么改。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.5)
        task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=1)
        self.assertTrue(any("明显低于配置" in m for m in task.logs), task.logs)

    def test_hints_when_measured_ratio_much_higher(self):
        """实测系数明显偏大 = 配置系数偏小 -> 改成实测值就能一次到位。"""
        task = _FakeTurnTask(facing=0.0, k_error=2.0)
        task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=1)
        self.assertTrue(any("明显高于配置" in m for m in task.logs), task.logs)

    def test_no_hint_for_tiny_displacement(self):
        """位移太小（<40px）时实测比例被量化噪声主导，不据此提示。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.5)
        task.turn_to_bearing(2.0, tolerance=0.5, max_rounds=1)
        self.assertFalse(any("明显" in m for m in task.logs), task.logs)

    def test_no_hint_when_ratio_matches(self):
        task = _FakeTurnTask(facing=0.0)
        task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=1)
        self.assertFalse(any("明显" in m for m in task.logs), task.logs)


if __name__ == "__main__":
    unittest.main()
