import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok.util.config import Config
from ok.util.file import read_json_file, write_json_file

from src.tasks.onetime.DailyDeliveryTask import DailyDeliveryTask
from src.tasks.onetime.DeliveryTask import DeliveryTask


class TestDailyDeliveryTask(unittest.TestCase):
    def test_daily_card_exposes_only_delivery_parameters(self):
        executor = SimpleNamespace(scene=None, pause_start=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(Config, "config_folder", tmp):
            task = DailyDeliveryTask(executor, SimpleNamespace())
            standalone = DeliveryTask(executor, SimpleNamespace())

        self.assertEqual(
            set(task.default_config),
            {"_enabled", "目标券数", "地区切换"},
        )
        self.assertEqual(set(task.config_type), {"目标券数", "地区切换"})
        self.assertNotIn("多账户独立配置", task.default_config)
        self.assertIn("选择测试对象", standalone.default_config)
        self.assertIn("运行模式", standalone.default_config)
        self.assertNotIn("仅接取", standalone.default_config)
        self.assertNotIn("仅送货", standalone.default_config)

    def test_existing_debug_options_are_backed_up_before_config_cleanup(self):
        executor = SimpleNamespace(scene=None, pause_start=None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(Config, "config_folder", tmp):
            config_file = Path(tmp) / "DailyDeliveryTask.json"
            write_json_file(str(config_file), {"目标券数": ["73100"], "仅接取": True})
            task = DailyDeliveryTask(executor, SimpleNamespace())
            task.load_config()

            backup = Path(tmp) / "daily_delivery_simplify_backup" / "DailyDeliveryTask.json"
            self.assertTrue(read_json_file(str(backup))["仅接取"])
            self.assertNotIn("仅接取", task.config)
            self.assertEqual(task.config["目标券数"], ["73100"])
            self.assertTrue(task._is_account_override_enabled())

    def _make_cycle(self, config, daily_mode):
        task = object.__new__(DailyDeliveryTask)
        task.config = config
        task._daily_delivery_mode = daily_mode
        task.ends = []
        task.delivery_area = "武陵城"
        task.lang = SimpleNamespace()
        task._logged_in = True
        task.ensure_main = Mock()
        task.back = Mock()
        task.accept_order = Mock(return_value=True)
        task.task_to_transfer_point = Mock(return_value=False)
        task.log_info = Mock()
        task.info_set = Mock()
        task.mark_task_failure = Mock()
        task._delivery_stage = "未开始"
        task._delivery_failure_recorded = False
        task._delivery_cargo_in_transit = False
        task._delivery_failure_requires_abort = False
        return task

    def _make_submit_stub(self):
        pattern = re.compile("target")
        stub = SimpleNamespace(
            lang=SimpleNamespace(
                DeliveryTask=SimpleNamespace(
                    k_6536f6f1=re.compile("special"),
                    k_0c1ef9f5=re.compile("replacement"),
                )
            ),
            box=SimpleNamespace(bottom_right="bottom_right"),
            align_ocr_or_find_target_to_center=Mock(),
            send_key=Mock(),
            navigate_until_target=Mock(return_value=True),
            box_of_screen=Mock(return_value="nav_box"),
            wait_click_ocr=Mock(return_value=True),
            skip_dialog=Mock(return_value=True),
            wait_feature=Mock(),
            wait_click_feature=Mock(return_value=False),
            ensure_main=Mock(),
            active_time=Mock(return_value=0.0),
            ocr=Mock(return_value=[]),
            next_frame=Mock(return_value="frame"),
            sleep=Mock(),
            log_info=Mock(),
            mark_task_failure=Mock(),
            _delivery_stage="已取货，提交委托",
            _delivery_failure_recorded=False,
            _delivery_cargo_in_transit=True,
            _delivery_failure_requires_abort=False,
        )
        stub._delivery_fail = lambda message: DeliveryTask._delivery_fail(stub, message)
        stub._confirm_delivery_success = lambda evidence: DeliveryTask._confirm_delivery_success(stub, evidence)
        return stub, pattern

    def test_daily_cycle_ignores_legacy_debug_flags(self):
        task = self._make_cycle(
            {"选择测试对象": "完整循环测试", "仅接取": True, "仅送货": True},
            daily_mode=True,
        )
        with patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]):
            task._run_single_delivery_cycle()

        task.accept_order.assert_called_once_with()
        self.assertEqual(task.task_to_transfer_point.call_count, 3)

    def test_daily_card_runs_normally_without_debug_keys(self):
        task = self._make_cycle({"目标券数": ["119000"], "地区切换": "武陵城"}, daily_mode=False)
        with patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]):
            task._run_single_delivery_cycle()

        task.accept_order.assert_called_once_with()
        self.assertEqual(task.task_to_transfer_point.call_count, 3)

    def test_daily_cycle_reports_each_failure(self):
        for failure in ("accept_order", "task_to_transfer_point", "to_storage_point_and_back_zip_line"):
            with self.subTest(failure=failure):
                task = self._make_cycle({}, daily_mode=True)
                task.task_to_transfer_point.return_value = failure == "to_storage_point_and_back_zip_line"
                task.to_storage_point_and_back_zip_line = Mock(return_value=False)
                if failure == "accept_order":
                    task.accept_order.return_value = False

                with patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]):
                    self.assertIs(task._run_single_delivery_cycle(), False)
                self.assertFalse(task.daily_failure_is_fatal())

    def test_daily_cycle_reports_success_and_reuses_target_box(self):
        task = self._make_cycle({}, daily_mode=True)
        task.task_to_transfer_point.return_value = True
        task.to_storage_point_and_back_zip_line = Mock(return_value=True)
        task.ends = ["target"]
        task.box = SimpleNamespace(left=object(), bottom_right=object())
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="board"))
        target_result = SimpleNamespace(name="target")
        task.wait_ocr = Mock(return_value=[target_result])
        task.wait_click_ocr = Mock(return_value=True)
        task.on_zip_line_start = Mock()
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.to_end_and_submit = Mock(return_value=True)

        with (
            patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]),
            patch("src.tasks.onetime.DeliveryTask.get_delivery_target_ocr_pattern", return_value=re.compile("target")),
        ):
            self.assertIs(task._run_single_delivery_cycle(), True)
        self.assertEqual(task.to_end_and_submit.call_count, 3)
        for call_args in task.to_end_and_submit.call_args_list:
            self.assertIs(call_args.kwargs["end_target_box"], target_result)
        self.assertEqual(task._delivery_stage, "第3单：送达成功")

    def test_daily_cycle_fails_when_submit_cannot_be_confirmed_and_marks_cargo_fatal(self):
        task = self._make_cycle({}, daily_mode=True)
        task.task_to_transfer_point.return_value = True
        task.to_storage_point_and_back_zip_line = Mock(return_value=True)
        task.ends = ["target"]
        task.box = SimpleNamespace(left=object(), bottom_right=object())
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="board"))
        target_result = SimpleNamespace(name="target")
        task.wait_ocr = Mock(return_value=[target_result])
        task.wait_click_ocr = Mock(return_value=True)
        task.on_zip_line_start = Mock()
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.to_end_and_submit = Mock(side_effect=lambda *_args, **_kwargs: task._delivery_fail("submit failed"))

        with (
            patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]),
            patch("src.tasks.onetime.DeliveryTask.get_delivery_target_ocr_pattern", return_value=re.compile("target")),
        ):
            self.assertIs(task._run_single_delivery_cycle(), False)

        self.assertTrue(task.daily_failure_is_fatal())
        self.assertEqual(task.to_end_and_submit.call_count, 1)

    def test_submit_success_template_clears_cargo_state(self):
        stub, pattern = self._make_submit_stub()
        stub.wait_feature.return_value = SimpleNamespace(name="delivery_success_check")

        self.assertTrue(DeliveryTask.to_end_and_submit(stub, pattern, end_target_box="target_box"))
        self.assertFalse(stub._delivery_cargo_in_transit)
        self.assertFalse(stub._delivery_failure_requires_abort)
        stub.wait_click_feature.assert_not_called()
        stub.ocr.assert_not_called()

    def test_submit_reward_ok_is_positive_success_evidence(self):
        stub, pattern = self._make_submit_stub()
        stub.wait_feature.return_value = None
        stub.wait_click_feature.return_value = True

        self.assertTrue(DeliveryTask.to_end_and_submit(stub, pattern, end_target_box="target_box"))
        self.assertFalse(stub._delivery_cargo_in_transit)
        stub.ocr.assert_not_called()

    def test_submit_reuses_original_target_box_for_disappearance(self):
        stub, pattern = self._make_submit_stub()
        stub.wait_feature.return_value = None
        stub.wait_click_feature.return_value = False
        stub.ocr.return_value = []
        target_box = object()

        self.assertTrue(DeliveryTask.to_end_and_submit(stub, pattern, end_target_box=target_box))
        self.assertIs(stub.ocr.call_args.kwargs["box"], target_box)
        self.assertEqual(stub.ocr.call_args.kwargs["frame"], "frame")
        self.assertFalse(stub._delivery_cargo_in_transit)

    def test_submit_target_persists_for_five_seconds_is_fatal_while_carrying(self):
        stub, pattern = self._make_submit_stub()
        stub.wait_feature.return_value = None
        stub.wait_click_feature.return_value = False
        stub.active_time.side_effect = [0.0, 0.0, 6.0]
        stub.ocr.return_value = [SimpleNamespace(name="target")]

        self.assertFalse(DeliveryTask.to_end_and_submit(stub, pattern, end_target_box="target_box"))
        self.assertTrue(stub._delivery_failure_requires_abort)
        self.assertTrue(stub._delivery_cargo_in_transit)

    def test_delivery_failure_before_pickup_is_not_fatal(self):
        task = self._make_cycle({}, daily_mode=True)
        task._delivery_cargo_in_transit = False

        self.assertFalse(task._delivery_fail("before pickup"))
        self.assertFalse(task.daily_failure_is_fatal())

    def test_run_daily_propagates_cycle_result_and_resets_mode(self):
        task = self._make_cycle({}, daily_mode=False)
        task._ensure_delivery_area_config = Mock()
        task._run_single_delivery_cycle = Mock(return_value=False)

        self.assertIs(task.run_daily(), False)
        self.assertIs(task._daily_delivery_mode, False)
        task._run_single_delivery_cycle.return_value = True
        self.assertIs(task.run_daily(), True)
        self.assertIs(task._daily_delivery_mode, False)


if __name__ == "__main__":
    unittest.main()
