# -*- coding: utf-8 -*-
import unittest

from src.data.delivery_area import DELIVERY_AREA_CONFIG
from src.data import delivery_area_service


class TestDeliveryAreaConfig(unittest.TestCase):
    def test_get_transfer_search_area_returns_preset_config(self):
        self.assertEqual(
            delivery_area_service.get_transfer_search_area("武陵城", "武陵"),
            {"preset": "top"},
        )
        self.assertEqual(
            delivery_area_service.get_transfer_search_area("试验园区", "武陵"),
            {"preset": "right"},
        )

    def test_get_transfer_search_area_supports_custom_box_config(self):
        area_name = "测试区域"
        DELIVERY_AREA_CONFIG[area_name] = {
            "delivery_locations": ["测试点"],
            "delivery_targets_by_location": {"测试点": []},
            "transfer_search_area": {
                "测试点": {"x": 0.1, "y": 0.2, "to_x": 0.8, "to_y": 0.9},
            },
            "ocr_priority_locations": ["测试点"],
        }
        try:
            self.assertEqual(
                delivery_area_service.get_transfer_search_area("测试点", area_name),
                {"x": 0.1, "y": 0.2, "to_x": 0.8, "to_y": 0.9},
            )
        finally:
            DELIVERY_AREA_CONFIG.pop(area_name, None)

    def test_get_task_model_area_returns_configured_value(self):
        self.assertEqual(
            delivery_area_service.get_task_model_area("武陵"),
            "武陵",
        )

    def test_get_delivery_target_ocr_pattern_applies_override(self):
        pattern = delivery_area_service.get_delivery_target_ocr_pattern("武陵", "常沄")
        self.assertIsNotNone(pattern.search("常云"))
        self.assertIsNotNone(pattern.search("常沄"))

    def test_get_accept_feature_labels_returns_mapping_by_target_ticket(self):
        self.assertEqual(
            delivery_area_service.get_accept_feature_labels("武陵", "73100"),
            ["wuling_7_31w"],
        )
        self.assertEqual(
            delivery_area_service.get_accept_feature_labels("武陵", "79800"),
            ["wuling_7_98w"],
        )
        self.assertEqual(
            delivery_area_service.get_accept_feature_labels("武陵", "119000"),
            ["wuling_11_9w"],
        )
        self.assertEqual(
            delivery_area_service.get_accept_feature_labels("武陵", "000000"),
            [],
        )

    def test_get_accept_feature_labels_returns_empty_for_invalid_label(self):
        area_name = "测试区域"
        DELIVERY_AREA_CONFIG[area_name] = {
            "feature_label_area_code": "test_area",
            "delivery_locations": ["测试点"],
            "delivery_targets_by_location": {"测试点": []},
            "transfer_search_area": {"测试点": {"preset": "top"}},
            "ocr_priority_locations": ["测试点"],
        }
        try:
            self.assertEqual(
                delivery_area_service.get_accept_feature_labels(area_name, "73100"),
                [],
            )
        finally:
            DELIVERY_AREA_CONFIG.pop(area_name, None)


if __name__ == "__main__":
    unittest.main()
