"""Environment monitoring must select one matching card and verify transfer completion."""

import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok import Box
from ok.util.color import calculate_color_percentage
from ok.util.config import Config

from src.config import config as app_config
from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.EnvironmentMonitoringEntryTask import EnvironmentMonitoringEntryTask


class TestEnvironmentMonitoringEntry(unittest.TestCase):
    def task(self, width=1920, height=1080):
        task = object.__new__(EnvironmentMonitoringEntryTask)
        task.lang = SimpleNamespace(
            environment_monitoring=SimpleNamespace(
                entry=re.compile("环境监测"),
                page=re.compile("环境监测终端"),
                accept=re.compile("接取委托"),
                go=re.compile("前往任务"),
                track=re.compile("开始追踪"),
                outskirts=re.compile("城郊监测终端"),
                marker_stone=re.compile("首墩监测终端"),
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
        task.wait_ocr = Mock(return_value=[Box(1200, 100, 200, 40)])
        task.wait_click_ocr = Mock(return_value=True)
        task.wait_click_feature = Mock(return_value=True)
        task.find_one = Mock(return_value=None)
        task.wait_feature = Mock(return_value=True)
        task.wait_ui_stable = Mock(return_value=True)
        task.wait_until = Mock(side_effect=lambda condition, **kwargs: condition())
        task.to_near_transfer_point = Mock(return_value=True)
        task._wait_for_transfer_arrival = Mock(return_value=True)
        task.log_info = Mock()
        task.selected_observation = {"name": "previous run"}
        return task

    def color_task(self, frame):
        task = Mock(frame=frame, lang=self.task().lang)
        task.calculate_color_percentage.side_effect = lambda color, box: calculate_color_percentage(frame, color, box)
        return task

    def test_registered_task_constructs_with_only_entry_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Config, "config_folder", directory):
            task = EnvironmentMonitoringEntryTask(SimpleNamespace(scene=None, pause_start=None), SimpleNamespace())
        self.assertTrue(task.visible)
        self.assertEqual(task.default_config, {"观察点名称": ""})
        self.assertEqual(task.group_name, "工具与调试")
        self.assertIsNone(task.selected_observation)
        self.assertIn(
            ["src.tasks.onetime.EnvironmentMonitoringEntryTask", "EnvironmentMonitoringEntryTask"],
            app_config["onetime_tasks"],
        )

    def test_available_cards_bind_buttons_to_their_own_titles_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                task = self.task(width, height)
                titles = [
                    Box(round(width * x), round(height * 0.30), 100, 20, name=name)
                    for x, name in ((0.06, "古树"), (0.37, "“小池荷”"), (0.68, "枯枝"))
                ]

                # The completed first card has a title but no action; the second and third are actionable.
                def read_cards(match=None, box=None, titles=titles, width=width):
                    if box is None:
                        return [*titles, Box(10, 10, 100, 20, name="环境监测终端")]
                    return [] if box.x < width * 0.2 else [Box(box.x + 10, box.y + 10, 70, 20)]

                task.ocr.side_effect = read_cards
                candidates = task.available_observations()
                self.assertEqual([item["name"] for item in candidates], ["“小池荷”", "枯枝"])
                for candidate, title in zip(candidates, titles[1:], strict=True):
                    area = candidate["action_box"]
                    self.assertLessEqual(area.x, title.x)
                    self.assertGreater(area.x + area.width, title.x)
                    self.assertGreater(area.y, title.y)

    def test_terminal_highlight_identifies_only_the_selected_tab_at_multiple_sizes(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            for selected in ("outskirts", "marker_stone"):
                with self.subTest(size=(width, height), selected=selected):
                    frame = np.full((height, width, 3), 40, dtype=np.uint8)
                    x1, x2 = (0.046, 0.246) if selected == "outskirts" else (0.25, 0.45)
                    frame[int(height * 0.1) : int(height * 0.172), int(width * x1) : int(width * x2)] = 235
                    task = self.color_task(frame)
                    for terminal in ("outskirts", "marker_stone"):
                        matcher = getattr(task.lang.environment_monitoring, terminal)
                        self.assertEqual(
                            EnvironmentMonitoringEntryTask._terminal_selected(task, matcher), terminal == selected
                        )

    def test_colored_tab_or_sparse_white_text_is_not_a_selected_background(self):
        for color in ((255, 255, 50), (255, 50, 50), (180, 180, 180)):
            with self.subTest(color=color):
                frame = np.full((1080, 1920, 3), color, dtype=np.uint8)
                frame[::3, ::3] = 255
                task = self.color_task(frame)
                self.assertFalse(
                    EnvironmentMonitoringEntryTask._terminal_selected(task, task.lang.environment_monitoring.outskirts)
                )

    def test_terminal_color_range_and_crop_keep_the_exact_pixel_boundaries(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (1366, 768)):
            for terminal, left in (("outskirts", 0.06), ("marker_stone", 0.27)):
                for color, selected in (
                    ((185, 255, 255), False),
                    ((255, 185, 255), False),
                    ((255, 255, 185), False),
                    ((186, 186, 186), True),
                ):
                    with self.subTest(size=(width, height), terminal=terminal, color=color):
                        frame = np.zeros((height, width, 3), dtype=np.uint8)
                        x1, x2 = int(width * left), int(width * (left + 0.07))
                        y1, y2 = int(height * 0.145), int(height * 0.16)
                        frame[y1:y2, x1:x2] = color
                        task = self.color_task(frame)
                        matcher = getattr(task.lang.environment_monitoring, terminal)
                        self.assertEqual(EnvironmentMonitoringEntryTask._terminal_selected(task, matcher), selected)
                        area = task.calculate_color_percentage.call_args.args[1]
                        self.assertEqual((area.x, area.y, area.width, area.height), (x1, y1, x2 - x1, y2 - y1))

    def test_terminal_white_fraction_must_strictly_exceed_seventy_percent(self):
        frame = np.zeros((1000, 1000, 3), dtype=np.uint8)
        # The 70 x 15 crop has 1,050 pixels, so 735 white pixels are exactly 70%.
        patch = np.zeros((15, 70, 3), dtype=np.uint8)
        patch.reshape(-1, 3)[:735] = 255
        frame[145:160, 60:130] = patch
        task = self.color_task(frame)
        matcher = task.lang.environment_monitoring.outskirts
        self.assertFalse(EnvironmentMonitoringEntryTask._terminal_selected(task, matcher))
        frame[155, 95] = 255
        self.assertTrue(EnvironmentMonitoringEntryTask._terminal_selected(task, matcher))

    def test_unselected_or_unstable_terminal_cannot_scan_old_cards(self):
        for selected, stable in ((False, True), (True, False)):
            with self.subTest(selected=selected, stable=stable):
                task = self.prepare_task([{"name": "小池荷"}], [])
                del task.select_terminal
                task._terminal_selected = Mock(return_value=selected)
                task.wait_ui_stable.return_value = stable
                with self.assertRaisesRegex(RuntimeError, "终端切换未完成"):
                    task.prepare_observation("小池荷")
                self.assertEqual(task.wait_click_ocr.call_count, 2)
                task.available_observations.assert_not_called()
                task.enter_observation.assert_not_called()
                task.transfer_observation.assert_not_called()
                self.assertIsNone(task.selected_observation)

    def test_swallowed_terminal_click_is_retried_before_scanning(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.select_terminal
        task._terminal_selected = Mock(side_effect=[False, True])
        self.assertEqual(task.prepare_observation("小池荷")["name"], "小池荷")
        self.assertEqual(task.wait_click_ocr.call_count, 2)
        task.wait_ui_stable.assert_called_once_with()

    def test_unstable_monitoring_page_stops_before_terminal_selection(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.open_monitoring
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "页面未稳定"):
            task.prepare_observation("小池荷")
        task.select_terminal.assert_not_called()
        task.available_observations.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_ambiguous_title_is_not_selected_even_with_an_accept_button(self):
        task = self.task()
        task.ocr.side_effect = lambda match=None, box=None: (
            [Box(120, 325, 100, 20, name="小池"), Box(240, 325, 40, 20, name="荷")]
            if box is None
            else [Box(box.x, box.y, 50, 20)]
        )
        self.assertEqual(task.available_observations(), [])
        task.click.assert_not_called()

    def test_accept_and_go_remain_inside_the_selected_card(self):
        task = self.task()
        area = Box(678, 918, 562, 81)
        accept_button = Box(1100, 950, 110, 25, name="接取委托")
        task.ocr.return_value = [accept_button]
        task.enter_observation({"name": "“小池荷”", "action_box": area})
        task.ocr.assert_called_once_with(match=task.lang.environment_monitoring.accept, box=area)
        task.click.assert_called_once_with(accept_button)
        self.assertIs(task.wait_click_ocr.call_args.kwargs["box"], area)
        self.assertEqual(task.wait_ocr.call_args.kwargs["match"].pattern, "小池荷")
        title_region = task.wait_ocr.call_args.kwargs["box"]
        self.assertLessEqual(title_region.x, 650)
        self.assertGreater(title_region.x + title_region.width, 1500)

    def test_unstable_mission_details_stop_before_tracking_or_transfer(self):
        task = self.prepare_task([{"name": "小池荷", "action_box": Box(678, 918, 562, 81)}], [])
        del task.enter_observation
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "任务详情未稳定"):
            task.prepare_observation("小池荷")
        task.transfer_observation.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_already_accepted_card_goes_to_mission_without_accepting_again(self):
        task = self.task()
        area = Box(678, 918, 562, 81)
        task.enter_observation({"name": "小池荷", "action_box": area})
        task.click.assert_not_called()
        self.assertIs(task.wait_click_ocr.call_args.kwargs["box"], area)

    def test_failed_accept_transition_does_not_click_another_card_or_continue(self):
        task = self.task()
        task.ocr.return_value = [Box(1100, 950, 110, 25, name="接取委托")]
        task.wait_click_ocr.return_value = False
        with self.assertRaisesRegex(RuntimeError, "前往任务"):
            task.enter_observation({"name": "小池荷", "action_box": Box(678, 918, 562, 81)})
        self.assertEqual(task.click.call_count, 1)
        task.wait_ocr.assert_not_called()

    def test_unknown_tracking_state_cannot_open_map_or_teleport(self):
        task = self.task()
        with self.assertRaisesRegex(RuntimeError, "追踪状态"):
            task.transfer_observation()
        task.click.assert_not_called()
        task.wait_click_feature.assert_not_called()
        task.to_near_transfer_point.assert_not_called()

    def test_already_tracked_task_explicitly_locates_map_before_transfer(self):
        task = self.task()
        cancel = Box(1815, 975, 38, 39)
        locator = Box(1487, 961, 62, 61)
        task.find_one.side_effect = [cancel, locator]
        task.transfer_observation()
        task.ocr.assert_called_once_with(match=task.lang.environment_monitoring.track, box=task.box.bottom_right)
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
                task.find_one.side_effect = lambda feature, cancel=cancel, locator=locator, **kwargs: (
                    cancel if feature == fL.skip_dialog_cancel else locator
                )
                with self.assertRaisesRegex(RuntimeError, "追踪状态"):
                    task.transfer_observation()
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
                    icon = close if feature == fL.skip_dialog_cancel else locator
                    return (
                        icon if box.x <= icon.x < box.x + box.width and box.y <= icon.y < box.y + box.height else None
                    )

                task.find_one.side_effect = find_icon
                with self.assertRaisesRegex(RuntimeError, "追踪状态"):
                    task.transfer_observation()
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
        with self.assertRaisesRegex(RuntimeError, "追踪状态"):
            task.transfer_observation()
        task.click.assert_not_called()
        task.wait_feature.assert_not_called()
        task.to_near_transfer_point.assert_not_called()

    def test_missing_map_stops_after_locator_without_teleporting(self):
        task = self.task()
        locator = Box(1487, 961, 62, 61)
        task.find_one.side_effect = [Box(1815, 975, 38, 39), locator]
        task.wait_feature.return_value = False
        with self.assertRaisesRegex(RuntimeError, "任务地图未打开"):
            task.transfer_observation()
        task.click.assert_called_once_with(locator)
        task.to_near_transfer_point.assert_not_called()
        task._wait_for_transfer_arrival.assert_not_called()

    def test_untracked_task_still_uses_start_tracking_ocr(self):
        task = self.task()
        track = Box(1500, 950, 100, 30, name="开始追踪")
        task.ocr.return_value = [track]
        task.transfer_observation()
        task.click.assert_called_once_with(track)
        task.find_one.assert_not_called()
        task.to_near_transfer_point.assert_called_once_with(need_track=False)

    def test_unstable_map_stops_before_clicking_teleport(self):
        task = self.task()
        task.ocr.return_value = [Box(1500, 950, 100, 30, name="开始追踪")]
        task.wait_ui_stable.return_value = False
        with self.assertRaisesRegex(RuntimeError, "任务地图未稳定"):
            task.transfer_observation()
        task.to_near_transfer_point.assert_not_called()
        task._wait_for_transfer_arrival.assert_not_called()

    def prepare_task(self, first, second):
        task = self.task()
        task.open_monitoring = Mock()
        task.select_terminal = Mock()
        task.available_observations = Mock(side_effect=[first, second])
        task.enter_observation = Mock()
        task.transfer_observation = Mock()
        return task

    def test_requested_title_never_falls_back_to_a_different_available_task(self):
        task = self.prepare_task([{"name": "古树"}], [{"name": "小池荷"}])
        with self.assertRaisesRegex(RuntimeError, "未找到符合条件"):
            task.prepare_observation("枯枝")
        task.enter_observation.assert_not_called()
        task.transfer_observation.assert_not_called()
        self.assertIsNone(task.selected_observation)

    def test_exact_requested_title_can_be_found_in_the_second_terminal(self):
        requested = {"name": "“小池荷”"}
        task = self.prepare_task([{"name": "古树"}], [requested, {"name": "枯枝"}])
        result = task.prepare_observation(" 小池荷 ")
        task.enter_observation.assert_called_once_with(requested)
        task.transfer_observation.assert_called_once_with()
        self.assertEqual(result["name"], "“小池荷”")
        self.assertEqual(
            task.select_terminal.call_args_list,
            [call(task.lang.environment_monitoring.outskirts), call(task.lang.environment_monitoring.marker_stone)],
        )

    def test_no_target_stops_after_the_first_available_observation(self):
        first = {"name": "古树"}
        task = self.prepare_task([first, {"name": "枯枝"}], [{"name": "小池荷"}])
        self.assertEqual(task.prepare_observation()["name"], "古树")
        task.enter_observation.assert_called_once_with(first)
        self.assertEqual(task.select_terminal.call_count, 1)

    def test_transfer_arrival_failure_clears_previous_result_and_never_reports_success(self):
        task = self.prepare_task([{"name": "小池荷"}], [])
        del task.transfer_observation
        task.ocr.return_value = [Box(1500, 950, 100, 30, name="开始追踪")]
        task._wait_for_transfer_arrival.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未确认到达"):
            task.prepare_observation("小池荷")
        task.to_near_transfer_point.assert_called_once_with(need_track=False)
        self.assertIsNone(task.selected_observation)
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
