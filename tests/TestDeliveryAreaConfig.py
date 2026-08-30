import unittest
from unittest.mock import Mock, call

from src.tasks.onetime.DeliveryTask import DeliveryTask
from src.data import delivery_area_service


class TestDeliveryAreaConfig(unittest.TestCase):
    def test_accept_order_ticket_priority_stops_after_first_match(self):
        task = object.__new__(DeliveryTask)
        task.delivery_area = "武陵"
        preferred_result = object()
        task.find_feature = Mock(return_value=[preferred_result])
        box = object()

        results, configured = task._find_accept_order_results([73100, 119000], box)

        self.assertTrue(configured)
        self.assertEqual(results, [preferred_result])
        task.find_feature.assert_called_once_with(
            feature="wuling_7_31w",
            box=box,
            threshold=0.98,
        )

    def test_accept_order_ticket_priority_falls_back_in_order(self):
        task = object.__new__(DeliveryTask)
        task.delivery_area = "武陵"
        fallback_result = object()
        task.find_feature = Mock(side_effect=[[], [fallback_result]])
        box = object()

        results, configured = task._find_accept_order_results([73100, 119000], box)

        self.assertTrue(configured)
        self.assertEqual(results, [fallback_result])
        self.assertEqual(
            task.find_feature.call_args_list,
            [
                call(feature="wuling_7_31w", box=box, threshold=0.98),
                call(feature="wuling_11_9w", box=box, threshold=0.98),
            ],
        )

    def test_accept_order_ticket_priority_rejects_unconfigured_values(self):
        task = object.__new__(DeliveryTask)
        task.delivery_area = "武陵"
        task.find_feature = Mock()

        results, configured = task._find_accept_order_results([0, 999999], object())

        self.assertFalse(configured)
        self.assertEqual(results, [])
        task.find_feature.assert_not_called()

    def test_get_delivery_target_ocr_pattern_matches_literal(self):
        pattern = delivery_area_service.get_delivery_target_ocr_pattern("武陵", "常沄")
        self.assertIsNotNone(pattern.search("常沄"))
        # OCR 字符混淆由 src/ocr_text_fix_patch.py 运行时 patch 处理，此处不测试

    def test_get_delivery_target_ocr_pattern_matches_yu_shi(self):
        pattern = delivery_area_service.get_delivery_target_ocr_pattern("武陵", "于施")
        self.assertIsNotNone(pattern.search("于施"))
        # OCR 字符混淆由 src/ocr_text_fix_patch.py 运行时 patch 处理，此处不测试


if __name__ == "__main__":
    unittest.main()
