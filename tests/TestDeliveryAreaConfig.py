import unittest
from unittest.mock import Mock, call, patch

from src.data.delivery_area import DELIVERY_AREA_CONFIG
from src.data.delivery_area_service import (
    get_delivery_location_coordinate,
    get_delivery_locations,
    get_delivery_target_coordinate,
    get_delivery_targets,
)
from src.tasks.onetime.DeliveryTask import DeliveryTask
from src.core.base_mixin.game_flow_mixin import GameFlowMixin


class TestDeliveryAreaConfig(unittest.TestCase):
    @patch("src.core.base_mixin.game_flow_mixin.get_world_map_text", return_value="武陵")
    @patch("src.core.base_mixin.game_flow_mixin.get_world_map_matcher", side_effect=lambda lang, area: area)
    def test_region_switch_retries_open_menu_and_confirms_current_area(self, _matcher, _text):
        task = Mock()
        task.box_of_screen.side_effect = ["current", "menu"]
        valley = Mock(name="valley")
        valley.name = "四号谷地"
        wuling = Mock(name="wuling")
        wuling.name = "武陵"
        task.wait_ocr.side_effect = [[valley], [valley], [valley], [wuling]]
        task.ocr.side_effect = [[], [wuling], [wuling]]
        task.wait_until.side_effect = lambda condition, **kwargs: next((True for _ in range(4) if condition()), None)
        self.assertTrue(GameFlowMixin.switch_to_area_delivery_list(task, "武陵"))
        self.assertEqual(task.click.call_args_list, [call(valley, after_sleep=5), call(wuling, after_sleep=5), call(wuling, after_sleep=5)])

    @patch("src.core.base_mixin.game_flow_mixin.get_world_map_text", return_value="武陵")
    @patch("src.core.base_mixin.game_flow_mixin.get_world_map_matcher", side_effect=lambda lang, area: area)
    def test_region_switch_times_out_without_clicking_when_current_area_missing(self, _matcher, _text):
        task = Mock()
        task.wait_ocr.return_value = []
        task.wait_until.side_effect = lambda condition, **kwargs: condition()
        self.assertFalse(GameFlowMixin.switch_to_area_delivery_list(task, "武陵"))
        task.click.assert_not_called()

    def test_delivery_location_records_preserve_names_and_coordinates(self):
        self.assertEqual(get_delivery_locations("武陵"), ["武陵城", "试验园区"])
        self.assertIsNotNone(get_delivery_location_coordinate("武陵", "武陵城"))

    def test_delivery_location_coordinate_reads_manual_xyz(self):
        location_config = {
            "delivery_locations": [
                {
                    "name": "武陵城",
                    "coordinate": {"x": 1.5, "y": 27, "z": -38.25},
                }
            ],
            "delivery_targets_by_location": {},
        }

        with patch.dict(DELIVERY_AREA_CONFIG, {"武陵": location_config}):
            self.assertEqual(
                get_delivery_location_coordinate("武陵", "武陵城"),
                (1.5, 27.0, -38.25),
            )

    def test_delivery_target_records_preserve_names_and_empty_coordinates(self):
        self.assertEqual(
            get_delivery_targets("武陵"),
            ["常沄", "资源", "彦宁", "齐纶", "于施", "苏白易", "普里莫", "赵昭", "裴令容", "阿禾"],
        )
        self.assertIsNone(get_delivery_target_coordinate("武陵", "常沄"))
        self.assertEqual(
            get_delivery_target_coordinate("武陵", "资源"),
            (-1363.610, 325.989, -410.345),
        )

    def test_delivery_target_coordinate_reads_manual_xyz(self):
        target_config = {
            "delivery_locations": ["武陵城"],
            "delivery_targets_by_location": {
                "武陵城": [
                    {
                        "name": "常沄",
                        "coordinate": {"x": 12.5, "y": 34, "z": -56.25},
                    }
                ]
            },
        }

        with patch.dict(DELIVERY_AREA_CONFIG, {"武陵": target_config}):
            self.assertEqual(
                get_delivery_target_coordinate("武陵", "常沄"),
                (12.5, 34.0, -56.25),
            )

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


if __name__ == "__main__":
    unittest.main()
