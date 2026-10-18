"""Combat and post-battle retries deny every learning path, but retain realtime movers."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.core.learning.policy import deny_learning, learning_allowed, learning_epoch, without_learning
from src.tasks.mixin import zip_line_motion_cache as motion_cache
from src.tasks.mixin import zip_line_yaw_cache as yaw_cache
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic
from src.tasks.onetime.BattleTask import BattleContext, BattleTask
from tests import TestNavigationMovement as movement_tests
from tests import TestZipLineGoldGate as gold_tests
from tests import TestZipLineMotionCache as zip_tests
from tests.TestNavigationMixin import NavigationMixin


class TestLearningBattleBoundary(unittest.TestCase):
    def test_legacy_motion_and_yaw_apis_are_blocked_before_disk_access(self):
        with (
            patch.object(motion_cache, "_read_cache", side_effect=AssertionError("disk read")),
            patch.object(motion_cache, "_write_cache", side_effect=AssertionError("disk write")),
            patch.object(yaw_cache, "_read_cache", side_effect=AssertionError("yaw read")),
            patch.object(yaw_cache, "_write_cache", side_effect=AssertionError("yaw write")),
            deny_learning("post_battle"),
        ):
            self.assertIsNone(motion_cache.load_zip_line_segment_motion("key", 0))
            self.assertIsNone(motion_cache.load_nearest_zip_line_segment_motion("route", 0, 1920, 1080, 0))
            self.assertFalse(motion_cache.record_zip_line_segment_motion("key", 0, 100, 20))
            self.assertIsNone(yaw_cache.load_arrow_turn_motion(1920, 1080, 0, 90))
            self.assertFalse(yaw_cache.record_arrow_turn_motion(1920, 1080, 0, 90, 100))

    def test_disabled_zipline_does_not_even_call_history_adapters(self):
        task = zip_tests.TestZipLineMotionCache()._route_stub([(30, 0.9)])
        with (
            patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")),
            patch(
                "src.core.learning.routes.RouteLearning.nearest_yaw",
                side_effect=AssertionError("nearest"),
            ),
            patch(
                "src.tasks.mixin.route_learning._migrate_once", side_effect=AssertionError("migration")
            ),
            deny_learning("post_battle"),
        ):
            ZipLineMixin.zip_line_list_go(task, [108], config_key="route", stay_on_zip_line=True)
        self.assertIsNone(task._align_zip_line_distance.call_args.kwargs["initial_move"])
        task._read_zip_line_settled_arrow_angle.assert_not_called()
        task.active_and_send_mouse_delta.assert_called_once_with(40, 20)

    def test_explicit_prefeed_is_ignored_but_visual_ellipse_runs(self):
        task = gold_tests.TestZipLineGoldGate._alignment_stub()
        task._find_zip_line_template_target.side_effect = [
            gold_tests.TestZipLineGoldGate._matched_icon(1400, 800),
            gold_tests.TestZipLineGoldGate._matched_icon(),
        ]
        with deny_learning("post_battle"):
            self.assertTrue(ZipLineMixin._align_zip_line_distance(task, 108, initial_move=(500, 100)))
        task._read_zip_line_arrow_angle.assert_not_called()
        task._wait_zip_line_prefeed_settled.assert_not_called()
        task._move_to_zip_line_target.assert_called_once()

    def test_realtime_yaw_calibration_can_run_without_yaw_learning(self):
        task = zip_tests.TestZipLineMotionCache()._yaw_stub([(30, 0.9), (60, 0.9)])
        with (
            patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", side_effect=AssertionError("lookup")),
            patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", side_effect=AssertionError("record")),
            deny_learning("post_battle"),
        ):
            _yaw, ready = ZipLineMixin._normalize_zip_line_segment_yaw(task, 60)
        self.assertTrue(ready)

    def test_post_battle_retry_includes_transfer_zipline_and_hud_ellipse(self):
        task, _ = movement_tests.TestNavigationMovement().task(
            [
                SimpleNamespace(x=1600, y=800, width=0, height=0),
                SimpleNamespace(x=960, y=540, width=0, height=0),
            ]
        )
        route = zip_tests.TestZipLineMotionCache()._route_stub([(30, 0.9)])
        task.battle_ctx = BattleContext()
        task.lang = SimpleNamespace(
            daily_battle_mixin=SimpleNamespace(k_bfe73e18="challenge", k_39d12e73_1="claim", k_b8a81b7a="abandon")
        )
        task.box = SimpleNamespace(bottom_right=None)
        task._open_index = Mock(side_effect=lambda: self.assertFalse(learning_allowed()))
        task.to_stage = Mock(return_value=True)
        task._click_track_and_transfer = Mock(side_effect=lambda: self.assertFalse(learning_allowed()))

        def zipline():
            self.assertFalse(learning_allowed())
            ZipLineMixin.zip_line_list_go(route, [108], config_key="route", stay_on_zip_line=True)
            return True

        task._navigate_via_zip_line = zipline
        task.wait_ocr = Mock(side_effect=[False, [object()]])
        task.wait_click_ocr = Mock(return_value=True)
        task.navigate_until_target = Mock(return_value=True)
        task.align_ocr_or_find_target_to_center = lambda *args, **kwargs: (
            NavigationMixin.align_ocr_or_find_target_to_center(task, *args, **kwargs)
        )
        with (
            patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("lookup")),
            patch(
                "src.tasks.mixin.route_learning._migrate_once", side_effect=AssertionError("migration")
            ),
        ):
            self.assertTrue(BattleTask._gather_retry_navigate(task))
        self.assertTrue(learning_allowed())
        self.assertIsNone(route._align_zip_line_distance.call_args.kwargs["initial_move"])
        task.active_and_send_mouse_delta.assert_called_once()
        task.move_to_target_once.assert_not_called()

    def test_post_battle_yolo_still_uses_normal_mover(self):
        task, _ = movement_tests.TestNavigationMovement().task(
            [
                SimpleNamespace(x=1600, y=800, width=0, height=0),
                SimpleNamespace(x=960, y=540, width=0, height=0),
            ]
        )
        with deny_learning("post_battle"):
            self.assertTrue(
                NavigationMixin.align_ocr_or_find_target_to_center(
                    task, "battle_end", ocr=False, use_yolo=True, max_time=1
                )
            )
        task.move_to_target_once.assert_called_once()
        task.active_and_send_mouse_delta.assert_not_called()

    def test_combat_and_reward_entrypoints_restore_permission_on_stop(self):
        task = SimpleNamespace()

        def stop(*args, **kwargs):
            self.assertFalse(learning_allowed())
            raise TaskDisabledException()

        task.active_time = stop
        with self.assertRaises(TaskDisabledException):
            BattleMixin.auto_battle(task)
        task.battle_ctx = BattleContext(category_name="normal")
        task.lang = object()
        task.wait_pop_up = stop
        with (
            patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=False),
            self.assertRaises(TaskDisabledException),
        ):
            BattleTask.to_battle(task)
        task._find_end = stop
        with self.assertRaises(TaskDisabledException):
            BattleTask.to_end(task)
        self.assertTrue(learning_allowed())

    def test_deny_is_nested_context_local_and_propagates_cancel(self):
        @without_learning("outer")
        def stopped():
            with deny_learning("after_transfer"):
                self.assertFalse(learning_allowed())
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertTrue(pool.submit(learning_allowed).result())
                raise TaskDisabledException()

        with self.assertRaises(TaskDisabledException):
            stopped()
        self.assertTrue(learning_allowed())

    def test_trigger_poll_without_combat_preserves_learning_epoch(self):
        from tests.TestConditionalRotation import _FakeTask

        task = _FakeTask({"技能时间排轴": True}, skill=3)
        task.in_combat = lambda **kwargs: False
        before = learning_epoch()
        self.assertFalse(AutoCombatLogic(task).run())
        self.assertEqual(learning_epoch(), before)
        self.assertTrue(learning_allowed())

    def test_confirmed_trigger_combat_denies_timed_logic_and_restores_after_stop(self):
        from tests.TestConditionalRotation import _FakeTask

        task = _FakeTask({"技能时间排轴": True}, skill=3)

        def stop(**kwargs):
            self.assertFalse(learning_allowed())
            raise TaskDisabledException()

        before = learning_epoch()
        with (
            patch("src.tasks.onetime.AutoCombatLogic.TimedCombatLogic.run", side_effect=stop),
            self.assertRaises(TaskDisabledException),
        ):
            AutoCombatLogic(task).run()
        self.assertGreater(learning_epoch(), before)
        self.assertTrue(learning_allowed())
