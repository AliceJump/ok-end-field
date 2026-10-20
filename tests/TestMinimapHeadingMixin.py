"""MinimapHeadingMixin 单元测试：读朝向 + 闭环转到指定方位。

用一个假任务模拟游戏机制：
- 鼠标相对位移 dx 只转**视角**（相机方位角 += dx × 实测系数）；
- 按一次 W 让**角色**转到视角方向；
- 读朝向返回角色当前方位角。

真机上"箭头角 -> 方位角"的换算已由 TestMinimapOdometry 覆盖，这里只测控制逻辑。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.localization.minimap_heading_mixin import CONFIG_MIN_SCORE, MinimapHeadingMixin


class _FakeTurnTask(MinimapHeadingMixin):
    """模拟"鼠标转视角 + 按 W 转身"的假任务，绕开真实鼠标/键盘/截图。"""

    def __init__(
        self,
        facing=0.0,
        per_px=0.0675,
        k_error=1.0,
        score=0.9,
        arrow_fail=False,
        input_mode="foreground",
        facing_follows_camera=False,
    ):
        self.config = {
            CONFIG_MIN_SCORE: 0.6,
        }
        self.width = 1920
        self.per_px = float(per_px)
        self.facing = float(facing)  # 角色朝向（方位角）
        self.camera = float(facing)  # 视角朝向
        self.k_error = float(k_error)  # 实测系数 = 配置系数 x k_error（模拟标定偏差）
        self.score = float(score)
        self.arrow_fail = arrow_fail
        self.facing_follows_camera = bool(facing_follows_camera)
        self._input_mode = input_mode
        self.sent: list[int] = []
        self.w_presses = 0
        self.logs: list[str] = []
        self._init_minimap_heading_mixin()

    # --- 输入/环境桩 ---
    def yaw_per_pixel(self):
        return self.per_px

    def input_mode(self):
        return self._input_mode

    def sleep(self, seconds):
        pass

    def press_key(self, key, down_time=0.0):
        self.w_presses += 1

    def active_and_send_mouse_delta(self, dx=0, dy=0, steps=1, delay=0):
        self.sent.append(int(dx))
        self.camera = (self.camera + dx * self.yaw_per_pixel() * self.k_error) % 360.0
        if self.facing_follows_camera:
            self.facing = self.camera

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
        self.facing = self.camera  # 角色转到视角方向
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


class TestCameraYawRatio(unittest.TestCase):
    """覆盖摄像机水平比率的读取、缓存和失败边界。"""

    def _task(self):
        task = MinimapHeadingMixin()
        task.width = 1920
        task.logs = []
        task.executor = SimpleNamespace(
            device_manager=SimpleNamespace(
                hwnd_window=SimpleNamespace(exe_full_path=r"D:\Games\Endfield Game\Endfield.exe")
            )
        )
        task._init_minimap_heading_mixin()
        task.log_info = lambda msg, notify=False: task.logs.append(str(msg))
        task.log_warning = lambda msg, notify=False: task.logs.append("WARN " + str(msg))
        return task

    def test_uses_build_verified_camera_ratio_and_caches_it(self):
        task = self._task()
        with patch(
            "src.localization.minimap_heading_mixin.get_default_camera_yaw_ratio",
            return_value=0.084375,
        ) as get_ratio:
            self.assertAlmostEqual(task.yaw_per_pixel(), 0.084375)
            self.assertAlmostEqual(task.yaw_per_pixel(), 0.084375)

        get_ratio.assert_called_once_with(1920, game_dir=r"D:\Games\Endfield Game")
        self.assertTrue(any("0.084375" in msg for msg in task.logs))

    def test_rejects_unverifiable_camera_ratio(self):
        task = self._task()
        with patch(
            "src.localization.minimap_heading_mixin.get_default_camera_yaw_ratio",
            side_effect=ValueError("changed build"),
        ):
            self.assertEqual(task.yaw_per_pixel(), 0.0)

        self.assertTrue(any("不可用" in msg for msg in task.logs))

    def test_rejects_missing_game_window_path(self):
        task = self._task()
        task.executor = None

        self.assertEqual(task.yaw_per_pixel(), 0.0)
        self.assertTrue(any("安装目录" in msg for msg in task.logs))

    def test_accepts_signed_reverse_ratio(self):
        task = self._task()
        with patch(
            "src.localization.minimap_heading_mixin.get_default_camera_yaw_ratio",
            return_value=-0.084375,
        ):
            self.assertAlmostEqual(task.yaw_per_pixel(), -0.084375)


class TestTurnToBearing(unittest.TestCase):
    """覆盖闭环转向的轮数、误差和失败行为。"""

    def test_already_at_target_does_nothing(self):
        task = _FakeTurnTask(facing=90.0)
        res = task.turn_to_bearing(90.0)
        self.assertTrue(res["ok"])
        self.assertEqual(res["rounds"], 0)
        self.assertEqual(task.w_presses, 0)  # 一次 W 都不按
        self.assertEqual(task.sent, [])  # 一点鼠标都不动

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
        # 首轮尚无相机基准；后续稳定观测才反映真实输入比例。
        self.assertIsNone(res["history"][0]["ratio"])
        for e in res["history"][1:]:
            self.assertAlmostEqual(e["ratio"], 0.0675 * 0.8, delta=0.002)

    def test_initial_camera_offset_converges_without_full_revolutions(self):
        task = _FakeTurnTask(facing=0.0, per_px=0.08438)
        task.camera = 280.0
        result = task.turn_to_bearing(90.0, tolerance=4.0, max_rounds=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 2)
        self.assertIsNone(result["history"][0]["ratio"])
        self.assertAlmostEqual(sum(abs(dx * 0.08438) for dx in task.sent), 170.0, delta=0.2)

    def test_all_camera_offsets_converge_with_calibrated_gain(self):
        for camera in range(0, 360, 5):
            with self.subTest(camera=camera):
                task = _FakeTurnTask(facing=0.0, per_px=0.08438)
                task.camera = float(camera)
                result = task.turn_to_bearing(90.0, tolerance=4.0, max_rounds=3)
                self.assertTrue(result["ok"], result)
                self.assertLessEqual(result["rounds"], 2)
                self.assertLess(sum(abs(dx * 0.08438) for dx in task.sent), 271.0)

    @staticmethod
    def _feedback_task(headings):
        task = _FakeTurnTask(facing=0.0)
        pending = list(headings)
        active = False

        def read(frame=None):
            if active and pending:
                reading = pending.pop(0)
                if reading is None:
                    return None, 0.0
                task.facing = float(reading)
            return task.facing, task.score

        def after_w(*, min_score=None):
            nonlocal active
            active = True
            return MinimapHeadingMixin._arrow_after_w(task, min_score=min_score)

        task._read_arrow = read
        task._arrow_after_w = after_w
        return task, pending

    def test_delayed_heading_is_waited_for_without_sending_another_turn(self):
        task, pending = self._feedback_task([0, 30, 60, 90, 90, 90])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(task.w_presses, 1)
        self.assertEqual(pending, [])
        self.assertAlmostEqual(sum(task.sent) * 0.0675, 90.0, delta=0.1)

    def test_transient_target_hit_is_not_accepted_before_heading_settles(self):
        task, pending = self._feedback_task([90, 120, 90, 90, 90])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(pending, [])

    def test_continuously_changing_heading_does_not_trigger_another_input(self):
        task, _ = self._feedback_task([10, 30, 60, 90, 120, 150, 180])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "heading_unsettled")
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(task.w_presses, 1)

    def test_slow_drift_is_not_mistaken_for_stable_heading(self):
        task, _ = self._feedback_task([10, 11, 12, 13, 14, 15, 16])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "heading_unsettled")
        self.assertEqual(task.w_presses, 1)

    def test_low_confidence_gap_requires_new_stable_observations(self):
        task, pending = self._feedback_task([20, None, 40, 90, 90, 90])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 1)
        self.assertEqual(pending, [])

    def test_reverse_feedback_is_not_an_adaptive_gain(self):
        self.assertEqual(MinimapHeadingMixin._adapted_yaw_per_pixel(0.0675, -0.0675, 100), 0.0675)

    def test_half_turn_feedback_is_not_used_as_a_calibration_sample(self):
        task = _FakeTurnTask(facing=0.0, k_error=2.0)
        result = task.turn_to_bearing(90.0, max_rounds=2)
        self.assertIsNone(result["history"][1]["ratio"])

    def test_unsettled_final_target_reading_does_not_report_success(self):
        task, _ = self._feedback_task([10, 30, 60, 120, 150, 180, 90])
        result = task.turn_to_bearing(90.0, max_rounds=3)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "heading_unsettled")
        self.assertEqual(result["rounds"], 1)

    def test_normal_turn_reuses_refresh_wait_for_stability(self):
        task = _FakeTurnTask(facing=0.0)
        waits = []
        task.sleep = waits.append
        press_key = task.press_key

        def align_character(key, **kwargs):
            press_key(key, **kwargs)
            task.facing = task.camera

        task.press_key = align_character
        task._arrow_after_w = lambda **kwargs: MinimapHeadingMixin._arrow_after_w(task, **kwargs)
        result = task.turn_to_bearing(90.0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(task.w_presses, 1)
        self.assertEqual(waits[-3:], [0.3, 0.05, 0.05])

    def test_takes_shortest_path_across_north(self):
        """350° -> 10° 应该向东走 +20°，而不是向西绕 340°。"""
        task = _FakeTurnTask(facing=350.0)
        res = task.turn_to_bearing(10.0, tolerance=5.0)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["rounds"], 1)
        self.assertGreater(task.sent[0], 0)  # 鼠标右移 = 方位角增大
        self.assertAlmostEqual(res["heading"], 10.0, delta=1.0)

    def test_reverse_ratio_uses_opposite_mouse_direction(self):
        task = _FakeTurnTask(facing=0.0, per_px=-0.084375)

        res = task.turn_to_bearing(90.0, tolerance=5.0)

        self.assertTrue(res["ok"], res)
        self.assertLess(task.sent[0], 0)
        self.assertAlmostEqual(res["heading"], 90.0, delta=1.0)

    def test_adapts_to_ten_times_lower_coefficient(self):
        """从第二轮的同基准观测估计系数，首轮不能拿来估计。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.1)
        res = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["rounds"], 3)
        self.assertEqual(task.w_presses, 3)
        self.assertLessEqual(abs(res["error"]), 5.0)

    def test_adapts_reverse_ratio_from_second_round(self):
        """负号反向镜头也要能根据第二轮稳定反馈修正增益。"""
        task = _FakeTurnTask(facing=0.0, per_px=-0.084375, k_error=0.5)

        result = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 3)
        self.assertLess(sum(task.sent), 0)
        self.assertTrue(all(entry["ratio"] is None or entry["ratio"] < 0 for entry in result["history"]))

    def test_large_turn_is_chunked_but_total_is_kept(self):
        """1333px 的位移要拆成 ≤100px 的多段，但总和不变（避免单次事件被截断）。"""
        task = _FakeTurnTask(facing=0.0)
        task.turn_to_bearing(90.0, tolerance=5.0)
        self.assertGreater(len(task.sent), 1)
        self.assertTrue(all(abs(step) <= 100 for step in task.sent), task.sent)
        self.assertEqual(sum(task.sent), 1333)
        self.assertAlmostEqual(task.facing, 90.0, delta=1.0)

    def test_refuses_without_valid_coefficient(self):
        task = _FakeTurnTask(facing=0.0, per_px=0.0)
        res = task.turn_to_bearing(90.0)
        self.assertFalse(res["ok"])
        self.assertFalse(res["one_shot"])
        self.assertEqual(task.w_presses, 0)
        self.assertEqual(task.sent, [])
        self.assertTrue(any("摄像机水平比率不可用" in m for m in task.logs))

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


