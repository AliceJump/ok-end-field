import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.image.restaurant_detector import (
    KITCHEN_TABS,
    KitchenQueue,
    RestockCard,
    kitchen_queues,
    reference_frame,
    restock_cards,
)
from src.tasks.onetime.RestaurantTask import RestaurantTask


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
        task.frame = kitchen_frame()
        task.active_time.side_effect = iter(range(10000))
        task._ended.side_effect = [False, True]
        task._panel_ready.return_value = True
        task.ocr.return_value = [object()]
        return task

    def test_current_session_dispatches_then_settles_once(self):
        task = self._running_task()
        task._dispatch.return_value = True
        RestaurantTask.run.__wrapped__(task)
        task._dispatch.assert_called_once()
        task._settle.assert_called_once()
        task.scroll_relative.assert_not_called()

    def test_full_kitchens_wait_without_clicking_or_scrolling(self):
        task = self._running_task()
        task.frame = kitchen_frame(used=(3, 3, 3))
        RestaurantTask.run.__wrapped__(task)
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
            RestaurantTask.run.__wrapped__(task)
        task._dispatch.assert_not_called()
        task.scroll_relative.assert_not_called()
        task._settle.assert_not_called()

    def test_failed_preflight_never_enters_dispatch_loop(self):
        task = self._running_task()
        task.wait_until.side_effect = RuntimeError("preflight")
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            RestaurantTask.run.__wrapped__(task)
        task._dispatch.assert_not_called()
        task._settle.assert_not_called()

    def test_failed_kitchen_switch_never_clicks_recipe(self):
        task = Mock()
        task.wait_until.return_value = None
        self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(1, 0, 3, False)))
        self.assertEqual(task.click_relative.call_count, 1)
        task._available.assert_not_called()

    def test_switch_rechecks_capacity_before_dispatch(self):
        task = Mock()
        task.wait_until.return_value = KitchenQueue(1, 3, 3, True)
        self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(1, 0, 3, False)))
        self.assertEqual(task.click_relative.call_count, 1)

    def test_unknown_or_zero_ingredients_never_click_recipe(self):
        task = Mock()
        task.frame = kitchen_frame()
        task._available.return_value = False
        with patch("src.tasks.onetime.RestaurantTask.restock_cards", return_value=[RestockCard(488, 207)]):
            self.assertFalse(RestaurantTask._dispatch(task, KitchenQueue(0, 0, 3, True)))
        task.click_relative.assert_not_called()

    def test_one_dispatch_per_fresh_snapshot_even_if_ack_is_delayed(self):
        task = Mock()
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
        task.lang.RestaurantTask.craftable = re.compile(r"可烹[饪飪]\s*(\d+|∞)")
        for value, expected in (("可烹饪0", False), ("可烹飪20", True), ("可烹饪∞", True), ("可烹饪?", False)):
            with self.subTest(value=value):
                task.ocr.return_value = [SimpleNamespace(name=value)]
                self.assertEqual(RestaurantTask._available(task, RestockCard(488, 207)), expected)

    def test_settlement_waits_are_required_before_reporting_success(self):
        task = Mock()
        task.ocr.return_value = []
        RestaurantTask._settle(task)
        self.assertEqual(task.wait_click_ocr.call_count, 2)
        for call in task.wait_click_ocr.call_args_list:
            self.assertTrue(call.kwargs["raise_if_not_found"])
        self.assertTrue(task.wait_until.call_args.kwargs["raise_if_not_found"])

    def test_settlement_failure_does_not_report_success(self):
        task = Mock()
        task.ocr.return_value = []
        task.wait_click_ocr.side_effect = RuntimeError("missing settlement")
        with self.assertRaisesRegex(RuntimeError, "missing settlement"):
            RestaurantTask._settle(task)
        task.log_info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
