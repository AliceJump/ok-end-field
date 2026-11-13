"""Prepare safely, then take a photo only while ready and close its preview."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok import Box, TaskDisabledException
from ok.util.config import Config

from src.data.FeatureList import FeatureList as fL
from src.tasks.navigation.mixin.map_mixin import MapMixin
from src.tasks.onetime.DailyTask import DailyTask
from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask
from src.tasks.onetime.EnvironmentMonitoringRoute import (
    AnnularWaterfallRoute,
    CisternOriginiumSlugsRoute,
    EnvironmentMonitoringRoute,
    EternalSunsetRoute,
    PoolLotusRoute,
    StationaryObservationRoute,
)


class TestEnvironmentMonitoringPhotoFlow(unittest.TestCase):
    def test_route_photo_directions(self):
        for route, direction in (
            (PoolLotusRoute, "right"),
            (StationaryObservationRoute, "right"),
            (CisternOriginiumSlugsRoute, "left"),
            (AnnularWaterfallRoute, "up"),
            (EternalSunsetRoute, "up"),
        ):
            with self.subTest(route=route.__name__):
                self.assertEqual(route.photo_direction, direction)

    def test_routes_choose_their_own_teleport_strategy(self):
        task = self.task()
        EnvironmentMonitoringRoute(task).transfer()
        task.open_observation_map.assert_called_once_with()
        task.to_near_transfer_point.assert_called_once_with(need_track=False)
        task._wait_for_transfer_arrival.assert_called_once_with()

    def test_quick_transfer_failure_does_not_check_arrival(self):
        task = self.task()
        task.to_near_transfer_point.return_value = False
        route = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "环境监测快捷传送失败"):
            route.transfer()
        task._wait_for_transfer_arrival.assert_not_called()

    def test_eternal_sunset_navigation_uses_recorded_goal_and_stops_on_failure(self):
        task = self.task()
        task.navigate_grid_to.return_value = True
        EternalSunsetRoute(task).run()
        task.navigate_grid_to.assert_called_once_with((-908.95, -1179.31), map_id="map02")
        task.navigate_grid_to.return_value = False
        navigation = EternalSunsetRoute(task)
        with self.assertRaisesRegex(RuntimeError, "栖霞驻影导航失败"):
            navigation.run()

    def test_alternate_transfer_clears_map_before_selecting_point(self):
        task = self.task()
        task.clear_icon_in_map.return_value = True
        task.zoom_map_out.return_value = True
        task.wait_click_feature.return_value = True
        task._wait_click_transfer_go.return_value = True
        task._wait_for_transfer_arrival.return_value = True
        stages = Mock()
        for name in (
            "open_observation_map",
            "clear_icon_in_map",
            "zoom_map_out",
            "wait_click_feature",
            "_wait_click_transfer_go",
            "_wait_for_transfer_arrival",
        ):
            stages.attach_mock(getattr(task, name), name)
        EternalSunsetRoute(task).transfer()
        task.open_observation_map.assert_called_once_with()
        task.clear_icon_in_map.assert_called_once()
        task.zoom_map_out.assert_called_once()
        task._wait_click_transfer_go.assert_called_once_with()
        task._wait_for_transfer_arrival.assert_called_once_with()
        task.wait_click_feature.assert_called_once_with(
            fL.transfer_point,
            box=task.box_of_screen(0.05, 0.02, 0.5, 0.5),
            threshold=0.7,
            time_out=5,
            raise_if_not_found=False,
        )
        self.assertEqual(
            [item[0] for item in stages.mock_calls],
            [
                "open_observation_map",
                "clear_icon_in_map",
                "zoom_map_out",
                "wait_click_feature",
                "_wait_click_transfer_go",
                "_wait_for_transfer_arrival",
            ],
        )

    def test_unstable_map_after_clearing_stops_before_zoom(self):
        task = self.task()
        task.wait_click_feature.return_value = True
        task.wait_ui_stable.return_value = False
        task.clear_icon_in_map.side_effect = lambda: MapMixin.clear_icon_in_map(task)
        route = EternalSunsetRoute(task)
        with self.assertRaisesRegex(RuntimeError, "清空地图标记失败"):
            route.transfer()
        task.zoom_map_out.assert_not_called()

    def test_eternal_sunset_uses_photo_flow_and_continues(self):
        task = self.task()
        task.observation_key.side_effect = lambda name: "eternal_sunset"
        task.prepare_observation.side_effect = [{"name": "栖霞驻影"}, None]
        task.photograph_observation.side_effect = None
        task.photograph_observation.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        task.photograph_observation.assert_called_once()
        self.assertEqual(task.prepare_observation.call_count, 2)

    def test_daily_monitoring_runs_only_when_selected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            daily = DailyTask(SimpleNamespace(scene=None, pause_start=None, debug=False), SimpleNamespace())
        self.assertNotIn("环境监测", daily.default_config[daily.CFG_OTHER_TASKS])
        item = next(item for item in daily.build_task_plan() if item[0] == "⭐环境监测")
        plan_keys = [entry[0] for entry in daily.build_task_plan()]
        self.assertEqual(plan_keys.index("⭐环境监测") + 1, plan_keys.index("⭐自动送货"))
        self.assertEqual(item[1].__self__.run_method, "run")
        daily._account_override_for = Mock(return_value={})
        daily.config = SimpleNamespace(
            get=lambda key, default=None: ["收邮件"] if key == daily.CFG_OTHER_TASKS else default
        )
        self.assertFalse(item[2]())
        daily.config = SimpleNamespace(
            get=lambda key, default=None: ["环境监测"] if key == daily.CFG_OTHER_TASKS else default
        )
        self.assertTrue(item[2]())

    def test_run_skips_navigation_when_no_observation(self):
        task = self.task()
        task.prepare_observation.return_value = None
        result = EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        self.assertTrue(result)
        task.mark_task_failure.assert_not_called()
        task.prepare_photo.assert_not_called()

    def test_failed_transfer_does_not_block_successful_observation(self):
        task = self.task()

        def failed_transfer():
            task.selected_observation = {"name": "小池荷"}
            raise RuntimeError("传送失败")

        attempts = iter((failed_transfer, lambda: {"name": "表象"}, lambda: None))
        task.prepare_observation.side_effect = lambda: next(attempts)()
        task.navigate_observation.side_effect = None
        task.photograph_observation.side_effect = None
        task.photograph_observation.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertEqual(task.completed_observations, {"pool_lotus", "thillu"})
        task.mark_task_failure.assert_not_called()
        task.ensure_main.assert_called_once()

    def test_all_observations_failed_reports_failure(self):
        task = self.task()
        task.prepare_observation.side_effect = [{"name": "小池荷"}, {"name": "表象"}, None]
        task.navigate_observation.side_effect = RuntimeError("路线失败")
        self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        task.mark_task_failure.assert_called_once()
        message = task.mark_task_failure.call_args.args[0]
        self.assertIn("小池荷", message)
        self.assertIn("表象", message)
        self.assertEqual(task.ensure_main.call_count, 2)

    def test_failed_recovery_stops_further_observations(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "小池荷"}
        task.navigate_observation.side_effect = RuntimeError("路线失败")
        task.ensure_main.side_effect = RuntimeError("无法恢复主界面")
        self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        task.prepare_observation.assert_called_once()
        self.assertIn("无法恢复主界面", task.mark_task_failure.call_args.args[0])

    def test_success_before_failure_still_reports_success(self):
        task = self.task()
        task.prepare_observation.side_effect = [{"name": "小池荷"}, {"name": "表象"}, None]
        task.navigate_observation.side_effect = [None, RuntimeError("路线失败")]
        task.photograph_observation.side_effect = None
        task.photograph_observation.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        task.mark_task_failure.assert_not_called()
        task.ensure_main.assert_called_once()

    def test_failed_photo_does_not_block_successful_observation(self):
        task = self.task()
        task.prepare_observation.side_effect = [{"name": "小池荷"}, {"name": "表象"}, None]
        task.navigate_observation.side_effect = None
        task.photograph_observation.side_effect = [False, True]
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertEqual(task.completed_observations, {"pool_lotus", "thillu"})
        task.mark_task_failure.assert_not_called()
        task.ensure_main.assert_called_once()

    def test_daily_records_exception_without_recovering_main(self):
        task = self.task()
        task.prepare_observation.side_effect = RuntimeError("navigation failed")
        task.tr.side_effect = lambda text: text
        self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        task.ensure_main.assert_not_called()
        task.mark_task_failure.assert_called_once_with("环境监测执行失败：navigation failed")

    def test_daily_propagates_cancellation(self):
        task = self.task()
        task.prepare_observation.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        task.mark_task_failure.assert_not_called()
        task.ensure_main.assert_not_called()

    def task(self):
        task = Mock()
        task.OBSERVATIONS = EnvironmentMonitoringPhotoTask.OBSERVATIONS
        task._matches_observation.side_effect = lambda normalized, name, mode: EnvironmentMonitoringPhotoTask._matches_observation(task, normalized, name, mode)
        task.tr.side_effect = lambda text: text
        task.box = SimpleNamespace(right=Box(960, 0, 960, 1080))
        task.box_of_screen.side_effect = lambda x1, y1, x2, y2: Box(
            round(task.frame.shape[1] * x1),
            round(task.frame.shape[0] * y1),
            round(task.frame.shape[1] * (x2 - x1)),
            round(task.frame.shape[0] * (y2 - y1)),
        )
        task.ocr.return_value = []
        task.normalize_title.side_effect = EnvironmentMonitoringPhotoTask.normalize_title
        task.selected_observation = {"name": "小池荷"}
        task.prepare_photo.return_value = {"photo_ready": False, "photo_taken": False}
        task.lang = SimpleNamespace(environment_monitoring=SimpleNamespace(pool_lotus="小池荷", thillu="表象", ancient_tree="古树"))
        task.observation_key.side_effect = lambda title: EnvironmentMonitoringPhotoTask.observation_key(task, title)
        task.observation_route.side_effect = lambda title: EnvironmentMonitoringPhotoTask.observation_route(task, title)

        def navigate_observation(observation):
            route_class = task.observation_route(observation["name"])
            if callable(route_class) and task.navigation is None:
                task.navigation = route_class(task)
            return EnvironmentMonitoringPhotoTask.navigate_observation(task, observation)

        task.navigate_observation.side_effect = navigate_observation
        task.photograph_observation.side_effect = lambda: EnvironmentMonitoringPhotoTask.photograph_observation(task)
        task.process_observation.side_effect = lambda observation: EnvironmentMonitoringPhotoTask.process_observation(
            task, observation
        )
        task._resolve_config_key.side_effect = lambda key, category: {"r": "f6", "w": "up", "d": "right"}.get(key, key)
        task.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        task.next_frame.side_effect = lambda: task.frame
        task._camera_open.return_value = False
        task.screenshot = Mock()
        task.normalize_pos.side_effect = lambda point: (round(1920 * point[0]), round(1080 * point[1]))
        return task

    def test_movement_cancellation_or_exception_always_releases_resolved_key(self):
        for error in (TaskDisabledException(), RuntimeError("input interrupted")):
            with self.subTest(error=type(error).__name__):
                task = self.task()
                task.sleep.side_effect = error
                navigation = EnvironmentMonitoringRoute(task)
                with self.assertRaises(type(error)):
                    navigation.hold_route_key("w", 0.5)
                task.send_key_down.assert_called_once_with("up")
                task.send_key_up.assert_called_once_with("up")

    def test_failed_focus_prevents_movement_key_down(self):
        task = self.task()
        task._require_input_focus.side_effect = RuntimeError("no focus")
        navigation = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "no focus"):
            navigation.hold_route_key("d", 3)
        task.send_key_down.assert_not_called()
        task.sleep.assert_not_called()

    def test_tool_selection_cancellation_and_drag_failure_release_tool(self):
        for failing_method, error in (
            ("wait_ui_stable", TaskDisabledException()),
            ("smooth_drag", RuntimeError("drag interrupted")),
        ):
            with self.subTest(failing_method=failing_method):
                task = self.task()
                getattr(task, failing_method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.open_camera(task)
                task.send_key_down.assert_called_once_with("f6")
                task.send_key_up.assert_called_once_with("f6")
                task.press_key.assert_not_called()

    def test_selecting_camera_waits_after_tool_release_before_short_press(self):
        task = self.task()
        task.wait_until.side_effect = [False, True]
        events = Mock()
        events.attach_mock(task.send_key_up, "release")
        events.attach_mock(task.wait_until, "observe")
        events.attach_mock(task.press_key, "press")
        EnvironmentMonitoringPhotoTask.open_camera(task)
        tail = events.mock_calls
        self.assertEqual(tail[0], call.release("f6"))
        self.assertIs(tail[1].args[0], task._camera_open)
        self.assertGreater(tail[1].kwargs["time_out"], 0)
        self.assertEqual(tail[2], call.press("r", down_time=0.15))
        self.assertIs(tail[3].args[0], task._camera_open)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.click.assert_not_called()

    def test_existing_camera_ui_never_reopens_tool_wheel_or_presses_r(self):
        task = self.task()
        task._camera_open.return_value = True
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task._require_input_focus.assert_not_called()
        task.send_key_down.assert_not_called()
        task.wait_ocr.assert_not_called()
        task.press_key.assert_not_called()

    def test_camera_ui_appearing_after_selection_does_not_press_r_again(self):
        task = self.task()
        task.wait_until.return_value = True
        EnvironmentMonitoringPhotoTask.open_camera(task)
        task.send_key_up.assert_called_once_with("f6")
        task.press_key.assert_not_called()

    def test_camera_layout_requires_both_icons_in_the_supplied_frame(self):
        for escape_present, zoom_present in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(escape=escape_present, zoom=zoom_present):
                task = self.task()
                supplied_frame = np.full_like(task.frame, 170)
                present = {fL.esc: escape_present, fL.plus_button: zoom_present}
                task.find_one.side_effect = lambda feature, present=present, **kwargs: present[feature]
                self.assertEqual(
                    EnvironmentMonitoringPhotoTask._camera_open(task, frame=supplied_frame),
                    escape_present and zoom_present,
                )
                for detection in task.find_one.call_args_list:
                    self.assertIs(detection.kwargs["frame"], supplied_frame)
                    self.assertNotIn("threshold", detection.kwargs)
                task.ocr.assert_not_called()

    def test_failed_camera_entry_is_not_reported_as_open(self):
        task = self.task()
        task.wait_until.side_effect = [False, False]
        with self.assertRaisesRegex(RuntimeError, "未进入拍照界面"):
            EnvironmentMonitoringPhotoTask.open_camera(task)
        task.press_key.assert_called_once_with("r", down_time=0.15)
        task.send_key_up.assert_called_once_with("f6")

    def test_camera_ui_is_required_before_checking_the_marker(self):
        task = self.task()
        task._camera_open.return_value = False
        self.assertFalse(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
        task.find_one.assert_not_called()
        task.ocr.assert_not_called()

    def test_photo_condition_uses_checked_marker_without_ocr(self):
        for marker_found in (False, True):
            with self.subTest(marker=marker_found):
                task = self.task()
                task._camera_open.return_value = True
                task.find_one.return_value = Box(52, 726, 26, 24) if marker_found else None
                self.assertEqual(EnvironmentMonitoringPhotoTask.photo_condition_met(task), marker_found)
                self.assertEqual(task.find_one.call_args.args[0], fL.photo_target_checked)
                task.ocr.assert_not_called()

    def test_marker_keeps_the_camera_detection_frame(self):
        task = self.task()
        original_frame = task.frame

        def camera_detected(**kwargs):
            self.assertIs(kwargs["frame"], original_frame)
            task.frame = np.full_like(original_frame, 255)
            return True

        task._camera_open.side_effect = camera_detected
        task.find_one.return_value = Box(52, 726, 26, 24)
        self.assertTrue(EnvironmentMonitoringPhotoTask.photo_condition_met(task))
        self.assertIs(task.find_one.call_args.kwargs["frame"], original_frame)
        task.ocr.assert_not_called()

    def test_route_requires_world_and_does_not_move_in_a_menu(self):
        task = self.task()
        navigation = PoolLotusRoute(task)
        task.in_world.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未处于大世界"):
            navigation.run()
        task.send_key_down.assert_not_called()
        task.press_key.assert_not_called()

    def test_unstable_world_after_route_prevents_tracking_key(self):
        for title in ("小池荷", "蓄水源石虫"):
            with self.subTest(title=title):
                task = self.task()
                task.lang.environment_monitoring.cistern_originium_slugs = "蓄水源石虫"
                task.prepare_observation.side_effect = [{"name": title}, None]
                task.in_world.side_effect = [True, False]
                task.wait_until.side_effect = lambda condition, **kwargs: condition()
                self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
                self.assertIn("路线结束后未恢复大世界", task.mark_task_failure.call_args.args[0])
                task.prepare_photo.assert_not_called()
                task.take_photo.assert_not_called()
                self.assertEqual(task.send_key_up.call_args_list, task.send_key_down.call_args_list)
                self.assertNotIn(call("v", after_sleep=0), task.press_key.call_args_list)

    def test_route_uses_world_state_without_mode_checks(self):
        task = self.task()
        navigation = PoolLotusRoute(task)
        task.in_world.return_value = True
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        navigation.run()
        self.assertEqual(task.send_key_down.call_args_list, [call("up"), call("right")])
        self.assertEqual(task.send_key_up.call_args_list, [call("up"), call("right")])
        self.assertEqual(task.sleep.call_args_list, [call(0.5), call(3)])
        self.assertGreater(task.wait_until.call_args.kwargs["settle_time"], 0)
        self.assertIs(task.wait_until.call_args.args[0], task.in_world)
        task.press_key.assert_not_called()
        task.ocr.assert_not_called()

    def test_camera_lost_prevents_any_rotation_gesture(self):
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, "相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
        task.mouse_down.assert_not_called()
        task.smooth_drag.assert_not_called()
        task.mouse_up.assert_not_called()

    def test_rotation_cancellation_or_drag_failure_releases_mouse(self):
        for method, error in (("smooth_drag", TaskDisabledException()), ("smooth_drag", RuntimeError("drag failed"))):
            with self.subTest(method=method):
                task = self.task()
                task._camera_open.return_value = True
                getattr(task, method).side_effect = error
                with self.assertRaises(type(error)):
                    EnvironmentMonitoringPhotoTask.rotate_photo_view(task)
                task.mouse_down.assert_called_once()
                task.mouse_up.assert_called_once_with(key="left")

    def test_slug_photo_adjustments_are_bounded_and_keep_observation_name(self):
        task = self.task()
        task.lang.environment_monitoring.cistern_originium_slugs = "蓄水源石虫"
        task.selected_observation = {"name": "蓄水源石虫"}
        task.navigation = CisternOriginiumSlugsRoute(task)
        task.photo_condition_met.return_value = False
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        self.assertFalse(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_args_list, [call(direction="left")] * 8)
        task._camera_open.return_value = True
        result = EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertEqual(result["name"], "蓄水源石虫")
        self.assertFalse(result["photo_taken"])
        task.press_key.assert_not_called()
        task.click.assert_not_called()

    def test_leftward_rotation_keeps_height_and_releases_mouse(self):
        task = self.task()
        task._camera_open.return_value = True
        EnvironmentMonitoringPhotoTask.rotate_photo_view(task, direction="left")
        start, end = task.smooth_drag.call_args.args
        self.assertLess(end[0], start[0])
        self.assertEqual(end[1], start[1])
        task.mouse_up.assert_called_once_with(key="left")

    def test_waterfall_route_and_upward_photo_search(self):
        task = self.task()
        task.in_world.return_value = True
        task.wait_until.return_value = True
        AnnularWaterfallRoute(task).run()
        self.assertEqual(task.sleep.call_args_list, [call(0.5), call(5), call(2), call(3)])
        self.assertEqual(task.send_key_down.call_args_list, [call("a"), call("up"), call("right"), call("up")])
        self.assertEqual(task.press_key.call_count, 3)
        task._camera_open.return_value = True
        EnvironmentMonitoringPhotoTask.rotate_photo_view(task, direction="up")
        start, end = task.smooth_drag.call_args.args
        self.assertEqual(start[0], end[0])
        self.assertLess(end[1], start[1])
        task.mouse_up.assert_called_once_with(key="left")

    def test_waterfall_uses_upward_search(self):
        task = self.task()
        task.lang.environment_monitoring.annular_waterfall = "环形瀑布"
        task.selected_observation = {"name": "环形瀑布"}
        task.navigation = AnnularWaterfallRoute(task)
        task.photo_condition_met.side_effect = [False, True]
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        self.assertIs(task.observation_route("环形瀑布"), AnnularWaterfallRoute)
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        task.rotate_photo_view.assert_called_once_with(direction="up")

    def test_slug_navigation_stops_on_cancellation_and_releases_bound_backward_key(self):
        task = self.task()
        task._resolve_config_key.side_effect = None
        task._resolve_config_key.return_value = "down"
        task.sleep.side_effect = TaskDisabledException()
        navigation = CisternOriginiumSlugsRoute(task)
        with self.assertRaises(TaskDisabledException):
            navigation.run()
        task.send_key_down.assert_called_once_with("down")
        task.send_key_up.assert_called_once_with("down")
        task.wait_until.assert_not_called()

    def test_thillu_navigation_stays_at_transfer_point(self):
        task = self.task()
        task.in_world.return_value = True
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        StationaryObservationRoute(task).run()
        task.send_key_down.assert_not_called()
        task.press_key.assert_not_called()
        task.wait_until.assert_called_once()
        self.assertIs(task.wait_until.call_args.args[0], task.in_world)
        self.assertGreater(task.wait_until.call_args.kwargs["settle_time"], 0)

    def test_slug_route_runs_backward_then_enters_shared_camera_flow(self):
        task = self.task()
        task.lang.environment_monitoring.cistern_originium_slugs = "蓄水源石虫"
        task.prepare_observation.side_effect = [{"name": "蓄水源石虫"}, None]
        task.in_world.return_value = True
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        events = Mock()
        for name in ("send_key_down", "press_key", "sleep", "send_key_up", "wait_until", "prepare_photo"):
            events.attach_mock(getattr(task, name), name)
        EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
        self.assertEqual(
            [c[0] for c in events.mock_calls],
            ["send_key_down", "press_key", "sleep", "send_key_up", "wait_until", "prepare_photo"],
        )
        task.send_key_down.assert_called_once_with("s")
        task.sleep.assert_called_once_with(7)
        task.press_key.assert_called_once_with("shift", after_sleep=0.05)

    def test_rotation_stops_immediately_when_condition_becomes_satisfied(self):
        for failed_attempts in (0, 2):
            with self.subTest(failed_attempts=failed_attempts):
                task = self.task()
                task.photo_condition_met.side_effect = [False] * failed_attempts + [True]
                task.wait_until.side_effect = lambda condition, **kwargs: condition()
                self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
                self.assertEqual(task.rotate_photo_view.call_count, failed_attempts)

    def test_unsatisfied_condition_has_at_most_eight_rotations(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        self.assertFalse(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.wait_until.call_count, 8)
        task.press_key.assert_not_called()
        task.send_key_down.assert_not_called()

    def test_initial_condition_wait_avoids_rotation(self):
        task = self.task()
        task.photo_condition_met.return_value = True
        events = Mock()
        events.attach_mock(task.wait_ui_stable, "wait")
        events.attach_mock(task.photo_condition_met, "condition")
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        self.assertEqual([item[0] for item in events.mock_calls], ["wait", "condition"])
        task.rotate_photo_view.assert_not_called()

    def test_unstable_target_area_prevents_rotation(self):
        task = self.task()
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "目标条件区域未稳定"):
            EnvironmentMonitoringPhotoTask.adjust_photo_view(task)
        task.rotate_photo_view.assert_not_called()

    def test_full_run_takes_photo_only_when_ready(self):
        for ready in (False, True):
            with self.subTest(ready=ready), patch.object(PoolLotusRoute, "run") as route:
                task = self.task()
                task.prepare_observation.side_effect = [{"name": "“小池荷”", "terminal": "outskirts"}, None]
                task.adjust_photo_view.return_value = ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = ready
                task.wait_until.side_effect = lambda condition, **kwargs: condition()
                task.prepare_photo.side_effect = lambda task=task: EnvironmentMonitoringPhotoTask.prepare_photo(task)
                stages = Mock()
                stages.attach_mock(task.prepare_observation, "prepare_observation")
                stages.attach_mock(route, "navigate")
                stages.attach_mock(task.prepare_photo, "prepare_photo")
                result = EnvironmentMonitoringPhotoTask.run.__wrapped__(task)
                self.assertEqual(task.prepare_observation.call_count, 2)
                route.assert_called_once_with()
                self.assertEqual(
                    stages.mock_calls,
                    [call.prepare_observation(), call.navigate(), call.prepare_photo(), call.prepare_observation()],
                )
                task.open_camera.assert_called_once_with()
                task.adjust_photo_view.assert_called_once_with()
                self.assertIs(result, ready)
                if not ready:
                    self.assertIn("未能调整到满足条件的取景角度", task.mark_task_failure.call_args.args[0])
                self.assertEqual(task.take_photo.call_count, int(ready))
                task.press_key.assert_not_called()
                task.click.assert_not_called()
                task.click_relative.assert_not_called()
                task.send_key_down.assert_not_called()

    def test_run_processes_multiple_observations(self):
        task = self.task()
        task.prepare_observation.side_effect = [
            {"name": "小池荷", "terminal": "outskirts"},
            {"name": "表象", "terminal": "marker_stone"},
            None,
        ]
        task.navigate_observation.side_effect = None
        task.photograph_observation.side_effect = None
        task.photograph_observation.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertEqual(task.navigate_observation.call_count, 2)
        self.assertEqual(task.photograph_observation.call_count, 2)
        self.assertEqual(task.completed_observations, {"pool_lotus", "thillu"})

    def test_run_stops_after_six_observations(self):
        task = self.task()
        task.prepare_observation.side_effect = [{"name": str(index), "terminal": "outskirts"} for index in range(7)]
        task.navigate_observation.side_effect = None
        task.photograph_observation.side_effect = None
        task.photograph_observation.return_value = True
        self.assertTrue(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertEqual(task.prepare_observation.call_count, 6)
        self.assertEqual(task.photograph_observation.call_count, 6)

    def test_photo_rechecks_condition_and_does_not_click_when_lost(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        with self.assertRaisesRegex(RuntimeError, "条件已失效"):
            EnvironmentMonitoringPhotoTask.take_photo(task)
        task.click_relative.assert_not_called()
        task.click.assert_not_called()

    def test_photo_waits_for_condition_to_recover_before_shutter(self):
        task = self.task()
        task.photo_condition_met.side_effect = [False, True]
        task.wait_feature.return_value = Box(1835, 26, 28, 28)
        task._camera_open.return_value = True

        def wait_for_condition(condition, **kwargs):
            if condition == task.photo_condition_met:
                self.assertEqual(kwargs, {"time_out": 2, "settle_time": 0, "raise_if_not_found": False})
                self.assertFalse(condition())
                task.click_relative.assert_not_called()
                return condition()
            return condition()

        task.wait_until.side_effect = wait_for_condition
        EnvironmentMonitoringPhotoTask.take_photo(task)
        self.assertEqual(task.photo_condition_met.call_count, 2)
        task.click_relative.assert_called_once_with(0.856, 0.803)

    def test_photo_closes_preview_without_checking_collection(self):
        for camera, world in ((True, False), (False, True)):
            with self.subTest(camera=camera, world=world):
                task = self.task()
                close = Box(1835, 26, 28, 28)
                task.wait_feature.return_value = close
                task._camera_open.return_value = camera
                task.in_world.return_value = world
                task.wait_until.side_effect = lambda condition, **kwargs: condition()
                EnvironmentMonitoringPhotoTask.take_photo(task)
                task.click_relative.assert_called_once()
                task.click.assert_called_once_with(close)
                task.open_monitoring.assert_not_called()
                task.ocr.assert_not_called()
                task.ensure_main.assert_not_called()

    def test_missing_photo_preview_never_repeats_shutter_or_closes_another_page(self):
        task = self.task()
        task.wait_feature.return_value = None
        with self.assertRaisesRegex(RuntimeError, "未出现照片预览"):
            EnvironmentMonitoringPhotoTask.take_photo(task)
        task.click_relative.assert_called_once()
        task.click.assert_not_called()

    def test_failure_to_close_photo_preview_is_reported(self):
        task = self.task()
        task._camera_open.return_value = False
        task.in_world.return_value = False
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        with self.assertRaisesRegex(RuntimeError, "照片预览未关闭"):
            EnvironmentMonitoringPhotoTask.take_photo(task)

    def test_navigation_failure_stops_before_camera(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "小池荷"}
        with patch.object(PoolLotusRoute, "run", side_effect=RuntimeError("navigation failed")):
            self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertIn("navigation failed", task.mark_task_failure.call_args.args[0])
        task.prepare_photo.assert_not_called()

    def test_missing_registered_route_stops_before_camera(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "古树"}
        with patch.dict(
            EnvironmentMonitoringPhotoTask.OBSERVATIONS,
            {"ancient_tree": (None, "right")},
        ):
            self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertIn("未支持观察点导航", task.mark_task_failure.call_args.args[0])
        task.prepare_photo.assert_not_called()

    def test_unregistered_observation_stops_before_camera(self):
        task = self.task()
        task.prepare_observation.return_value = {"name": "未知点位"}
        self.assertFalse(EnvironmentMonitoringPhotoTask.run.__wrapped__(task))
        self.assertIn("未支持观察点导航", task.mark_task_failure.call_args.args[0])
        task.prepare_photo.assert_not_called()

    def test_camera_lost_after_final_rotation_cannot_be_reported_as_open(self):
        task = self.task()
        task.photo_condition_met.return_value = False
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        task.adjust_photo_view.side_effect = lambda: EnvironmentMonitoringPhotoTask.adjust_photo_view(task)
        with self.assertRaisesRegex(RuntimeError, "取景结束时相机界面已关闭"):
            EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertEqual(task.rotate_photo_view.call_count, 8)
        self.assertEqual(task.next_frame.call_count, 2)
        task._camera_open.assert_called_once_with(frame=task.frame)
        task.ocr.assert_not_called()
        self.assertFalse(any("camera_open=True" in c.args[0] for c in task.log_info.call_args_list))

    def test_delayed_checked_marker_stops_before_another_rotation(self):
        task = self.task()
        task.photo_condition_met.side_effect = [False, False, True]
        task.wait_until.side_effect = lambda condition, **kwargs: condition() or condition()
        self.assertTrue(EnvironmentMonitoringPhotoTask.adjust_photo_view(task))
        task.rotate_photo_view.assert_called_once()
        task.wait_until.assert_called_once_with(
            task.photo_condition_met, time_out=1, settle_time=0, raise_if_not_found=False
        )

    def test_final_frame_decides_ready_instead_of_the_previous_search_result(self):
        for search_ready, final_ready in ((True, False), (False, True)):
            with self.subTest(search_ready=search_ready, final_ready=final_ready):
                task = self.task()
                task.adjust_photo_view.return_value = search_ready
                task._camera_open.return_value = True
                task.photo_condition_met.return_value = final_ready
                task.wait_until.side_effect = lambda condition, **kwargs: condition()
                result = EnvironmentMonitoringPhotoTask.prepare_photo(task)
                self.assertEqual(result["photo_ready"], final_ready)
                task._camera_open.assert_called_once_with(frame=task.frame)
                task.wait_until.assert_called_once_with(
                    task.photo_condition_met, time_out=2, settle_time=0, raise_if_not_found=False
                )
                task.navigation.run.assert_not_called()
                task.press_key.assert_not_called()
                task.click.assert_not_called()

    def test_final_condition_can_recover_after_a_temporary_disappearance(self):
        task = self.task()
        task._camera_open.return_value = True
        task.photo_condition_met.side_effect = [False, True]
        task.wait_until.side_effect = lambda condition, **kwargs: condition() or condition()
        result = EnvironmentMonitoringPhotoTask.prepare_photo(task)
        self.assertTrue(result["photo_ready"])
        task.wait_until.assert_called_once_with(
            task.photo_condition_met, time_out=2, settle_time=0, raise_if_not_found=False
        )


if __name__ == "__main__":
    unittest.main()
