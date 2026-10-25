import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok import TaskDisabledException

from src.core.learning.policy import learning_allowed
from src.data.FeatureList import FeatureList as fL
from src.tasks.mixin.navigation_detection_scope import NavigationDetectionScope, get_navigation_detection_scope
from src.tasks.onetime.BattleTask import BattleContext, BattleTask
from tests.TestNavigationMixin import NavigationMixin


class _Task:
    def __init__(self):
        self.now = 0.0
        self.events = []
        self.detection = SimpleNamespace(x=200, y=300, width=20, height=20)
        self.box = SimpleNamespace(bottom_right=None)
        self.detect_duration = 0

    def active_time(self):
        return self.now

    def yolo_detect(self, name, frame=None, box=None, conf=0.7, model_key=None):
        self.now += self.detect_duration
        return [self.detection] if self.detection else []

    def find_feature(self, feature, box=None, threshold=0.7, **kwargs):
        return self.detection

    def ocr(self, match, box=None, **kwargs):
        return []

    def click(self, key="left", **kwargs):
        self.events.append(("click", key))

    def log_info(self, *args, **kwargs):
        pass

    def next_frame(self):
        return object()

    def box_of_screen(self, *args):
        return None

    def screen_center(self):
        return 960, 540

    def scale_distance(self, value):
        return value

    def sleep(self, seconds):
        self.now += seconds

    def move_to_target_once(self, target, **kwargs):
        self.events.append(("ghost", target.x, target.y))
        return 10, 0

    def active_and_send_mouse_delta(self, *args, **kwargs):
        self.events.append(("random",))

    def send_key_down(self, key):
        self.events.append(("down", key))

    def send_key_up(self, key):
        self.events.append(("up", key))

    def press_key(self, key, **kwargs):
        self.events.append(("press", key))


