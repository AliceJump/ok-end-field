import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.data.FeatureList import FeatureList as fL
from src.data.world_map import stages_cost
from src.tasks.mixin.navigation_detection_scope import get_navigation_detection_scope
from src.tasks.mixin.search_mixin import SearchMixin
from src.tasks.onetime.BattleTask import BattleContext, BattleTask


class _ToEndTaskHarness:
    def __init__(self):
        self.events = []
        self._now = 0.0
        self.width = 1920
        self.height = 1080
        self.box = SimpleNamespace(bottom_right=object())
        self.lang = SimpleNamespace(
            daily_battle_mixin=SimpleNamespace(
                k_b8a81b7a="abandon",
                k_39d12e73_1="claim",
            )
        )

    def wait_ocr(self, **kwargs):
        self.events.append("ocr_miss")

    def find_one(self, **kwargs):
        return None

    def wait_feature(self, **kwargs):
        return None

    def click(self, *args, **kwargs):
        self.events.append(("click", kwargs.get("key")))

    def yolo_detect(self, *args, **kwargs):
        self.events.append("yolo_hit")
        return [SimpleNamespace(x=200, y=300, width=20, height=20)]

    def find_feature(self, *args, **kwargs):
        self.events.append("feature_hit")
        return SimpleNamespace(x=200, y=300, width=20, height=20)

    def box_of_screen(self, *args, **kwargs):
        return object()

    def align_ocr_or_find_target_to_center(self, *args, **kwargs):
        self.events.append("align")
        scope = get_navigation_detection_scope(self)
        kind = "yolo" if kwargs.get("use_yolo") else "feature"
        self.events.append(("seed", scope.latest(kind, args[0]) is not None))
        return False

    def move_keys(self, *args, **kwargs):
        self.events.append("move")

    def active_and_send_mouse_delta(self, *args, **kwargs):
        self.events.append("mouse_delta")

    def rotate_search(self, check_func, **kwargs):
        self.events.append("rotate")
        return check_func()

    def strafe_search(self, check_func, **kwargs):
        self.events.append("strafe")
        return check_func()

    def sleep(self, timeout):
        self._now += timeout
        self.events.append(("sleep", timeout))

    def active_time(self):
        return self._now

    def scale_distance(self, value):
        return value

    def log_info(self, *args, **kwargs):
        pass


def _make_impl(task):
    """裸 BattleTask 实例绑定 harness 的属性与方法，避免触发框架 __init__/property。"""
    impl = BattleTask.__new__(BattleTask)
    impl.__dict__.update(vars(task))
    for name in dir(_ToEndTaskHarness):
        if name.startswith("_"):
            continue
        member = getattr(_ToEndTaskHarness, name)
        if callable(member):
            setattr(impl, name, types.MethodType(member, impl))
    return impl


