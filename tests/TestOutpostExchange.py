import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.daily.daily_routine_mixin import DailyRoutineFeature
from src.tasks.daily.misc.daily_outpost_mixin import DailyOutpostMixin, _edit_distance


class TestOutpostExchange(unittest.TestCase):
    def make_exchange_feature(self, ticket_numbers, goods=None, quantities=(1,) * 11):
        feature = object.__new__(DailyRoutineFeature)
        feature.width, feature.height = 2560, 1440
        available_goods = goods if goods is not None else [SimpleNamespace(name="息壤玉葫芦")]
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
        screens = iter([[], [], available_goods, [], available_goods])
        quantity_readings = iter(quantities)

        def read_screen(*, match, **kwargs):
            if isinstance(match, re.Pattern) and match.pattern == r"\d+$":
                quantity = next(quantity_readings)
                return [] if quantity is None else [SimpleNamespace(name=str(quantity))]
            return next(screens)

        feature.wait_ocr = Mock(side_effect=read_screen)
        feature.read_outpost_ticket_num = Mock(side_effect=ticket_numbers)
        feature.click = Mock()
        feature.wait_feature = Mock(return_value=object())
        feature.plus_max = Mock(return_value=True)
        feature.wait_click_feature = Mock(return_value=True)
        feature.wait_pop_up = Mock(return_value=False)
        feature.click_confirm = Mock(return_value=True)
        feature.sleep = Mock()
        return feature

    @patch("src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text", side_effect=lambda lang, text: text)
    def test_activity_prices_round_down_and_ignore_other_goods(self, _translate):
        feature = self.make_exchange_feature([])
        for name, tickets, expected in [
            ("息壤龙泡泡", 100, 1),
            ("重息壤龙泡泡", 200, 1),
            ("息壤龙泡泡", 299, 2),
            ("重息壤龙泡泡", 299, 1),
            ("息壤龙泡泡", 99, 0),
            ("重息壤龙泡泡", 0, 0),
            ("息壤玉葫芦", 299, None),
        ]:
            with self.subTest(name=name, tickets=tickets):
                self.assertEqual(feature._get_outpost_trade_limit(name, tickets), expected)

    def test_quantity_bins_and_ocr_fallback(self):
        # 依次点击 0% 到 100%，首次达到或超过上限时停在当前档位，不再回跳。
        quantities = list(range(1, 1002, 100))
        clicks = [2024, 2056, 2088, 2120, 2152, 2184, 2216, 2248, 2280, 2312, 2344]
        cases = [
            ("全部可售", 1002, quantities, clicks),
            ("单份库存恰好达限", 1, [1], clicks[:1]),
            ("恰好20%卖20%", 201, quantities[:3], clicks[:3]),
            ("超过20%一份卖30%", 202, quantities[:4], clicks[:4]),
            ("不足20%一份卖20%", 200, quantities[:3], clicks[:3]),
            ("100%首次超限", 1000, quantities, clicks),
            ("10%首次超限", 2, quantities[:2], clicks[:2]),
            ("0%已超限", 0, quantities[:1], clicks[:1]),
            ("0%读数缺失后继续遍历", 250, [None, *quantities[1:4]], clicks[:4]),
            ("0%读数为零后继续遍历", 250, [0, *quantities[1:4]], clicks[:4]),
            ("所有档位OCR缺失", 250, [None] * 11, [*clicks, 2056]),
            ("调量OCR连续缺失", 250, [1] + [None] * 10, [*clicks, 2056]),
            ("带文字的数量", 201, [f"份数{value}" for value in quantities[:3]], clicks[:3]),
        ]
        for label, limit, readings, expected_x in cases:
            with self.subTest(label=label):
                feature = SimpleNamespace(
                    width=2560,
                    height=1440,
                    box_of_screen=Mock(),
                    click=Mock(),
                    log_info=Mock(),
                    wait_ocr=Mock(
                        side_effect=[[] if value is None else [SimpleNamespace(name=str(value))] for value in readings]
                    ),
                )
                self.assertIsNone(DailyOutpostMixin._limit_outpost_trade_quantity(feature, limit))
                self.assertEqual([c.args[:2] for c in feature.click.call_args_list], [(x, 1150) for x in expected_x])
                self.assertEqual(feature.wait_ocr.call_count, len(readings))

    @patch("src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text", side_effect=lambda lang, text: text)
    def test_over_limit_activity_trade_confirms_before_reading_balance(self, _translate):
        for name in ("息壤龙泡泡", "重息壤龙泡泡"):
            with self.subTest(name=name):
                feature = self.make_exchange_feature([1000, 0], [SimpleNamespace(name=name)], [1, 101])
                feature.wait_pop_up.return_value = True
                events = Mock()
                for action in ("read_outpost_ticket_num", "wait_click_feature", "click_confirm", "wait_pop_up"):
                    events.attach_mock(getattr(feature, action), action)
                feature.perform_outpost_exchange("天王坪援建点")
                self.assertEqual(
                    [c[0] for c in events.mock_calls],
                    [
                        "read_outpost_ticket_num",
                        "wait_click_feature",
                        "click_confirm",
                        "wait_pop_up",
                        "read_outpost_ticket_num",
                    ],
                )
                feature.click_confirm.assert_called_once_with(after_sleep=2)
                self.assertEqual(feature.click.call_args_list[-1].args[:2], (2056, 1150))

    @patch("src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text", side_effect=lambda lang, text: text)
    def test_activity_stock_remains_available_to_next_outpost(self, _translate):
        for name, price in (("息壤龙泡泡", 100), ("重息壤龙泡泡", 200)):
            with self.subTest(name=name):
                excluded_goods = set()
                for outpost in ("天王坪援建点", "心脏修缮站"):
                    feature = self.make_exchange_feature([250 * price, 0], [SimpleNamespace(name=name)], [1, 101, 201, 301])
                    feature.wait_pop_up.return_value = True
                    feature.perform_outpost_exchange(outpost, excluded_goods=excluded_goods)
                    self.assertNotIn(name, excluded_goods)
                    feature.plus_max.assert_called_once()
                    feature.wait_click_feature.assert_called_once()
                    self.assertEqual(feature.read_outpost_ticket_num.call_count, 2)

    def test_edit_distance_counts_insertions_deletions_and_substitutions(self):
        cases = [
            ("", "", 0),
            ("", "息壤", 2),
            ("息壤龙泡泡", "息壤龙泡泡", 0),
            ("息壤龙泡泡", "重息壤龙泡泡", 1),
            ("息壤龙泡泡", "息壤龙炮泡", 1),
            ("甲乙", "乙甲", 2),
            ("kitten", "sitting", 3),
        ]
        for left, right, expected in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(_edit_distance(left, right), expected)
                self.assertEqual(_edit_distance(right, left), expected)

    def test_activity_goods_choose_nearest_candidate(self):
        cases = [
            ("息壤龙泡泡", "息壤龙泡泡"),
            ("|息壤龙泡泡", "息壤龙泡泡"),
            ("重息壤龙泡泡", "重息壤龙泡泡"),
            ("|重息壤龙泡泡", "重息壤龙泡泡"),
            ("重息壤龙泡", "重息壤龙泡泡"),
            ("息壤龙泡", "息壤龙泡泡"),
            ("息壤龙炮泡", "息壤龙泡泡"),
            ("息壤龙小泡泡", "息壤龙泡泡"),
            ("息壤龙泡泡货品", "息壤龙泡泡"),
            ("｜赫铜零件丨", "赫铜零件"),
            ("息壤葫芦", "息壤葫芦"),
            ("|息壤玉葫芦", "息壤玉葫芦"),
            ("息壤玉葫", "息壤玉葫芦"),
        ]
        for ocr_name, expected_name in cases:
            with self.subTest(ocr_name=ocr_name):
                feature = self.make_exchange_feature([1000, 999], [SimpleNamespace(name=ocr_name)])

                with patch(
                    "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
                    side_effect=lambda lang, text: text,
                ):
                    feature.perform_outpost_exchange(
                        "天王坪援建点",
                        priority_list=[expected_name],
                        only_priority_goods=True,
                    )

                feature.plus_max.assert_called_once()
                selected_good = feature.click.call_args_list[0].args[0]
                self.assertEqual(selected_good.name, expected_name)

    def test_matching_keeps_length_condition_without_substring_or_distance_limit(self):
        cases = [
            ("甲", ["甲", "乙"], None),
            ("甲乙", ["甲乙丙丁"], None),
            ("甲乙", ["甲乙丙丁", "丙丁"], "丙丁"),
            ("甲乙", ["丙丁戊"], "丙丁戊"),
            ("甲乙", [], None),
            ("丁丙乙甲", ["甲乙丙丁", "丁丙乙戊"], "丁丙乙戊"),
        ]
        for text, candidates, expected in cases:
            with self.subTest(text=text, candidates=candidates):
                feature = self.make_exchange_feature([1000, 999], [SimpleNamespace(name=text)])
                with (
                    patch("src.tasks.daily.misc.daily_outpost_mixin.goods_dict", {"武陵": candidates}),
                    patch(
                        "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
                        side_effect=lambda lang, text: text,
                    ),
                ):
                    feature.perform_outpost_exchange("天王坪援建点")
                if expected is None:
                    feature.click.assert_not_called()
                    feature.plus_max.assert_not_called()
                else:
                    self.assertEqual(feature.click.call_args_list[0].args[0].name, expected)
                    feature.plus_max.assert_called_once()

    def test_equal_distances_keep_longer_candidate_first(self):
        feature = self.make_exchange_feature([1000, 999], [SimpleNamespace(name="货物甲")])
        with (
            patch("src.tasks.daily.misc.daily_outpost_mixin.goods_dict", {"武陵": ["货物", "货物甲乙"]}),
            patch(
                "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
                side_effect=lambda lang, text: text,
            ),
        ):
            feature.perform_outpost_exchange("天王坪援建点")
        self.assertEqual(feature.click.call_args_list[0].args[0].name, "货物甲乙")

    def test_both_activity_goods_follow_exact_priority(self):
        names = ["重息壤龙泡泡", "息壤龙泡泡"]
        for preferred in names:
            with self.subTest(preferred=preferred):
                feature = self.make_exchange_feature([1000, 999], [SimpleNamespace(name=name) for name in names])
                with patch(
                    "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
                    side_effect=lambda lang, text: text,
                ):
                    feature.perform_outpost_exchange(
                        "天王坪援建点",
                        priority_list=[preferred],
                        only_priority_goods=True,
                    )
                self.assertEqual(feature.click.call_args_list[0].args[0].name, preferred)

    def test_empty_selection_does_not_exchange_goods(self):
        cases = [
            ("no_goods", [], [], set()),
            ("empty_ocr_text", [""], [], set()),
            ("only_card_borders", ["|｜丨"], [], set()),
            ("no_priority_match", ["重息壤龙泡泡"], ["息壤龙泡泡"], set()),
            ("all_excluded", ["息壤玉葫芦"], [], {"息壤玉葫芦"}),
        ]
        for label, goods, priority_list, excluded_goods in cases:
            with self.subTest(reason=label):
                feature = self.make_exchange_feature([1000], [SimpleNamespace(name=name) for name in goods])

                with patch(
                    "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
                    side_effect=lambda lang, text: text,
                ):
                    feature.perform_outpost_exchange(
                        "天王坪援建点",
                        priority_list=priority_list,
                        excluded_goods=excluded_goods,
                        only_priority_goods=True,
                    )

                feature.plus_max.assert_not_called()
                feature.click.assert_not_called()

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

    def test_only_priority_goods_filters_other_goods(self):
        feature = self.make_exchange_feature(
            [1000, 999],
            [
                SimpleNamespace(name="息壤玉葫芦"),
                SimpleNamespace(name="荞愈胶囊"),
            ],
        )

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "难民暂居处",
                priority_list=["荞愈胶囊"],
                only_priority_goods=True,
            )

        selected_good = feature.click.call_args_list[0].args[0]
        self.assertEqual(selected_good.name, "荞愈胶囊")

    def test_empty_priority_list_ignores_only_priority_setting(self):
        feature = self.make_exchange_feature(
            [1000, 999],
            [SimpleNamespace(name="息壤玉葫芦")],
        )

        with patch(
            "src.tasks.daily.misc.daily_outpost_mixin.get_world_map_text",
            side_effect=lambda lang, text: text,
        ):
            feature.perform_outpost_exchange(
                "天王坪援建点",
                only_priority_goods=True,
            )

        selected_good = feature.click.call_args_list[0].args[0]
        self.assertEqual(selected_good.name, "息壤玉葫芦")

    def test_exchange_outposts_share_exclusions_only_within_area(self):
        feature = object.__new__(DailyRoutineFeature)
        feature.info_set = Mock()
        feature.log_info = Mock()
        feature.to_model_area = Mock()
        feature.ensure_main = Mock()
        feature.config = {"交易货品优先序列": ["货物甲"]}

        exclusion_sets = {}

        def perform(outpost_name, priority_list, excluded_goods, only_priority_goods):
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
        feature.wait_ocr.side_effect = [
            [],
            [],
            [SimpleNamespace(name="息壤玉葫芦")],
            [],
            [SimpleNamespace(name="息壤玉葫芦")],
        ]
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
        self.assertEqual(feature.read_outpost_ticket_num.call_count, 2)
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
        feature.read_outpost_ticket_num.assert_called_once_with("天王坪援建点")
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
        feature.read_outpost_ticket_num.assert_called_once_with("天王坪援建点")
        feature.wait_click_feature.assert_not_called()


if __name__ == "__main__":
    unittest.main()