class TestNavigationDetectionScope(unittest.TestCase):
    def reward_task(self, *, follow_button=None, marker=True):
        task = _Task()
        task.battle_ctx = BattleContext()
        task.lang = SimpleNamespace(daily_battle_mixin=SimpleNamespace(k_b8a81b7a="abandon", k_39d12e73_1="claim"))
        state = {"world": False, "claim": False, "page": False}
        task.wait_ocr = Mock(side_effect=lambda **kwargs: [task.detection] if state["claim"] else [])
        task.find_feature = Mock(side_effect=lambda **kwargs: task.detection if state["world"] and marker else None)
        task.rotate_search = Mock(return_value=None)
        task._open_index = Mock()
        task.to_stage = Mock(return_value=True)
        task.find_one = Mock(return_value=None)
        task.wait_feature = Mock(
            side_effect=lambda **kwargs: (
                (task.detection if state["page"] else None)
                if kwargs.get("feature") == fL.get_claim_page
                else follow_button
            )
        )

        def ensure_main():
            self.assertFalse(learning_allowed())
            state["world"] = True

        task.ensure_main = Mock(side_effect=ensure_main)
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: bool(condition()))
        task.align_ocr_or_find_target_to_center = Mock(return_value=True)

        def navigate(**kwargs):
            self.assertFalse(learning_allowed())
            self.assertNotIn("learning_session", kwargs)
            state["claim"] = True
            return True

        task.navigate_until_target = Mock(side_effect=navigate)
        task.click_with_alt = Mock(side_effect=lambda *_args: state.update(page=True))
        task._gather_retry_navigate = Mock(return_value=False)
        task.mark_task_failure = Mock()
        task._find_end = lambda: BattleTask._find_end(task)
        return task

    def test_reward_existing_tracking_without_button_continues_using_fresh_marker(self):
        task = self.reward_task()
        with (
            patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=True),
            patch("src.tasks.mixin.navigation_learning.get_learning_store", side_effect=AssertionError("learning")),
        ):
            self.assertTrue(BattleTask.to_end(task))
        task.ensure_main.assert_called_once()
        self.assertEqual(task.wait_until.call_args.kwargs, {"time_out": 5, "raise_if_not_found": False})
        task.navigate_until_target.assert_called_once()
        task.click_with_alt.assert_called_once()
        task._gather_retry_navigate.assert_not_called()
        self.assertNotIn(("click", "middle"), task.events[1:])
        self.assertTrue(learning_allowed())

    def test_reward_missing_button_and_marker_retains_retry_instead_of_false_success(self):
        task = self.reward_task(marker=False)
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=True):
            self.assertFalse(BattleTask.to_end(task))
        task._gather_retry_navigate.assert_called_once()
        task.align_ocr_or_find_target_to_center.assert_not_called()
        task.navigate_until_target.assert_not_called()
        task.click_with_alt.assert_not_called()

    def test_reward_visible_tracking_button_keeps_original_click_path(self):
        task = self.reward_task(follow_button=object())
        with patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=True):
            self.assertTrue(BattleTask.to_end(task))
        task.ensure_main.assert_called_once()
        task.wait_until.assert_not_called()
        task._gather_retry_navigate.assert_not_called()

    def test_stop_while_checking_existing_reward_marker_propagates(self):
        task = self.reward_task()
        task.wait_until.side_effect = TaskDisabledException()
        with (
            patch("src.tasks.onetime.BattleTask.is_world_map_text", return_value=True),
            self.assertRaises(TaskDisabledException),
        ):
            BattleTask.to_end(task)
        task._gather_retry_navigate.assert_not_called()
        self.assertTrue(learning_allowed())

    def test_methods_are_restored_and_other_tasks_are_unaffected(self):
        task, other = _Task(), _Task()
        before = dict(vars(task))
        other_before = dict(vars(other))
        with NavigationDetectionScope(task) as scope:
            scope.track("yolo", "end")
            task.yolo_detect("end")
            task.click(key="middle")
            other.click(key="middle")
            self.assertIsNone(get_navigation_detection_scope(other))
        self.assertEqual(before, vars(task))
        self.assertEqual(other_before, vars(other))
        self.assertNotIn(("click", "middle"), task.events)
        self.assertIn(("click", "middle"), other.events)
        task.click(key="middle")
        self.assertIn(("click", "middle"), task.events)

    def test_exception_and_explicit_close_restore_instance_overrides(self):
        task = _Task()
        task.yolo_detect = lambda *args, **kwargs: [task.detection]
        original = task.yolo_detect
        with self.assertRaisesRegex(RuntimeError, "stop"), NavigationDetectionScope(task):
            raise RuntimeError("stop")
        self.assertIs(task.yolo_detect, original)
        scope = NavigationDetectionScope(task).start()
        scope.close()
        scope.close()
        self.assertIs(task.yolo_detect, original)
        self.assertIsNone(get_navigation_detection_scope(task))

    def test_nested_scopes_keep_observations_separate(self):
        task = _Task()
        with NavigationDetectionScope(task) as outer:
            with NavigationDetectionScope(task) as inner:
                task.yolo_detect("inner")
                self.assertIsNotNone(inner.latest("yolo", "inner"))
                self.assertIsNone(outer.latest("yolo", "inner"))
            self.assertIs(get_navigation_detection_scope(task), outer)
            task.yolo_detect("outer")
            self.assertIsNotNone(outer.latest("yolo", "outer"))
        self.assertIsNone(get_navigation_detection_scope(task))

    def test_miss_does_not_erase_recent_result_or_refresh_its_time(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            task.yolo_detect("end")
            task.detection = None
            task.now = 1.5
            task.yolo_detect("end")
            observation = scope.latest("yolo", "end")
            self.assertEqual(0, observation.observed_at)
            task.now = 2.01
            self.assertIsNone(scope.latest("yolo", "end"))

    def test_fingerprints_separate_matchers_detectors_thresholds_and_models(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            task.yolo_detect("end", conf=0.8, model_key="model")
            self.assertIsNone(scope.latest("yolo", "end"))
            self.assertIsNone(scope.latest("feature", "end", threshold=0.8, model_key="model"))
            self.assertIsNone(scope.latest("yolo", "other", threshold=0.8, model_key="model"))
            self.assertIsNotNone(scope.latest("yolo", ["end"], threshold=0.8, model_key="model"))

    def test_framework_feature_name_positional_alias_has_same_fingerprint(self):
        task = _Task()

        def find_feature(feature_name=None, threshold=0, feature=None):
            return task.detection

        task.find_feature = find_feature
        with NavigationDetectionScope(task) as scope:
            task.find_feature("end", threshold=0.7)
            first = scope.latest("feature", "end")
            self.assertIsNotNone(first)
            task.now = 0.5
            task.find_feature(feature="end", threshold=0.7)
            second = scope.latest("feature", "end")
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertEqual(0.5, second.observed_at)

    def test_cached_boxes_are_copies_and_new_detection_replaces_old_result(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            task.yolo_detect("end")
            first = scope.latest("yolo", "end")
            first.boxes[0].x = 800
            task.detection.x = 700
            self.assertEqual(200, scope.latest("yolo", "end").boxes[0].x)
            task.now = 0.5
            task.yolo_detect("end")
            self.assertEqual(700, scope.latest("yolo", "end").boxes[0].x)
            self.assertEqual(0.5, scope.latest("yolo", "end").observed_at)

    def test_detection_latency_does_not_make_old_frame_recent(self):
        task = _Task()
        task.detect_duration = 2.1
        with NavigationDetectionScope(task) as scope:
            task.yolo_detect("end")
            self.assertIsNone(scope.latest("yolo", "end"))

    def test_expired_target_allows_middle_reset_and_clears_ghosts(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            scope.track("yolo", "end")
            task.yolo_detect("end")
            task.now = 3
            task.click(key="middle")
            self.assertIn(("click", "middle"), task.events)
            task.now = 0  # 即使时钟回退，重置后的旧记录也不能复活。
            self.assertIsNone(scope.latest("yolo", "end"))

    def test_unrelated_detection_does_not_block_middle_reset(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            scope.track("yolo", "end")
            task.yolo_detect("other")
            task.click(key="middle")
            self.assertIn(("click", "middle"), task.events)

    def test_alignment_uses_search_ghost_but_cannot_report_success_from_it(self):
        task = _Task()
        with NavigationDetectionScope(task) as scope:
            task.yolo_detect("end")
            task.detection = None
            result = NavigationMixin.align_ocr_or_find_target_to_center(
                task,
                "end",
                ocr=False,
                use_yolo=True,
                threshold=0.7,
                only_x=True,
                max_time=1,
                raise_if_fail=False,
                allow_random_move=False,
            )
            self.assertFalse(result)
            self.assertTrue(any(event[0] == "ghost" for event in task.events))
            self.assertNotIn(("random",), task.events)
            self.assertEqual(200, scope.latest("yolo", "end").boxes[0].x)
            self.assertEqual(300, scope.latest("yolo", "end").boxes[0].y)

    def test_alignment_does_not_use_expired_ghost(self):
        task = _Task()
        with NavigationDetectionScope(task):
            task.yolo_detect("end")
            task.now = 3
            task.detection = None
            NavigationMixin.align_ocr_or_find_target_to_center(
                task,
                "end",
                ocr=False,
                use_yolo=True,
                threshold=0.7,
                max_time=1,
                raise_if_fail=False,
                allow_random_move=False,
            )
            self.assertFalse(any(event[0] == "ghost" for event in task.events))

    def test_scoped_navigation_checks_arrival_before_pressing_w(self):
        task = _Task()
        task.ocr = lambda *args, **kwargs: [task.detection]
        with NavigationDetectionScope(task):
            result = NavigationMixin.navigate_until_target(task, target="claim")
        self.assertTrue(result)
        self.assertNotIn(("down", "w"), task.events)

    def test_recent_interaction_miss_waits_without_resuming_navigation(self):
        task = _Task()
        task.find_feature = lambda *args, **kwargs: self.fail("近期交互提示丢失时不能继续对中")
        task.ocr = lambda *args, **kwargs: [task.detection] if task.now == 0 else []
        with NavigationDetectionScope(task):
            task.ocr("claim")
            task.now = 0.1
            result = NavigationMixin.navigate_until_target(task, target="claim", nav="end", time_out=0.2)
        self.assertFalse(result)
        self.assertNotIn(("down", "w"), task.events)


if __name__ == "__main__":
    unittest.main()
