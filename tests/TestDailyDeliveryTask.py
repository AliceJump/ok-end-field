import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok.util.config import Config
from ok.util.file import read_json_file, write_json_file

from src.tasks.daily.daily_task_runner import FatalTaskFailure
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
        task._delivery_fatal_failure = False
        task._delivery_last_failure = ""
        task._cargo_in_transit = False
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
        task.ends = ["target"]
        task.box = SimpleNamespace(left=object(), bottom_right=object())
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="board"))
        target_box = SimpleNamespace(name="target", x=50, y=100, width=80, height=24)
        task.wait_ocr = Mock(return_value=[target_box])
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
        self.assertTrue(all(call.args[1] is target_box for call in task.to_end_and_submit.call_args_list))
        self.assertEqual(task._delivery_stage, "第3单：送达成功")

    def test_daily_cycle_fails_when_submit_cannot_be_confirmed(self):
        task = self._make_cycle({}, daily_mode=True)
        task.task_to_transfer_point.return_value = True
        task.to_storage_point_and_back_zip_line = Mock(return_value=True)
        task.ends = ["target"]
        task.box = SimpleNamespace(left=object(), bottom_right=object())
        task.lang = SimpleNamespace(DeliveryTask=SimpleNamespace(k_b0e3a2da="board"))
        target_box = SimpleNamespace(name="target", x=50, y=100, width=80, height=24)
        task.wait_ocr = Mock(return_value=[target_box])
        task.wait_click_ocr = Mock(return_value=True)
        task.on_zip_line_start = Mock()
        task.zip_line_scroll_enabled = Mock(return_value=False)
        task.to_end_and_submit = Mock(return_value=False)

        with (
            patch("src.tasks.onetime.DeliveryTask.get_delivery_locations", return_value=[]),
            patch("src.tasks.onetime.DeliveryTask.get_delivery_target_ocr_pattern", return_value=re.compile("target")),
        ):
            self.assertIs(task._run_single_delivery_cycle(), False)

        task.mark_task_failure.assert_not_called()
        self.assertEqual(task.to_end_and_submit.call_count, 1)

    def test_run_daily_propagates_cycle_result_and_resets_mode(self):
        task = self._make_cycle({}, daily_mode=False)
        task._ensure_delivery_area_config = Mock()
        task._run_single_delivery_cycle = Mock(return_value=False)

        self.assertIs(task.run_daily(), False)
        self.assertIs(task._daily_delivery_mode, False)
        task._run_single_delivery_cycle.return_value = True
        self.assertIs(task.run_daily(), True)
        self.assertIs(task._daily_delivery_mode, False)


    def _make_submit_confirmation(self, *, reward=False, template=False, ocr_results=None):
        task = object.__new__(DeliveryTask)
        task.box = SimpleNamespace(bottom=object())
        task._delivery_target_check_box = Mock(return_value="local_target_box")
        task.wait_click_feature = Mock(return_value=reward)
        task.ensure_main = Mock()
        task.wait_feature = Mock(return_value=object() if template else None)
        task.next_frame = Mock(return_value=object())
        task.ocr = Mock(side_effect=ocr_results if ocr_results is not None else [])
        task.sleep = Mock()
        task.log_info = Mock()
        task.mark_task_failure = Mock()
        task._delivery_stage = "第1单：已取货，提交委托"
        task._delivery_failure_recorded = False
        task._delivery_fatal_failure = False
        task._delivery_last_failure = ""
        task._cargo_in_transit = True
        return task

    def test_submit_confirmation_accepts_reward_ok(self):
        task = self._make_submit_confirmation(reward=True)
        source_box = SimpleNamespace(x=60, y=120, width=90, height=24)

        self.assertTrue(task._confirm_delivery_after_submit(re.compile("target"), source_box))
        self.assertFalse(task._cargo_in_transit)
        task.wait_feature.assert_not_called()

    def test_submit_confirmation_accepts_success_template(self):
        task = self._make_submit_confirmation(template=True)
        source_box = SimpleNamespace(x=60, y=120, width=90, height=24)

        self.assertTrue(task._confirm_delivery_after_submit(re.compile("target"), source_box))
        self.assertFalse(task._cargo_in_transit)

    def test_submit_confirmation_reuses_original_target_box_until_target_disappears(self):
        task = self._make_submit_confirmation(
            ocr_results=[[SimpleNamespace(name="target")], []],
        )
        source_box = SimpleNamespace(x=60, y=120, width=90, height=24)

        self.assertTrue(task._confirm_delivery_after_submit(re.compile("target"), source_box))
        task._delivery_target_check_box.assert_called_once_with(source_box)
        self.assertTrue(all(call.kwargs["box"] == "local_target_box" for call in task.ocr.call_args_list))
        self.assertEqual(task.sleep.call_count, 1)
        self.assertFalse(task._cargo_in_transit)

    def test_submit_confirmation_fails_only_after_target_persists_in_original_box(self):
        task = self._make_submit_confirmation(ocr_results=None)
        task.ocr = Mock(return_value=[SimpleNamespace(name="target")])
        source_box = SimpleNamespace(x=60, y=120, width=90, height=24)

        self.assertFalse(task._confirm_delivery_after_submit(re.compile("target"), source_box))
        self.assertEqual(task.ocr.call_count, 20)
        self.assertTrue(task._delivery_fatal_failure)
        self.assertTrue(task._cargo_in_transit)

    def test_run_daily_pre_pickup_failure_is_not_fatal(self):
        task = self._make_cycle({}, daily_mode=False)
        task._ensure_delivery_area_config = Mock()
        task._run_single_delivery_cycle = Mock(return_value=False)

        self.assertFalse(task.run_daily())
        self.assertFalse(task._delivery_fatal_failure)

    def test_run_daily_cargo_failure_raises_account_scoped_fatal_signal(self):
        task = self._make_cycle({}, daily_mode=False)
        task._ensure_delivery_area_config = Mock()

        def fail_with_cargo():
            task._cargo_in_transit = True
            return task._delivery_fail("携货期间失败")

        task._run_single_delivery_cycle = Mock(side_effect=fail_with_cargo)
        with self.assertRaises(FatalTaskFailure):
            task.run_daily()


if __name__ == "__main__":
    unittest.main()
