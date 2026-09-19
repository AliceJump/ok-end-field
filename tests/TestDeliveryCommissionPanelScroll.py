import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.tasks.daily.misc.daily_logistics_mixin import DailyLogisticsMixin


class TestTransferCommissionPanelScroll(unittest.TestCase):
    """_click_transfer_commission_in_task_panel 在任务面板内滚动查找（转交运送委托）。"""

    def make_feature(self, click_results):
        feature = object.__new__(DailyLogisticsMixin)
        feature.log_info = Mock()
        feature.wait_click_ocr = Mock(side_effect=click_results)
        feature.scroll_relative = Mock()
        feature.wait_ui_stable = Mock(return_value=True)
        feature.box = SimpleNamespace(bottom_left=object())
        feature.lang = SimpleNamespace(
            daily_routine_mixin=SimpleNamespace(
                k_1dd73947="转交运送委托",
            )
        )
        return feature

    def test_found_without_scrolling(self):
        feature = self.make_feature([True])

        self.assertTrue(feature._click_transfer_commission_in_task_panel())
        feature.scroll_relative.assert_not_called()

    def test_found_after_scrolling_down(self):
        # 首屏折叠线以下，向下滚动两次后才识别到
        feature = self.make_feature([False, False, True])

        self.assertTrue(feature._click_transfer_commission_in_task_panel())
        self.assertEqual(feature.scroll_relative.call_count, 2)

    def test_not_found_exhausts_scroll_budget(self):
        feature = self.make_feature([False] * 8)

        self.assertFalse(feature._click_transfer_commission_in_task_panel())
        self.assertEqual(feature.scroll_relative.call_count, 7)


if __name__ == "__main__":
    unittest.main()