class TestDailyBattleToEnd(unittest.TestCase):
    def test_failed_gather_navigation_cannot_enter_activation(self):
        task = SimpleNamespace(battle_ctx=BattleContext(), navigate_to_gather=Mock(return_value=False))
        self.assertFalse(BattleTask.battle_gather(task))

    def test_failed_gather_retry_navigation_cannot_claim_reward(self):
        task = Mock()
        task._navigate_via_zip_line.return_value = False
        task.navigate_until_target.return_value = False
        self.assertFalse(BattleTask._gather_retry_navigate(task))
        task.navigate_until_target.assert_called_once()
        task.wait_ocr.assert_not_called()
        task.wait_click_ocr.assert_not_called()

    def test_to_battle_passes_recovery_and_only_starts_after_entry(self):
        for entered in (True, False):
            with self.subTest(entered=entered):
                task = Mock()
                task.battle_ctx = BattleContext(category_name="normal")
                task.wait_battle_space.return_value = entered
                task.auto_battle.return_value = True
                recovery = Mock()
                with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
                    self.assertEqual(BattleTask.to_battle(task, transition_recovery=recovery), entered)
                task.wait_battle_space.assert_called_once_with(transition_recovery=recovery)
                self.assertEqual(task.auto_battle.call_count, int(entered))
                self.assertEqual(task.strafe_search.call_count, int(entered))

    def test_recycle_recovery_checks_current_entry_button(self):
        task = Mock()
        task.battle_ctx = BattleContext(left_ticket=100)
        task._battle_stage_cost = 20
        button = object()
        task.find_one.side_effect = [None, button, None, button]
        task.get_claim.return_value = 80
        task.to_end.side_effect = [True, False]

        def enter(transition_recovery):
            transition_recovery()
            transition_recovery()
            return True

        task.to_battle.side_effect = enter
        self.assertFalse(BattleTask.battle_recycle(task))

        entry_box = task.box_of_screen.return_value
        self.assertEqual(
            [call.kwargs for call in task.find_one.call_args_list],
            [
                {"feature": fL.give_gift, "box": entry_box},
                {"feature": fL.give_gift, "box": entry_box},
                {"feature": fL.restart_battle, "vertical_variance": 0.1},
                {"feature": fL.restart_battle, "vertical_variance": 0.1},
            ],
        )
        self.assertEqual(task.click.call_count, 2)
        task.to_restart.assert_called_once_with()

    def test_recycle_does_not_wait_for_battle_after_failed_restart(self):
        task = Mock()
        task.battle_ctx = BattleContext(left_ticket=100)
        task._battle_stage_cost = 20
        task.get_claim.return_value = 80
        task.to_restart.return_value = False

        self.assertFalse(BattleTask.battle_recycle(task))

        self.assertEqual(task.to_battle.call_count, 1)

    def test_stage_cost_is_numeric_property(self):
        category, expected_cost = next(iter(stages_cost.items()))
        task = BattleTask.__new__(BattleTask)
        task.battle_ctx = BattleContext(category_name=category)

        self.assertEqual(task._battle_stage_cost, expected_cost)

    def test_yolo_hit_disables_subsequent_middle_clicks(self):
        task = _ToEndTaskHarness()
        feature = _make_impl(task)
        feature.battle_ctx = BattleContext(category_name="normal")

        with patch(
            "src.tasks.onetime.BattleTask.is_world_map_text",
            return_value=False,
        ):
            result = feature.to_end()

        self.assertFalse(result)
        middle_click_indexes = [index for index, event in enumerate(task.events) if event == ("click", "middle")]
        self.assertEqual(0, len(middle_click_indexes))
        self.assertEqual(4, task.events.count("ocr_miss"))
        self.assertEqual(0, task.events.count("rotate"))
        self.assertEqual(1, task.events.count("strafe"))
        self.assertIn(("seed", True), task.events)
        self.assertIsNone(get_navigation_detection_scope(feature))

    def test_gather_hit_also_preserves_view_and_seeds_alignment(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="gather")

        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=True):
            self.assertFalse(feature.to_end())

        self.assertNotIn(("click", "middle"), feature.events)
        self.assertEqual(0, feature.events.count("rotate"))
        self.assertIn(("seed", True), feature.events)

    def test_recent_detection_is_forwarded_without_extra_rotation(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        checks = iter([True, True, False])

        def detect(*args, **kwargs):
            if next(checks, False):
                return [SimpleNamespace(x=200, y=300, width=20, height=20)]
            return []

        feature.yolo_detect = detect
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertFalse(feature.to_end())

        self.assertEqual(0, feature.events.count("rotate"))
        self.assertNotIn(("click", "middle"), feature.events)
        self.assertIn(("seed", True), feature.events)

    def test_rotating_search_stops_at_first_detection(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        feature.rotate_search = types.MethodType(SearchMixin.rotate_search, feature)
        feature._executor = SimpleNamespace(method=SimpleNamespace(width=1920))

        def detect(*args, **kwargs):
            if "mouse_delta" in feature.events:
                return [SimpleNamespace(x=200, y=300, width=20, height=20)]
            return []

        feature.yolo_detect = detect
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertFalse(feature.to_end())

        self.assertEqual(1, feature.events.count("mouse_delta"))
        self.assertIn(("seed", True), feature.events)
        self.assertEqual(1, feature.events.count(("click", "middle")))

    def test_misleading_claim_text_does_not_confirm_reward_arrival(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        feature.wait_ocr = Mock(return_value=[object()])
        feature.click_with_alt = Mock()
        feature.wait_feature = Mock(return_value=None)
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertFalse(feature.to_end())
        self.assertEqual(feature.click_with_alt.call_count, 3)
        feature.wait_feature.assert_called_with(feature=fL.get_claim_page, time_out=3, raise_if_not_found=False)
        self.assertIn("align", feature.events)

    def test_missing_reward_page_can_recover_on_later_click(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        feature.wait_ocr = Mock(return_value=[object()])
        feature.click_with_alt = Mock()
        feature.wait_feature = Mock(side_effect=[None, object()])
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertTrue(feature.to_end())
        self.assertEqual(feature.click_with_alt.call_count, 2)

    def test_existing_reward_page_does_not_move_or_click(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        feature.find_one = Mock(return_value=object())
        feature.wait_ocr = Mock()
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertTrue(feature.to_end())
        feature.wait_ocr.assert_not_called()
        self.assertEqual(feature.events, [])

    def test_rotate_search_sine_path_uses_preview_defaults_and_keeps_legacy_yaw_budget(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature._executor = SimpleNamespace(method=SimpleNamespace(width=1920, height=1080))
        moves = []
        sleeps = []
        checks = 0

        feature.active_and_send_mouse_delta = lambda **kwargs: moves.append(kwargs)
        feature.sleep = lambda timeout: sleeps.append(timeout)

        def check():
            nonlocal checks
            checks += 1
            return False

        result = SearchMixin.rotate_search(
            feature,
            check,
            segments=40,
            step_ratio=0.1,
            between_delay=0.1,
        )

        self.assertIsNone(result)
        self.assertEqual(80, checks)
        self.assertEqual(80, len(moves))
        self.assertEqual(40 * int(feature.width * 0.1), sum(move["dx"] for move in moves))
        self.assertAlmostEqual(4.0, sum(sleeps))
        self.assertAlmostEqual(40 * 2 * 0.005, sum(move["delay"] * move["steps"] for move in moves))

        cumulative_pitch = 0
        pitch_positions = []
        for move in moves:
            cumulative_pitch += move["dy"]
            pitch_positions.append(cumulative_pitch)

        self.assertEqual([0] * 10, [pitch_positions[index - 1] for index in range(8, 81, 8)])
        self.assertEqual(240, max(pitch_positions))
        self.assertEqual(-240, min(pitch_positions))

    def test_to_end_restores_methods_after_task_stop(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        original = dict(vars(feature))

        with (
            patch("src.tasks.onetime.BattleTask.is_world_map_text", side_effect=RuntimeError("stopped")),
            self.assertRaisesRegex(RuntimeError, "stopped"),
        ):
            feature.to_end()

        self.assertEqual(original, vars(feature))

    def test_middle_reset_still_runs_when_marker_is_missing(self):
        feature = _make_impl(_ToEndTaskHarness())
        feature.battle_ctx = BattleContext(category_name="normal")
        feature.yolo_detect = lambda *args, **kwargs: []

        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False):
            self.assertFalse(feature.to_end())

        self.assertIn(("click", "middle"), feature.events)
        self.assertEqual(1, feature.events.count("rotate"))

    def test_normal_reward_search_has_no_redundant_one_second_sleep(self):
        task = _ToEndTaskHarness()
        task = _ToEndTaskHarness()
        task.yolo_detect = lambda *args, **kwargs: []
        feature = _make_impl(task)
        feature.battle_ctx = BattleContext(category_name="normal")

        with patch(
            "src.tasks.onetime.BattleTask.is_world_map_text",
            return_value=False,
        ):
            result = feature.to_end()

        self.assertFalse(result)
        self.assertNotIn(("sleep", 1), task.events)


if __name__ == "__main__":
    unittest.main()