class TestAimViewToBearing(unittest.TestCase):
    """覆盖滑索场景下只转视角、不按 W 的横向对准。"""

    def test_turns_view_without_pressing_w(self):
        task = _FakeTurnTask(facing=0.0, facing_follows_camera=True)

        result = task.aim_view_to_bearing(90.0, tolerance=5.0)

        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(result["heading"], 90.0, delta=1.0)
        self.assertEqual(task.w_presses, 0)
        self.assertTrue(task.sent)

    def test_already_aligned_does_not_move_mouse(self):
        task = _FakeTurnTask(facing=135.0, facing_follows_camera=True)

        result = task.aim_view_to_bearing(135.0, tolerance=5.0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rounds"], 0)
        self.assertEqual(task.sent, [])
        self.assertEqual(task.w_presses, 0)

    def test_unverified_ground_aim_sends_view_rotation_without_w(self):
        task = _FakeTurnTask(facing=0.0, facing_follows_camera=False)

        result = task.aim_view_to_bearing(
            90.0,
            tolerance=5.0,
            verify_heading=False,
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["view_rotation_sent"])
        self.assertFalse(result["verified_heading"])
        self.assertEqual(task.facing, 0.0)
        self.assertAlmostEqual(task.camera, 90.0, delta=1.0)
        self.assertEqual(task.w_presses, 0)

    def test_aim_adapts_to_lower_coefficient_in_second_round(self):
        task = _FakeTurnTask(
            facing=0.0,
            k_error=0.5,
            facing_follows_camera=True,
        )

        result = task.aim_view_to_bearing(
            90.0,
            tolerance=5.0,
            max_rounds=2,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rounds"], 2)
        self.assertLessEqual(abs(result["error"]), 5.0)
        self.assertEqual(task.w_presses, 0)


class TestOneShotAndDiagnostics(unittest.TestCase):
    """覆盖一轮到位判定和比例尺诊断。"""

    """目标是**一轮到位**（只按一次 W）；没到位时要给出能照着改的提示。"""

    def test_one_shot_when_coefficient_is_good(self):
        task = _FakeTurnTask(facing=0.0)
        res = task.turn_to_bearing(90.0)
        self.assertTrue(res["ok"])
        self.assertTrue(res["one_shot"])
        self.assertEqual(task.w_presses, 1)  # 只按了一次 W

    def test_not_one_shot_when_correction_needed(self):
        task = _FakeTurnTask(facing=0.0, k_error=0.8)
        res = task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(res["ok"])
        self.assertFalse(res["one_shot"])
        self.assertEqual(task.w_presses, 2)

    def test_hints_when_measured_ratio_much_lower(self):
        """实测系数明显偏小时要提示位移可能被截断或计数传递不一致。"""
        task = _FakeTurnTask(facing=0.0, k_error=0.5)
        task.turn_to_bearing(90.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(any("明显低于理论值" in m for m in task.logs), task.logs)

    def test_hints_when_measured_ratio_much_higher(self):
        """实测系数明显偏大时要提示计数传递可能不是 1:1。"""
        task = _FakeTurnTask(facing=0.0, k_error=1.5)
        task.turn_to_bearing(45.0, tolerance=5.0, max_rounds=3)
        self.assertTrue(any("明显高于理论值" in m for m in task.logs), task.logs)

    def test_initial_camera_offset_does_not_emit_coefficient_advice(self):
        task = _FakeTurnTask(facing=0.0)
        task.camera = 280.0
        result = task.turn_to_bearing(90.0, max_rounds=1)
        self.assertIsNone(result["history"][0]["ratio"])
        self.assertFalse(any("实测系数" in message for message in task.logs))

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
