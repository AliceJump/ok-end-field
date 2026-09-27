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
        self.assertIn("仅接取", standalone.default_config)

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
        return task

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

    def test_daily_cycle_reports_success(self):
        task = self._make_cycle({}, daily_mode=True)
        task.task_to_transfer_point.return_value = True
        task.to_storage_point_and_back_zip_line = Mock(return_value=True)
        task.box = SimpleNamespace(left=object(), bottom_right=object())
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="board"))
        task.wait_ocr = Mock(return_value=[SimpleNamespace(name="target")])
        task.wait_click_ocr = Mock()
        task.to_end_and_submit = Mock()

        with patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]):
            self.assertIs(task._run_single_delivery_cycle(), True)
        self.assertEqual(task.to_end_and_submit.call_count, 3)

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
