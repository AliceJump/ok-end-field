"""Environment monitoring must select one matching card and verify transfer completion."""

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok import Box
from ok.util.color import calculate_color_percentage

from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.EnvironmentMonitoringRoute import (
    CisternOriginiumSlugsRoute,
    EternalSunsetRoute,
    EnvironmentMonitoringRoute,
    PoolLotusRoute,
    StationaryObservationRoute,
)
from src.tasks.onetime.EnvironmentMonitoringPhotoTask import EnvironmentMonitoringPhotoTask


class TestEnvironmentMonitoringEntry(unittest.TestCase):
    def task(self, width=1920, height=1080):
        task = object.__new__(EnvironmentMonitoringPhotoTask)
        task._executor = SimpleNamespace(frame=np.zeros((height, width, 3), dtype=np.uint8))
        task.lang = SimpleNamespace(
            environment_monitoring=SimpleNamespace(
                pool_lotus="小池荷",
                peach_tree="桃树",
            )
        )
        task.box = SimpleNamespace(bottom_right=Box(width // 2, height // 2, width // 2, height // 2))
        task.box.right = Box(width // 2, 0, width // 2, height)
        task.box.top = Box(0, 0, width, height // 2)
        task.box.top_left = Box(0, 0, width // 2, height // 2)
        task.box_of_screen = lambda x1, y1, x2, y2: Box(
            round(width * x1), round(height * y1), round(width * (x2 - x1)), round(height * (y2 - y1))
        )
        task.ocr = Mock(return_value=[])
        task.ensure_main = Mock()
        task.to_model_area = Mock(return_value=True)
        task.click = Mock()
        task.click_relative = Mock()
        task.wait_ocr = Mock(return_value=[Box(1200, 100, 200, 40)])
        task.wait_click_ocr = Mock(return_value=True)
        task.wait_click_feature = Mock(return_value=True)
        task.click_until_color = Mock(return_value=True)
        task.find_one = Mock(return_value=None)
        task._monitoring_page = Mock(return_value=True)
        task._task_detail_page = Mock(return_value=True)
        task.wait_feature = Mock(return_value=True)
        task.wait_ui_stable = Mock(return_value=True)
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())
        task.to_near_transfer_point = Mock(return_value=True)
        task._wait_for_transfer_arrival = Mock(return_value=True)
        task.log_info = Mock()
        task.selected_observation = {"name": "previous run"}
        task.completed_observations = set()
        return task

    def test_peach_tree_uses_stationary_navigation(self):
        task = self.task()
        self.assertIs(task.observation_route("念念家的桃树"), StationaryObservationRoute)
        self.assertIs(task.observation_route("桃树"), StationaryObservationRoute)

    def test_pier_waterwheel_uses_stationary_navigation_in_each_locale(self):
        path = Path("assets/lang/environment_monitoring.json")
        names = json.loads(path.read_text(encoding="utf-8"))["pier_waterwheel"]
        titles = {
            "zh_CN": "码头水车",
            "zh_TW": "碼頭水車",
            "en_US": "Pier Waterwheel",
            "ja_JP": "渡し場の水車",
            "ko_KR": "부두 물레방아",
        }
        for locale, title in titles.items():
            with self.subTest(locale=locale):
                task = self.task()
                task.lang.environment_monitoring.pier_waterwheel = names[locale]["string"]
                self.assertIs(task.observation_route(title), StationaryObservationRoute)

    def test_peach_tree_localized_titles(self):
        path = Path("assets/lang/environment_monitoring.json")
        names = json.loads(path.read_text(encoding="utf-8"))["peach_tree"]
        titles = {
            "zh_CN": "念念家的桃树",
            "zh_TW": "念念家的桃樹",
            "en_US": "Peach Tree at Babbles's Home",
            "ja_JP": "ネンネンの家にある桃の木",
            "ko_KR": "염념이네 복숭아나무",
        }
        for locale, title in titles.items():
            with self.subTest(locale=locale):
                task = self.task()
                task.lang.environment_monitoring.peach_tree = names[locale]["string"]
                self.assertIs(task.observation_route(title), StationaryObservationRoute)

    def color_task(self, frame):
        height, width = frame.shape[:2]
        task = self.task(width, height)
        task.executor.frame = frame
        task.calculate_color_percentage = Mock(
            side_effect=lambda color, box: calculate_color_percentage(frame, color, box)
        )
        return task

    def test_completed_card_is_skipped_across_terminals(self):
        task = self.task()
        task.completed_observations = {"小池荷"}
        task.open_monitoring = Mock()
        task.select_terminal = Mock()
        task.available_observations = Mock(
            side_effect=[
                [{"name": "“小池荷”", "action_box": Box(0, 0, 1, 1)}],
                [{"name": "小池荷", "action_box": Box(1, 0, 1, 1)}],
            ]
        )
        task.enter_observation = Mock()
        task.open_observation_map = Mock()
        result = task.prepare_observation()
        self.assertIsNone(result)
        task.enter_observation.assert_not_called()

    def test_available_cards_bind_buttons_to_their_own_titles_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                task = self.task(width, height)
                titles = [
                    Box(round(width * x), round(height * 0.30), 100, 20, name=name)
                    for x, name in ((0.06, "古树"), (0.37, "“小池荷”"), (0.68, "枯枝"))
                ]

                # The completed first card has a title but no action; the second and third are actionable.
                task.ocr.return_value = [*titles, Box(10, 10, 100, 20, name="环境监测终端")]
                task._observation_action = Mock(side_effect=[None, "accept", "go"])
                candidates = task.available_observations()
                task.ocr.assert_called_once_with()
                self.assertEqual([item["name"] for item in candidates], ["“小池荷”", "枯枝"])
                for candidate, title in zip(candidates, titles[1:], strict=True):
                    area = candidate["action_box"]
                    self.assertLessEqual(area.x, title.x)
                    self.assertGreater(area.x + area.width, title.x)
                    self.assertGreater(area.y, title.y)

    def test_description_near_title_boundary_does_not_count_as_second_title(self):
        task = self.task()
        task._observation_action = Mock(side_effect=["go", None, None])
        task.ocr.return_value = [
            Box(104, 318, 160, 40, name="环形瀑布"),
            Box(110, 361, 280, 25, name="拍摄祖泉周边的环形瀑布"),
        ]
        self.assertEqual([item["name"] for item in task.available_observations()], ["环形瀑布"])

    def test_title_is_selected_above_other_ocr_text(self):
        task = self.task()
        task._observation_action = Mock(side_effect=["go", None, None])
        task.ocr.return_value = [Box(110, 350, 200, 10, name="说明"), Box(104, 318, 160, 28, name="环形瀑布")]
        self.assertEqual([item["name"] for item in task.available_observations()], ["环形瀑布"])

    def test_pool_lotus_title_selects_its_own_column(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            for target_column in range(3):
                with self.subTest(size=(width, height), target_column=target_column):
                    task = self.task(width, height)
                    task.ocr.return_value = [
                        Box(
                            round(width * (0.06 + i * 0.304)),
                            round(height * 0.30),
                            100,
                            20,
                            name="“小池荷”" if i == target_column else "其他观察点",
                        )
                        for i in range(3)
                    ]
                    task._observation_action = Mock(return_value="go")
                    candidates = task.available_observations()
                    task.ocr.assert_called_once_with()
                    matches = [item for item in candidates if task.normalize_title(item["name"]) == "小池荷"]
                    self.assertEqual(len(matches), 1)
                    expected_left = round(width * (0.049 + target_column * 0.304))
                    self.assertEqual(matches[0]["action_box"].x, expected_left)

    def test_terminal_highlight_identifies_only_the_selected_tab_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            for selected in ("outskirts", "marker_stone"):
                with self.subTest(size=(width, height), selected=selected):
                    frame = np.full((height, width, 3), 40, dtype=np.uint8)
                    x1, x2 = (0.046, 0.246) if selected == "outskirts" else (0.25, 0.45)
                    frame[int(height * 0.1) : int(height * 0.172), int(width * x1) : int(width * x2)] = 235
                    task = self.color_task(frame)
                    for terminal in ("outskirts", "marker_stone"):
                        self.assertEqual(
                            EnvironmentMonitoringPhotoTask._terminal_selected(task, terminal), terminal == selected
                        )

    def test_colored_tab_or_sparse_white_text_is_not_a_selected_background(self):
        for color in ((255, 255, 50), (255, 50, 50), (180, 180, 180)):
            with self.subTest(color=color):
                frame = np.full((1080, 1920, 3), color, dtype=np.uint8)
                frame[::3, ::3] = 255
                task = self.color_task(frame)
                self.assertFalse(EnvironmentMonitoringPhotoTask._terminal_selected(task, "outskirts"))

    def monitoring_frame(self, width, height, selected=("outskirts",)):
        frame = np.full((height, width, 3), 40, dtype=np.uint8)
        for terminal, left in (("outskirts", 0.06), ("marker_stone", 0.27)):
            if terminal in selected:
                frame[int(height * 0.145) : int(height * 0.16), int(width * left) : int(width * (left + 0.07))] = 235
        for left in (0.046, 0.25):
            x, y = round(width * left), round(height * 0.105)
            frame[y : y + round(height * 0.062), x : x + round(width * 0.003)] = (180, 180, 0)
        return frame

    def test_monitoring_page_requires_templates_two_stripes_and_one_selected_tab(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            for selected in ((), ("outskirts",), ("marker_stone",), ("outskirts", "marker_stone")):
                with self.subTest(size=(width, height), selected=selected):
                    task = self.color_task(self.monitoring_frame(width, height, selected))
                    del task._monitoring_page
                    task.find_one.return_value = Box(0, 0, 10, 10)
                    self.assertEqual(task._monitoring_page(), len(selected) == 1)
                    self.assertEqual([c.args[0] for c in task.find_one.call_args_list], [fL.q, fL.e])
                    for matched in task.find_one.call_args_list:
                        self.assertTrue(matched.kwargs["use_gray_scale"])
                        self.assertNotIn("threshold", matched.kwargs)

    def test_monitoring_page_rejects_missing_controls_or_stripe_even_with_white_tab(self):
        for missing in (fL.q, fL.e, "left_stripe", "right_stripe"):
            with self.subTest(missing=missing):
                frame = self.monitoring_frame(1920, 1080)
                if missing in ("left_stripe", "right_stripe"):
                    left = 0.046 if missing == "left_stripe" else 0.25
                    x = round(1920 * left)
                    frame[:, x : x + round(1920 * 0.003)] = 40
                task = self.color_task(frame)
                del task._monitoring_page
                task.find_one.side_effect = lambda feature, missing=missing, **kwargs: (
                    None if feature == missing else Box(0, 0, 10, 10)
                )
                self.assertFalse(task._monitoring_page())

    def test_card_button_colors_reject_dark_gray_and_sparse_white_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            for color, state in (
                ((0, 240, 255), "accept"),
                ((236, 236, 236), "go"),
                ((219, 219, 219), None),
                ((40, 40, 40), None),
            ):
                with self.subTest(size=(width, height), color=color):
                    frame = np.full((height, width, 3), 40, dtype=np.uint8)
                    task = self.color_task(frame)
                    for index in range(3):
                        left = 0.049 + index * 0.304
                        area = task.box_of_screen(left, 0.85, left + 0.293, 0.925)
                        frame[area.y : area.y + area.height, area.x : area.x + area.width] = color
                        self.assertEqual(task._observation_action(area), state)
                        frame[area.y : area.y + area.height, area.x : area.x + area.width] = 40
                        frame[area.y : area.y + area.height : 3, area.x : area.x + area.width : 3] = 255
                        self.assertIsNone(task._observation_action(area))

    def test_unselected_or_unstable_terminal_cannot_scan_old_cards(self):
        for selected, stable in ((False, True), (True, False)):
            with self.subTest(selected=selected, stable=stable):
                task = self.prepare_task([{"name": "小池荷"}], [])
                del task.select_terminal
                task._terminal_selected = Mock(return_value=selected)
                task.wait_ui_stable.return_value = stable
                with self.assertRaisesRegex(RuntimeError, "终端切换未完成"):
                    task.prepare_observation()
                task.wait_click_ocr.assert_not_called()
                task.available_observations.assert_not_called()
                task.enter_observation.assert_not_called()
                task.open_observation_map.assert_not_called()
                self.assertIsNone(task.selected_observation)

    def test_terminal_selection_waits_for_color_before_scanning(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.select_terminal
        task._terminal_selected = Mock(return_value=True)
        self.assertEqual(task.prepare_observation()["name"], "小池荷")
        task.click_until_color.assert_called_once()
        task.wait_click_ocr.assert_not_called()
        task.wait_ui_stable.assert_called_once_with()

    def test_already_selected_stable_terminal_skips_click_and_ocr(self):
        task = self.task()
        task._terminal_selected = Mock(return_value=True)
        task.select_terminal("marker_stone")
        task.click.assert_not_called()
        task.ocr.assert_not_called()
        task.wait_click_ocr.assert_not_called()
        task.wait_ui_stable.assert_called_once_with()

    def test_terminal_selection_requires_monitoring_page_before_fixed_click(self):
        task = self.task()
        task._monitoring_page.return_value = False
        with self.assertRaisesRegex(RuntimeError, "切换终端时未处于环境监测终端列表"):
            task.select_terminal("marker_stone")
        task.click.assert_not_called()

    def test_terminal_page_disappearing_during_wait_prevents_scanning(self):
        task = self.task()
        task._monitoring_page.side_effect = [True, False]
        task._terminal_selected = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "终端切换未完成"):
            task.select_terminal("marker_stone")
        task.click_until_color.assert_called_once()

    def test_unstable_monitoring_page_stops_before_terminal_selection(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.open_monitoring
        task.find_one.return_value = Box(10, 10, 20, 20)
        task.wait_ui_stable.side_effect = [True, False]
        with self.assertRaisesRegex(RuntimeError, "页面未稳定"):
            task.prepare_observation()
        task.select_terminal.assert_not_called()
        task.available_observations.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_monitoring_page_detection_failure_stops_before_terminal_selection(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.open_monitoring
        task.find_one.return_value = Box(10, 10, 20, 20)
        task._monitoring_page.return_value = False
        with self.assertRaisesRegex(RuntimeError, "终端列表"):
            task.prepare_observation()
        task.select_terminal.assert_not_called()
        task.wait_ocr.assert_not_called()

    def test_monitoring_entry_template_is_clicked_without_ocr(self):
        task = self.task()
        task.open_monitoring()
        task.to_model_area.assert_called_once_with("武陵")
        task.wait_click_feature.assert_called_once_with(
            fL.environment_monitoring_enter, box=task.box.right, time_out=5, raise_if_not_found=False
        )
        task.click_relative.assert_not_called()
        task.wait_click_ocr.assert_not_called()
        task.wait_ocr.assert_not_called()
        self.assertEqual(task.wait_ui_stable.call_count, 2)

    def test_missing_monitoring_template_prevents_entry_click(self):
        task = self.task()
        task.wait_click_feature.return_value = None
        with self.assertRaisesRegex(RuntimeError, "未找到环境监测入口"):
            task.open_monitoring()
        task.click.assert_not_called()
        task.wait_click_ocr.assert_not_called()

    def test_unstable_overview_cannot_use_fixed_entry_position(self):
        task = self.task()
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "地区建设页面未稳定"):
            task.open_monitoring()
        task.click_relative.assert_not_called()
        task.wait_click_ocr.assert_not_called()

    def test_completed_terminal_without_action_buttons_does_not_read_titles(self):
        task = self.task()
        task._observation_action = Mock(return_value=None)
        self.assertEqual(task.available_observations(), [])
        task.ocr.assert_not_called()

    def test_split_title_does_not_select_an_unsupported_fragment(self):
        task = self.task()
        task.ocr.return_value = [Box(120, 325, 100, 20, name="小池"), Box(240, 325, 40, 20, name="荷")]
        task._observation_action = Mock(side_effect=["accept", None, None])
        candidate = task.available_observations()[0]
        self.assertIsNone(task.observation_route(candidate["name"]))

    def test_accept_and_go_remain_inside_the_selected_card(self):
        task = self.task()
        area = Box(678, 918, 562, 81)
        task._observation_action = Mock(side_effect=["accept", "go"])
        task.enter_observation({"name": "“小池荷”", "action_box": area})
        task.ocr.assert_not_called()
        self.assertEqual(task.click.call_args_list, [call(area), call(area)])
        self.assertEqual(task._observation_action.call_args_list, [call(area), call(area)])
        self.assertEqual(task.wait_until.call_args_list[0].kwargs["settle_time"], 0.3)
        task.wait_ocr.assert_not_called()
        task._task_detail_page.assert_called_once_with()

    def test_task_detail_page_requires_both_existing_templates(self):
        for q_found, e_found in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(q=q_found, e=e_found):
                task = self.task()
                task.find_one.side_effect = lambda feature, q_found=q_found, e_found=e_found, **kwargs: {
                    fL.q: Box(660, 25, 25, 25) if q_found else None,
                    fL.e: Box(1230, 25, 25, 25) if e_found else None,
                }[feature]
                self.assertEqual(EnvironmentMonitoringPhotoTask._task_detail_page(task), q_found and e_found)

    def test_missing_task_detail_page_prevents_tracking(self):
        task = self.prepare_task([{"name": "小池荷", "action_box": Box(678, 918, 562, 81)}], [])
        del task.enter_observation
        task._observation_action = Mock(return_value="go")
        task._task_detail_page.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未进入任务详情"):
            task.prepare_observation()
        task.open_observation_map.assert_not_called()
        task.wait_ocr.assert_not_called()

    def test_unstable_mission_details_stop_before_tracking_or_transfer(self):
        task = self.prepare_task([{"name": "小池荷", "action_box": Box(678, 918, 562, 81)}], [])
        del task.enter_observation
        task._observation_action = Mock(return_value="go")
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "任务详情未稳定"):
            task.prepare_observation()
        task.open_observation_map.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_already_accepted_card_goes_to_mission_without_accepting_again(self):
        task = self.task()
        area = Box(678, 918, 562, 81)
        task._observation_action = Mock(return_value="go")
        task.enter_observation({"name": "小池荷", "action_box": area})
        task.click.assert_called_once_with(area)
        task.wait_click_ocr.assert_not_called()

    def test_failed_accept_transition_does_not_click_another_card_or_continue(self):
        task = self.task()
        task._observation_action = Mock(return_value="accept")
        area = Box(678, 918, 562, 81)
        with self.assertRaisesRegex(RuntimeError, "前往任务"):
            task.enter_observation({"name": "小池荷", "action_box": area})
        self.assertEqual(task.click.call_count, 1)
        task.wait_ocr.assert_not_called()

    def test_button_color_on_another_page_cannot_be_clicked(self):
        task = self.task()
        task._monitoring_page.return_value = False
        task._observation_action = Mock(return_value="go")
        area = Box(678, 918, 562, 81)
        with self.assertRaisesRegex(RuntimeError, "打开观察点前未处于环境监测终端列表"):
            task.enter_observation({"name": "小池荷", "action_box": area})
        task.click.assert_not_called()
        task._observation_action.assert_not_called()

    def test_page_lost_after_accept_cannot_click_go_on_other_page(self):
        task = self.task()
        task._monitoring_page.side_effect = [True, False]
        task._observation_action = Mock(return_value="accept")
        area = Box(678, 918, 562, 81)
        with self.assertRaisesRegex(RuntimeError, "前往任务"):
            task.enter_observation({"name": "小池荷", "action_box": area})
        task.click.assert_called_once_with(area)
        task.wait_ocr.assert_not_called()

    def test_unknown_button_color_never_clicks_or_opens_details(self):
        task = self.task()
        task._observation_action = Mock(return_value=None)
        area = Box(678, 918, 562, 81)
        with self.assertRaisesRegex(RuntimeError, "前往任务"):
            task.enter_observation({"name": "小池荷", "action_box": area})
        task.click.assert_not_called()
        task.wait_ocr.assert_not_called()

    def test_unknown_tracking_state_cannot_open_map_or_teleport(self):
        task = self.task()
        route = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "追踪状态"):
            route.transfer()
        task.click.assert_not_called()
        task.ocr.assert_not_called()
        task.wait_click_feature.assert_not_called()
        task.to_near_transfer_point.assert_not_called()

    def test_already_tracked_task_explicitly_locates_map_before_transfer(self):
        task = self.task()
        cancel = Box(1815, 975, 38, 39)
        locator = Box(1487, 961, 62, 61)
        task.find_one.side_effect = [cancel, locator]
        EnvironmentMonitoringRoute(task).transfer()
        task.ocr.assert_not_called()
        task.click.assert_called_once_with(locator)
        self.assertTrue(task.find_one.call_args_list[0].kwargs["use_gray_scale"])
        task.find_one.assert_any_call(fL.one_task_to_map, box=task.box.bottom_right)
        task.wait_click_feature.assert_not_called()
        task.to_near_transfer_point.assert_called_once_with(need_track=False)
        task._wait_for_transfer_arrival.assert_called_once_with()

    def test_generic_cancel_or_locator_alone_cannot_confirm_tracking(self):
        for cancel, locator in ((Box(1815, 975, 38, 39), None), (None, Box(1487, 961, 62, 61))):
            with self.subTest(cancel=cancel, locator=locator):
                task = self.task()
                task.find_one.side_effect = lambda feature, cancel=cancel, locator=locator, **kwargs: {
                    fL.skip_dialog_cancel: cancel,
                    fL.one_task_to_map: locator,
                }.get(feature)
                route = EnvironmentMonitoringRoute(task)
                with self.assertRaisesRegex(RuntimeError, "追踪状态"):
                    route.transfer()
                task.click.assert_not_called()
                task.wait_feature.assert_not_called()
                task.to_near_transfer_point.assert_not_called()
                task._wait_for_transfer_arrival.assert_not_called()

    def test_close_icon_elsewhere_cannot_confirm_tracking_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                task = self.task(width, height)
                close = Box(round(width * 0.95), round(height * 0.06), 20, 20)
                locator = Box(round(width * 0.78), round(height * 0.90), 30, 30)

                def find_icon(feature, *, box, close=close, locator=locator, **kwargs):
                    icon = {fL.skip_dialog_cancel: close, fL.one_task_to_map: locator}.get(feature)
                    return (
                        icon
                        if icon and box.x <= icon.x < box.x + box.width and box.y <= icon.y < box.y + box.height
                        else None
                    )

                task.find_one.side_effect = find_icon
                route = EnvironmentMonitoringRoute(task)
                with self.assertRaisesRegex(RuntimeError, "追踪状态"):
                    route.transfer()
                cancel_area = task.find_one.call_args_list[0].kwargs["box"]
                self.assertEqual((cancel_area.x, cancel_area.y), (round(width * 0.93), round(height * 0.89)))
                self.assertAlmostEqual(cancel_area.width / width, 0.04, delta=1 / width)
                self.assertAlmostEqual(cancel_area.height / height, 0.06, delta=1 / height)
                task.click.assert_not_called()
                task.to_near_transfer_point.assert_not_called()

    def test_tracking_icons_seen_in_different_frames_do_not_combine_into_confirmation(self):
        task = self.task()
        current_icons = {}
        frames = ({fL.skip_dialog_cancel: Box(1815, 975, 38, 39)}, {fL.one_task_to_map: Box(1487, 961, 62, 61)})
        task.find_one.side_effect = lambda feature, **kwargs: current_icons.get(feature)

        def wait_frames(condition, **kwargs):
            for frame_icons in frames:
                current_icons.clear()
                current_icons.update(frame_icons)
                result = condition()
                if result:
                    return result
            return None

        task.wait_until.side_effect = wait_frames
        route = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "追踪状态"):
            route.transfer()
        task.click.assert_not_called()
        task.wait_feature.assert_not_called()
        task.to_near_transfer_point.assert_not_called()

    def test_missing_map_stops_after_locator_without_teleporting(self):
        task = self.task()
        locator = Box(1487, 961, 62, 61)
        task.find_one.side_effect = [Box(1815, 975, 38, 39), locator]
        task.wait_feature.return_value = False
        route = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "任务地图未打开"):
            route.transfer()
        task.click.assert_called_once_with(locator)
        task.to_near_transfer_point.assert_not_called()
        task._wait_for_transfer_arrival.assert_not_called()

    def test_untracked_task_clicks_transfer_go_without_ocr(self):
        task = self.task()
        track = Box(1817, 976, 34, 34)
        task.find_one.side_effect = lambda feature, **kwargs: track if feature == fL.transfer_go else None
        EnvironmentMonitoringRoute(task).transfer()
        task.click.assert_called_once_with(track)
        task.ocr.assert_not_called()
        track_area = task.find_one.call_args.kwargs["box"]
        self.assertEqual((track_area.x, track_area.y), (round(1920 * 0.81), round(1080 * 0.89)))
        task.to_near_transfer_point.assert_called_once_with(need_track=False)

    def test_unstable_map_stops_before_clicking_teleport(self):
        task = self.task()
        track = Box(1817, 976, 34, 34)
        task.find_one.side_effect = lambda feature, **kwargs: track if feature == fL.transfer_go else None
        task.wait_ui_stable.return_value = False
        route = EnvironmentMonitoringRoute(task)
        with self.assertRaisesRegex(RuntimeError, "任务地图未稳定"):
            route.transfer()
        task.to_near_transfer_point.assert_not_called()
        task._wait_for_transfer_arrival.assert_not_called()

    def prepare_task(self, first, second):
        task = self.task()
        task.open_monitoring = Mock()
        task.select_terminal = Mock()
        task.available_observations = Mock(side_effect=[first, second])
        task.enter_observation = Mock()
        task.open_observation_map = Mock()
        return task

    def test_unsupported_tasks_are_never_selected_when_no_supported_task_is_available(self):
        task = self.prepare_task([{"name": "古树"}], [{"name": "枯枝"}])
        self.assertIsNone(task.prepare_observation())
        task.enter_observation.assert_not_called()
        task.open_observation_map.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_supported_title_can_be_found_in_the_second_terminal(self):
        requested = {"name": "“小池荷”"}
        task = self.prepare_task([{"name": "古树"}], [requested, {"name": "枯枝"}])
        result = task.prepare_observation()
        task.enter_observation.assert_called_once_with(requested)
        task.open_observation_map.assert_called_once_with()
        self.assertEqual(result["name"], "“小池荷”")
        self.assertEqual(result["terminal"], "marker_stone")
        self.assertEqual(
            task.select_terminal.call_args_list,
            [call("outskirts"), call("marker_stone")],
        )

    def test_eternal_sunset_card_selects_its_registered_route(self):
        names = json.loads(
            (Path(__file__).resolve().parents[1] / "assets/lang/environment_monitoring.json").read_text(encoding="utf-8")
        )["eternal_sunset"]
        for locale, title in (
            ("zh_CN", "拍摄生长在亭子中间的“栖霞驻影”"),
            ("zh_TW", "棲霞駐影"),
            ("en_US", "Eternal Sunset"),
            ("ja_JP", "棲霞留影"),
            ("ko_KR", "노을의 그림자"),
            ("es_ES", "Puesta de sol eterna"),
        ):
            with self.subTest(locale=locale):
                task = self.prepare_task([{"name": title}], [])
                task.lang.environment_monitoring.eternal_sunset = names[locale]["string"]
                with patch.object(EternalSunsetRoute, "transfer"):
                    result = task.prepare_observation()
                self.assertEqual(result["name"], title)
                self.assertIsInstance(task.navigation, EternalSunsetRoute)

    def test_unsupported_cards_are_skipped_before_supported_card_in_same_terminal(self):
        supported = {"name": "小池荷"}
        task = self.prepare_task([{"name": "古树"}, supported, {"name": "枯枝"}], [])
        self.assertEqual(task.prepare_observation()["name"], "小池荷")
        task.enter_observation.assert_called_once_with(supported)
        self.assertEqual(task.select_terminal.call_count, 1)

    def test_registered_route_is_available_without_changing_entry_logic(self):
        supported = {"name": "古树"}
        task = self.prepare_task([supported], [])
        task.lang.environment_monitoring.ancient_tree = "古树"
        with patch.dict(
            "src.tasks.onetime.EnvironmentMonitoringPhotoTask.OBSERVATIONS",
            {"ancient_tree": PoolLotusRoute},
        ):
            self.assertEqual(task.prepare_observation()["name"], "古树")
        task.enter_observation.assert_called_once_with(supported)

    def test_route_matches_localized_names_without_relying_on_punctuation(self):
        task = self.task()
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "assets/lang/environment_monitoring.json").read_text(
                encoding="utf-8"
            )
        )
        examples = (
            ("zh_CN", "“小池荷”"),
            ("zh_TW", "「小池荷」"),
            ("en_US", '"pOoL   loTUs"'),
            ("ja_JP", "「池の蓮」"),
            ("ko_KR", "'작은 연못의 연꽃'"),
        )
        for locale, title in examples:
            with self.subTest(title=title):
                task.lang.environment_monitoring.pool_lotus = data["pool_lotus"][locale]["string"]
                self.assertEqual(task.observation_route(title), PoolLotusRoute)
                self.assertIs(task.observation_route(title + "其他"), PoolLotusRoute)

    def test_thillu_matches_localized_names_with_or_without_quotes(self):
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "assets/lang/environment_monitoring.json").read_text(
                encoding="utf-8"
            )
        )
        for locale, node in data["thillu"].items():
            with self.subTest(locale=locale):
                task = self.task()
                name = node["string"]
                task.lang.environment_monitoring.thillu = name
                self.assertIs(task.observation_route(name), StationaryObservationRoute)
                self.assertIs(task.observation_route(f"“{name}”"), StationaryObservationRoute)

    def test_normal_slug_matches_without_accepting_vigorous_title_in_all_locales(self):
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "assets/lang/environment_monitoring.json").read_text(
                encoding="utf-8"
            )
        )
        vigorous_titles = {
            "zh_CN": "充满活力的蓄水源石虫",
            "zh_TW": "充滿活力的蓄水源石蟲",
            "en_US": "Vigorous Cistern Originium Slug",
            "ja_JP": "活発になっている貯水オリジムシ",
            "ko_KR": "활기찬 저수 원석충",
        }
        normal_key = "cistern_originium_slugs"
        for locale, vigorous_title in vigorous_titles.items():
            with self.subTest(locale=locale):
                task = self.task()
                normal_title = data[normal_key][locale]["string"]
                setattr(task.lang.environment_monitoring, normal_key, normal_title)
                self.assertIs(task.observation_route(f"“ {normal_title} ”"), CisternOriginiumSlugsRoute)
                self.assertIsNone(task.observation_key(vigorous_title))
                self.assertIsNone(task.observation_key(normal_title + "其他"))

    def test_short_names_match_only_their_point_in_every_locale(self):
        data = json.loads(
            (Path(__file__).resolve().parents[1] / "assets/lang/environment_monitoring.json").read_text(
                encoding="utf-8"
            )
        )
        points = data
        short_names = {key: value for key, value in points.items() if "pattern" in value["zh_CN"]}
        for locale in ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR"):
            for key, names in short_names.items():
                with self.subTest(locale=locale, point=key):
                    task = self.task()
                    setattr(task.lang.environment_monitoring, key, re.compile(names[locale]["pattern"]))
                    with patch.dict(
                        "src.tasks.onetime.EnvironmentMonitoringPhotoTask.OBSERVATIONS",
                        {key: PoolLotusRoute},
                        clear=True,
                    ):
                        self.assertEqual(task.observation_route(names[locale]["pattern"]), PoolLotusRoute)
                        for other, titles in points.items():
                            node = titles[locale]
                            actual = task.observation_route(node.get("string", node.get("pattern")))
                            self.assertEqual(actual, PoolLotusRoute if other == key else None, other)

    def test_ambiguous_short_name_never_selects_a_route(self):
        task = self.task()
        task.lang.environment_monitoring.first = re.compile("小池")
        task.lang.environment_monitoring.second = re.compile("池荷")
        with patch.dict(
            "src.tasks.onetime.EnvironmentMonitoringPhotoTask.OBSERVATIONS",
            {"first": PoolLotusRoute, "second": PoolLotusRoute},
            clear=True,
        ):
            self.assertIsNone(task.observation_route("小池荷"))

    def test_short_pattern_matches_a_complete_title(self):
        task = self.task()
        task.lang.environment_monitoring.ecology_near_the_field_logistics_depot = re.compile("储备站")
        with patch.dict(
            "src.tasks.onetime.EnvironmentMonitoringPhotoTask.OBSERVATIONS",
            {"ecology_near_the_field_logistics_depot": PoolLotusRoute},
            clear=True,
        ):
            self.assertEqual(task.observation_route("储备站周围的生态环境"), PoolLotusRoute)
            self.assertIsNone(task.observation_route("古树"))

    def test_transfer_arrival_failure_clears_previous_result_and_never_reports_success(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.open_observation_map
        track = Box(1817, 976, 34, 34)
        task.find_one.side_effect = lambda feature, **kwargs: track if feature == fL.transfer_go else None
        task._wait_for_transfer_arrival.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未确认到达"):
            task.prepare_observation()
        task.to_near_transfer_point.assert_called_once_with(need_track=False)
        self.assertIsNone(task.selected_observation)
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
