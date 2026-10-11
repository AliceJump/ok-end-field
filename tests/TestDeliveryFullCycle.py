import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from ok import TaskDisabledException

from src.tasks.onetime.DeliveryTask import DeliveryTask, secondary_objective_direction_dot


class TestDeliveryFullCycle(unittest.TestCase):
    @staticmethod
    def _task():
        task = object.__new__(DeliveryTask)
        task.config = {
            DeliveryTask.CFG_TEST_TARGET: DeliveryTask.TEST_FULL_CYCLE,
            DeliveryTask.CFG_FULL_CYCLE_LOCATION: "试验园区",
        }
        task.delivery_area = "武陵"
        task.info_set = Mock()
        task.log_info = Mock()
        task.task_to_transfer_point = Mock(return_value=True)
        task.to_storage_point_and_back_zip_line = Mock(return_value=True)
        task.on_zip_line_start = Mock()
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.wait_click_ocr = Mock()
        task.sleep = Mock()
        return task

    def test_each_npc_route_runs_once_without_reboarding(self):
        task = self._task()
        events = Mock()
        events.attach_mock(task.task_to_transfer_point, "transfer")
        events.attach_mock(task.to_storage_point_and_back_zip_line, "outbound")
        events.attach_mock(task.on_zip_line_start, "npc")

        self.assertTrue(task._run_single_delivery_cycle())

        expected = []
        for target in ("赵昭", "裴令容", "阿禾"):
            expected.extend(
                [
                    call.transfer(),
                    call.outbound(only_zip_line=True),
                    call.npc(target, need_v=False, need_scroll=False, stay_on_zip_line=True),
                ]
            )
        self.assertEqual(events.mock_calls, expected)
        task.wait_click_ocr.assert_not_called()
        self.assertEqual(task._accepted_delivery_location, "试验园区")

    def test_failed_transfer_skips_current_route_and_continues(self):
        task = self._task()
        task.task_to_transfer_point.side_effect = [False, True, True]

        self.assertFalse(task._run_single_delivery_cycle())

        self.assertEqual(task.task_to_transfer_point.call_count, 3)
        self.assertEqual(task.to_storage_point_and_back_zip_line.call_count, 2)
        self.assertEqual([args.args[0] for args in task.on_zip_line_start.call_args_list], ["裴令容", "阿禾"])

    def test_failed_outbound_skips_current_route_and_continues(self):
        task = self._task()
        task.to_storage_point_and_back_zip_line.side_effect = [False, True, True]

        self.assertFalse(task._run_single_delivery_cycle())

        self.assertEqual(task.task_to_transfer_point.call_count, 3)
        self.assertEqual([args.args[0] for args in task.on_zip_line_start.call_args_list], ["裴令容", "阿禾"])

    def test_route_timeout_continues_with_next_transfer(self):
        task = self._task()
        task.on_zip_line_start.side_effect = [RuntimeError("route failed"), None, None]

        self.assertFalse(task._run_single_delivery_cycle())

        self.assertEqual(task.task_to_transfer_point.call_count, 3)
        self.assertEqual([args.args[0] for args in task.on_zip_line_start.call_args_list], ["赵昭", "裴令容", "阿禾"])

    def test_user_stop_does_not_start_next_route(self):
        task = self._task()
        task.on_zip_line_start.side_effect = TaskDisabledException()

        with self.assertRaises(TaskDisabledException):
            task._run_single_delivery_cycle()

        task.task_to_transfer_point.assert_called_once_with()

    def test_outbound_test_stays_on_zip_line_without_blue_marker(self):
        task = self._task()
        # Exercise the real outbound helper rather than the cycle-level mock.
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_96b876e3="menu"))
        task.box = SimpleNamespace(top_left="top_left")
        task._find_zip_line_board_button = Mock(return_value="board")
        task.wait_ocr = Mock(return_value=[])
        task.click_with_alt = Mock()
        task._resolve_to_delivery_point_config_key = Mock(return_value="outbound")
        task.get_zip_line_config_value = Mock(return_value="36,48")
        task.zip_line_list_go = Mock()
        task.align_ocr_or_find_target_to_center = Mock()
        task.navigate_until_target = Mock()

        self.assertTrue(DeliveryTask.to_storage_point_and_back_zip_line(task, only_zip_line=True))

        task.zip_line_list_go.assert_called_once_with(
            [36, 48], need_scroll=False, target=None, need_v=False, stay_on_zip_line=True
        )
        task.align_ocr_or_find_target_to_center.assert_not_called()
        task.navigate_until_target.assert_not_called()

    def test_pickup_keeps_blue_marker_navigation(self):
        task = self._task()
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_96b876e3="menu", k_b0e3a2da="board"))
        task.box = SimpleNamespace(top_left="top_left")
        task._find_zip_line_board_button = Mock(return_value="board")
        task.wait_ocr = Mock(return_value=[])
        task.click_with_alt = Mock()
        task._resolve_to_delivery_point_config_key = Mock(return_value="outbound")
        task.get_zip_line_config_value = Mock(return_value="36")
        task.zip_line_list_go = Mock()
        task.align_ocr_or_find_target_to_center = Mock()
        task.send_key = Mock()
        task.navigate_until_target = Mock(return_value=False)

        self.assertFalse(DeliveryTask.to_storage_point_and_back_zip_line(task))

        task.zip_line_list_go.assert_called_once_with(
            [36],
            need_scroll=False,
            target=(secondary_objective_direction_dot, "feature"),
            need_v=True,
            stay_on_zip_line=False,
        )
        task.align_ocr_or_find_target_to_center.assert_called_once()
        task.navigate_until_target.assert_called_once()


if __name__ == "__main__":
    unittest.main()
