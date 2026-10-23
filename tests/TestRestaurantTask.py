import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.data.lang import get_lang_accessor
from src.image.restaurant_detector import (
    KITCHEN_TABS,
    KitchenQueue,
    RestockCard,
    kitchen_queues,
    match_menu_icon,
    menu_card_rows,
    menu_selected,
    reference_frame,
    restock_cards,
    table_icon_rois,
)
from src.tasks.mixin.restaurant_setup_mixin import RestaurantSetupMixin
from src.tasks.onetime.RestaurantTask import RestaurantTask
from src.tasks.restaurant_inventory import RestaurantInventory


def kitchen_frame(capacity=3, used=(0, 1, 3), selected=0):
    frame = np.full((720, 1280, 3), 240, dtype=np.uint8)
    for index, (x, y, width, _) in enumerate(KITCHEN_TABS):
        if index == selected:
            frame[y + 5 : y + 30, x + 6 : x + 91] = 30
        for dot in range(capacity):
            color = (60, 220, 40) if dot >= capacity - used[index] else (150, 150, 150)
            cv2.circle(frame, (x + width - 27 + dot * 9, y + 37), 2, color, -1)
    return frame


class TestRestaurantRecognition(unittest.TestCase):
    def test_queue_reading_survives_game_resolution_and_desktop_dpi_scales(self):
        # Includes 1536x864 logical screenshots from a 1920x1080 window at 125% DPI.
        for width, height in ((1280, 720), (1536, 864), (1600, 900), (1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(size=(width, height)):
                frame = cv2.resize(kitchen_frame(), (width, height))
                queues = kitchen_queues(reference_frame(frame))
                self.assertEqual(
                    [(q.used, q.capacity, q.selected) for q in queues], [(0, 3, True), (1, 3, False), (3, 3, False)]
                )

    def test_two_slot_kitchens_and_full_queues(self):
        queues = kitchen_queues(kitchen_frame(capacity=2, used=(0, 1, 2)))
        self.assertEqual([(q.used, q.capacity) for q in queues], [(0, 2), (1, 2), (2, 2)])

    def test_unknown_dots_are_not_reported_as_an_empty_kitchen(self):
        frame = kitchen_frame()
        x, y, width, _ = KITCHEN_TABS[0]
        frame[y + 33 : y + 42, x + width - 32 : x + width - 3] = 0
        self.assertEqual([q.index for q in kitchen_queues(frame)], [1, 2])

    def test_non_game_aspect_ratio_and_small_capture_are_rejected(self):
        for size in ((1080, 2560), (800, 1280), (360, 640)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                reference_frame(np.zeros((*size, 3), dtype=np.uint8))

    def test_restock_markers_follow_scrolled_cards_and_reject_partial_rows(self):
        frame = kitchen_frame()
        for left, top in ((488, 207), (797, 302), (488, 345), (797, 405)):
            cv2.rectangle(frame, (left + 8, top + 42), (left + 20, top + 58), (30, 200, 240), -1)
        # Unrelated gold UI outside the two marker columns must be ignored.
        cv2.rectangle(frame, (900, 250), (920, 270), (30, 200, 240), -1)
        cards = restock_cards(frame)
        self.assertEqual([(c.left, c.top) for c in cards], [(488, 207), (797, 302), (488, 345)])

    def test_yellow_background_and_tiny_specks_are_not_orders(self):
        frame = np.full((720, 1280, 3), (30, 200, 240), dtype=np.uint8)
        self.assertEqual(restock_cards(frame), [])
        frame[:] = 240
        frame[250, 500] = (30, 200, 240)
        self.assertEqual(restock_cards(frame), [])


class TestRestaurantFlow(unittest.TestCase):
    def test_task_can_be_instantiated_with_the_framework_context(self):
        task = RestaurantTask(executor=Mock(), app=Mock())
        self.assertTrue(task.name)
        self.assertIsNotNone(task.lang.RestaurantTask.craftable)

    def _running_task(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.frame = kitchen_frame()
        task.active_time.side_effect = iter(range(10000))
        task._ended.side_effect = [False, True]
        task._panel_ready.return_value = True
        task.ocr.return_value = [object()]
        return task

    def test_current_session_dispatches_then_settles_once(self):
        task = self._running_task()
        task._dispatch.return_value = True
        RestaurantTask._run_restaurant_round(task)
        task._dispatch.assert_called_once()
        task._settle.assert_called_once()
        task.scroll_relative.assert_not_called()

    def test_full_kitchens_wait_without_clicking_or_scrolling(self):
        task = self._running_task()
        task.frame = kitchen_frame(used=(3, 3, 3))
        RestaurantTask._run_restaurant_round(task)
        task._dispatch.assert_not_called()
        task.scroll_relative.assert_not_called()
        task._settle.assert_called_once()

    def test_lost_panel_stops_all_input_and_reports_failure(self):
        task = self._running_task()
        task._ended.side_effect = None
        task._ended.return_value = False
        task._panel_ready.return_value = False
        task.ocr.return_value = []
        with self.assertRaisesRegex(RuntimeError, "厨房界面丢失"):
            RestaurantTask._run_restaurant_round(task)
        task._dispatch.assert_not_called()
        task.scroll_relative.assert_not_called()
        task._settle.assert_not_called()

    def test_failed_preflight_never_enters_dispatch_loop(self):
        task = self._running_task()
        task.wait_until.side_effect = RuntimeError("preflight")
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            RestaurantTask._run_restaurant_round(task)
        task._dispatch.assert_not_called()
        task._settle.assert_not_called()

    def test_failed_kitchen_switch_never_clicks_recipe(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.wait_until.return_value = None
        self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(1, 0, 3, False)))
        self.assertEqual(task.click_relative.call_count, 1)
        task._available.assert_not_called()

    def test_switch_rechecks_capacity_before_dispatch(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.wait_until.return_value = KitchenQueue(1, 3, 3, True)
        self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(1, 0, 3, False)))
        self.assertEqual(task.click_relative.call_count, 1)

    def test_unknown_or_zero_ingredients_never_click_recipe(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.frame = kitchen_frame()
        task._available.return_value = False
        with patch("src.tasks.onetime.RestaurantTask.restock_cards", return_value=[RestockCard(488, 207)]):
            self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(0, 0, 3, True)))
        task.click_relative.assert_not_called()

    def test_one_dispatch_per_fresh_snapshot_even_if_ack_is_delayed(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.frame = kitchen_frame()
        task.wait_until.return_value = None
        task._available.return_value = True
        with patch(
            "src.tasks.onetime.RestaurantTask.restock_cards",
            return_value=[RestockCard(488, 207), RestockCard(797, 302)],
        ):
            self.assertTrue(RestaurantTask._dispatch(task, KitchenQueue(0, 0, 3, True)))
        self.assertEqual(task.click_relative.call_count, 1)
        task.info_incr.assert_not_called()

    def test_ocr_quantities_are_required_including_unlimited_trial_stock(self):
        import re

        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.lang.RestaurantTask.craftable = re.compile(r"可烹[饪飪]\s*(\d+|∞)")
        for value, expected in (("可烹饪0", False), ("可烹飪20", True), ("可烹饪∞", True), ("可烹饪?", False)):
            with self.subTest(value=value):
                task.ocr.return_value = [SimpleNamespace(name=value)]
                self.assertEqual(RestaurantTask._available(task, RestockCard(488, 207)), expected)

    def test_settlement_waits_are_required_before_reporting_success(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.ocr.return_value = []
        RestaurantTask._settle(task)
        self.assertEqual(task.wait_click_ocr.call_count, 2)
        for call in task.wait_click_ocr.call_args_list:
            self.assertTrue(call.kwargs["raise_if_not_found"])
        self.assertTrue(task.wait_until.call_args.kwargs["raise_if_not_found"])

    def test_settlement_failure_does_not_report_success(self):
        task = Mock()
        task._restaurant_icons = {}
        task._restaurant_inventory = RestaurantInventory()
        task.ocr.return_value = []
        task.wait_click_ocr.side_effect = RuntimeError("missing settlement")
        with self.assertRaisesRegex(RuntimeError, "missing settlement"):
            RestaurantTask._settle(task)
        task.log_info.assert_not_called()

    def test_round_limit_and_insufficient_patronage_stop_without_another_session(self):
        task = Mock()
        task.config = {"经营轮次": 3}
        task._restaurant_dishes = ["dish:a"]
        task._prepare_restaurant.side_effect = ["fresh", "insufficient"]
        RestaurantTask.run.__wrapped__(task)
        self.assertEqual(task._run_restaurant_round.call_count, 1)
        self.assertEqual(task._prepare_restaurant.call_count, 2)

    def test_resume_does_not_invent_an_empty_inventory_for_preparation(self):
        task = Mock()
        task.config = {"经营轮次": 1}
        task._restaurant_dishes = []
        task._prepare_restaurant.return_value = "resume"
        RestaurantTask.run.__wrapped__(task)
        self.assertFalse(task._restaurant_inventory.certain)
        task.ensure_main.assert_not_called()

    def test_zero_or_unbounded_round_limit_is_rejected(self):
        for rounds in (0, -1, 101):
            task = Mock()
            task.config = {"经营轮次": rounds}
            with self.subTest(rounds=rounds), self.assertRaises(ValueError):
                RestaurantTask.run.__wrapped__(task)
            task._prepare_restaurant.assert_not_called()


class TestRestaurantInventory(unittest.TestCase):
    def test_spare_budget_preserves_a_counter_slot_but_demand_can_exceed_it(self):
        inventory = RestaurantInventory(tuple("abcdef"), fresh=True)
        for item in "abcdefa":
            inventory.submitted(item, confirmed=True)
        self.assertIsNone(inventory.spare())
        inventory.submitted("b", confirmed=True)
        self.assertEqual(inventory.portions["b"], 2)
        inventory.table(0, ("a", "a"))
        inventory.table(0, dining=True)
        self.assertIsNotNone(inventory.spare())

    def test_first_portions_for_each_dish_precede_second_portions(self):
        inventory = RestaurantInventory(("a", "b", "c"), fresh=True)
        inventory.submitted("a", confirmed=True)
        self.assertEqual(inventory.spare(), "b")
        inventory.submitted("b", confirmed=True)
        self.assertEqual(inventory.spare(), "c")
        inventory.submitted("c", confirmed=True)
        self.assertEqual(inventory.spare(), "a")

    def test_spares_include_waiting_and_cooking_portions(self):
        inventory = RestaurantInventory(("a", "b"), fresh=True)
        inventory.submitted("a", confirmed=True)
        inventory.submitted("a", confirmed=True)
        self.assertEqual(inventory.spare(), "b")
        inventory.submitted("b", confirmed=True)
        inventory.submitted("b", confirmed=True)
        self.assertIsNone(inventory.spare())

    def test_complete_dining_order_consumes_each_dish_once_and_ignores_drinks(self):
        inventory = RestaurantInventory(("a",), fresh=True)
        inventory.portions["a"] = 2
        inventory.table(0, ("a", "drink"))
        inventory.table(0, dining=True)
        inventory.table(0, dining=True)
        self.assertEqual(inventory.portions["a"], 1)
        self.assertNotIn("drink", inventory.portions)

    def test_two_identical_order_items_consume_two_portions(self):
        inventory = RestaurantInventory(("a",), fresh=True)
        inventory.portions["a"] = 2
        inventory.table(0, ("a", "a"))
        inventory.table(0, dining=True)
        self.assertEqual(inventory.portions["a"], 0)

    def test_unconfirmed_submission_disables_spares(self):
        inventory = RestaurantInventory(("a",), fresh=True)
        inventory.submitted("a", confirmed=False)
        self.assertIsNone(inventory.spare())
        self.assertEqual(inventory.portions["a"], 0)

    def test_changed_order_without_dining_never_assumes_a_delivery(self):
        inventory = RestaurantInventory(("a", "b"), fresh=True)
        inventory.portions["a"] = 2
        inventory.table(0, ("a",))
        inventory.table(0, ("b",))
        self.assertEqual(inventory.portions["a"], 2)
        self.assertFalse(inventory.certain)


class TestRestaurantSetup(unittest.TestCase):
    def task(self):
        task = Mock()
        task.frame = kitchen_frame()
        task.lang = get_lang_accessor(None)
        task._panel_ready.return_value = False
        task._restaurant_text.side_effect = lambda key, *_args, **_kwargs: key == "daily_theme"
        return task

    def test_insufficient_customers_do_not_click_open_or_reset(self):
        task = self.task()
        task.wait_ocr.return_value = [SimpleNamespace(name="59/1000")]
        self.assertEqual(RestaurantSetupMixin._prepare_restaurant(task), "insufficient")
        task.click_relative.assert_not_called()
        task._select_restaurant_menu.assert_not_called()
        self.assertNotIn("formal_start", [call.args[0] for call in task._restaurant_text.call_args_list])

    def test_unknown_customer_count_is_not_treated_as_sufficient(self):
        task = self.task()
        task.wait_ocr.return_value = [SimpleNamespace(name="?")]
        with self.assertRaisesRegex(RuntimeError, "客源数量"):
            RestaurantSetupMixin._prepare_restaurant(task)
        task.click_relative.assert_not_called()

    def test_existing_kitchen_resumes_without_reconfiguring_or_launching(self):
        task = self.task()
        task._panel_ready.return_value = True
        self.assertEqual(RestaurantSetupMixin._prepare_restaurant(task), "resume")
        task.ensure_main.assert_not_called()
        task._restaurant_text.assert_not_called()

    def test_missing_game_uses_project_ensure_main(self):
        task = self.task()
        task.get_game_hwnd.return_value = 0
        task._panel_ready.return_value = True
        RestaurantSetupMixin._prepare_restaurant(task)
        task.ensure_main.assert_called_once_with(time_out=400)

    def test_menu_failure_prevents_start_and_reaching_runtime(self):
        task = self.task()
        task.wait_ocr.return_value = [SimpleNamespace(name="100/1000")]
        task.ocr.return_value = [SimpleNamespace(name="至少需要6道菜品和3种饮品")]
        task._select_restaurant_menu.side_effect = RuntimeError("not enough food")
        with self.assertRaisesRegex(RuntimeError, "not enough food"):
            RestaurantSetupMixin._prepare_restaurant(task)
        self.assertNotIn("start", [call.args[0] for call in task._restaurant_text.call_args_list])

    def test_capacity_gate_accepts_exactly_sixty_customers(self):
        task = self.task()
        task.wait_ocr.return_value = [SimpleNamespace(name="60/1000")]
        task.ocr.return_value = [SimpleNamespace(name="至少需要6道菜品和3种饮品")]
        self.assertEqual(RestaurantSetupMixin._prepare_restaurant(task), "fresh")
        self.assertEqual(task._select_restaurant_menu.call_args_list[0].args, ("dish", 6))
        self.assertEqual(task._select_restaurant_menu.call_args_list[1].args, ("drink", 3))

    def test_open_kitchen_uses_the_project_alt_click_api_with_physical_pixels(self):
        task = self.task()
        task.width, task.height = 1920, 1080
        task.ocr.return_value = [object()]
        self.assertFalse(RestaurantSetupMixin._open_restaurant_kitchen(task))
        task.click_with_alt.assert_called_once_with(1300, 945, after_sleep=0.2)
        task.click_relative.assert_not_called()

    def test_wrong_aspect_ratio_is_rejected_before_setup_clicks(self):
        task = self.task()
        task.frame = np.zeros((900, 2000, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            RestaurantSetupMixin._prepare_restaurant(task)
        task.click_relative.assert_not_called()
        task.press_key.assert_not_called()


class TestRestaurantMenuImages(unittest.TestCase):
    def test_partial_title_row_is_not_counted_as_a_complete_menu_item(self):
        pattern = get_lang_accessor(None).RestaurantTask.craftable
        box = SimpleNamespace(x=650, y=190, height=16, name="可烹饪3")
        self.assertEqual(menu_card_rows([box], pattern, width=1280, height=720), [])

    def test_menu_minus_requires_dark_circle_and_white_bar(self):
        frame = np.full((720, 1280, 3), 240, dtype=np.uint8)
        self.assertFalse(menu_selected(frame, 552, 150))
        cv2.circle(frame, (853, 161), 13, (20, 20, 20), -1)
        self.assertFalse(menu_selected(frame, 552, 150))
        cv2.line(frame, (846, 161), (860, 161), (250, 250, 250), 2)
        self.assertTrue(menu_selected(frame, 552, 150))

    def test_available_menu_cards_use_capture_dimensions_and_skip_zero_or_partial(self):
        pattern = get_lang_accessor(None).RestaurantTask.craftable
        boxes = [
            SimpleNamespace(x=x * 1.5, y=(top + 70) * 1.5, height=16 * 1.5, name=text)
            for x, top, text in (
                (650, 150, "可烹饪3"),
                (1000, 150, "可烹饪0"),
                (1000, 250, "可烹饪∞"),
                (650, 480, "可烹饪9"),
            )
        ]
        self.assertEqual(menu_card_rows(boxes, pattern, width=1920, height=1080), [(552, 150), (870, 250)])

    def test_icon_matching_rejects_flat_pixels_and_ambiguous_candidates(self):
        icon = np.zeros((64, 64, 3), dtype=np.uint8)
        cv2.circle(icon, (32, 32), 16, (40, 180, 230), -1)
        other = np.zeros_like(icon)
        cv2.rectangle(other, (15, 15), (46, 46), (220, 80, 40), -1)
        small = cv2.resize(icon, (33, 33), interpolation=cv2.INTER_AREA)
        self.assertEqual(match_menu_icon(small, {"a": icon, "b": other}), "a")
        self.assertIsNone(match_menu_icon(small, {"a": icon, "duplicate": icon}))
        self.assertIsNone(match_menu_icon(np.zeros((33, 33, 3), dtype=np.uint8), {"a": icon}))

    def test_two_item_order_must_not_shrink_when_one_icon_is_unreadable(self):
        frame = np.full((720, 1280, 3), 240, dtype=np.uint8)
        left = 188
        frame[36, left + 40 : left + 51] = 0
        frame[32:38, left + 33 : left + 39] = 0
        frame[32:38, left + 52 : left + 58] = 0
        self.assertEqual(len(table_icon_rois(frame, 0)), 2)

    def test_gold_gourmand_card_is_excluded_from_ordinary_orders(self):
        frame = np.full((720, 1280, 3), (40, 170, 220), dtype=np.uint8)
        self.assertEqual(table_icon_rois(frame, 0), ())


if __name__ == "__main__":
    unittest.main()
