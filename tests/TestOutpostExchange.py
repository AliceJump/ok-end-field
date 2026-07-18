# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.daily.daily_routine_mixin import DailyRoutineFeature


class TestOutpostExchange(unittest.TestCase):
    def make_exchange_feature(self, ticket_numbers, goods=None):
        feature = object.__new__(DailyRoutineFeature)
        feature.lang = SimpleNamespace(
            daily_routine_mixin=SimpleNamespace(
                k_bb6c696b="更换",
                k_70b20820="选择货物",
            )
        )
        feature.box = SimpleNamespace(top=object(), top_left=object())
        feature.log_info = Mock()
        feature.box_of_screen = Mock(return_value=object())
        feature.wait_click_ocr = Mock(return_value=True)
        feature.wait_ocr = Mock(
            side_effect=[
                [],
                [],
                goods or [SimpleNamespace(name="息壤玉葫芦")],
            ]
        )
        feature.read_outpost_ticket_num = Mock(side_effect=ticket_numbers)
        feature.click = Mock()
        feature.wait_feature = Mock(return_value=object())
        feature.plus_max = Mock(return_value=True)
        feature.wait_click_feature = Mock(return_value=True)
        feature.wait_pop_up = Mock(return_value=False)
        return feature

    def test_full_good_names_use_exact_priority_matching(self):
        feature = self.make_exchange_feature(
            [1000, 999],
            [
                SimpleNamespace(name="精选荞愈胶囊"),
                SimpleNamespace(name="荞愈胶囊"),
            ],
        )

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "难民暂居处",
                priority_list=["荞愈胶囊", "精选荞愈胶囊"],
            )

        selected_good = feature.click.call_args_list[0].args[0]
        self.assertEqual(selected_good.name, "荞愈胶囊")

    def test_partial_good_name_keeps_regex_priority_matching(self):
        feature = self.make_exchange_feature(
            [1000, 999],
            [
                SimpleNamespace(name="荞愈胶囊"),
                SimpleNamespace(name="精选荞愈胶囊"),
            ],
        )

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "难民暂居处",
                priority_list=["精选荞愈"],
            )

        selected_good = feature.click.call_args_list[0].args[0]
        self.assertEqual(selected_good.name, "精选荞愈胶囊")

    def test_exchange_outposts_share_exclusions_only_within_area(self):
        feature = object.__new__(DailyRoutineFeature)
        feature.info_set = Mock()
        feature.log_info = Mock()
        feature.to_model_area = Mock()
        feature.ensure_main = Mock()
        feature.config = {"交易货品优先序列": ["货物甲"]}

        exclusion_sets = {}

        def perform(outpost_name, priority_list, excluded_goods):
            exclusion_sets[outpost_name] = excluded_goods
            excluded_goods.add(outpost_name)

        feature.perform_outpost_exchange = Mock(side_effect=perform)

        with (
            patch(
                "src.tasks.daily.misc.daily_outpost_mixin.areas_list",
                ["地区甲", "地区乙"],
            ),
            patch(
                "src.tasks.daily.misc.daily_outpost_mixin.outpost_dict",
                {"地区甲": ["据点甲", "据点乙"], "地区乙": ["据点丙"]},
            ),
        ):
            feature.exchange_outpost_goods()

        self.assertIs(exclusion_sets["据点甲"], exclusion_sets["据点乙"])
        self.assertIsNot(exclusion_sets["据点甲"], exclusion_sets["据点丙"])
        self.assertEqual(exclusion_sets["据点乙"], {"据点甲", "据点乙"})
        self.assertEqual(exclusion_sets["据点丙"], {"据点丙"})

    def test_clicked_exchange_is_excluded_even_when_popup_is_not_confirmed(self):
        feature = self.make_exchange_feature([1000, 1000, 0])
        excluded_goods = set()

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "天王坪援建点",
                excluded_goods=excluded_goods,
            )

        self.assertEqual(excluded_goods, {"息壤玉葫芦"})
        feature.plus_max.assert_called_once()
        feature.wait_click_feature.assert_called_once()

    def test_clicked_exchange_is_not_excluded_below_ticket_limit(self):
        feature = self.make_exchange_feature([1000, 999])
        excluded_goods = set()

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "天王坪援建点",
                excluded_goods=excluded_goods,
            )

        self.assertEqual(excluded_goods, set())
        feature.plus_max.assert_called_once()
        feature.wait_click_feature.assert_called_once()

    def test_good_is_excluded_when_exchange_button_is_not_clickable(self):
        feature = self.make_exchange_feature([1000, 0])
        feature.wait_click_feature.return_value = False
        excluded_goods = set()

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "天王坪援建点",
                excluded_goods=excluded_goods,
            )

        self.assertEqual(excluded_goods, {"息壤玉葫芦"})
        feature.wait_pop_up.assert_not_called()

    def test_good_is_excluded_when_trade_amount_is_not_available(self):
        feature = self.make_exchange_feature([1000, 0])
        feature.plus_max.return_value = False
        excluded_goods = set()

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "天王坪援建点",
                excluded_goods=excluded_goods,
            )

        self.assertEqual(excluded_goods, {"息壤玉葫芦"})
        feature.wait_click_feature.assert_not_called()


if __name__ == "__main__":
    unittest.main()
