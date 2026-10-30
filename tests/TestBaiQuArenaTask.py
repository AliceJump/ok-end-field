import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from ok import Box

from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.BaiQuArenaTask import BaiQuArenaTask, arena_cards, arena_over_ten


def card_frame(count=0, over_ten=False, size=(1920, 1080)):
    # 内存构造牌头、数字和警告条，只验证颜色判定；不依赖截图文件。
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    for index in range(count):
        left = 160 + index * 277
        frame[250:320, left : left + 170] = 240
        frame[260:295, left + 20 : left + 35] = 40
    if over_ten:
        frame[860:895, 1300:1580] = (30, 50, 240)
    return cv2.resize(frame, size)


class TestBaiQuArenaTask(unittest.TestCase):
    def test_card_headers_and_warning_at_multiple_sizes(self):
        for size in ((1920, 1080), (1280, 720), (2560, 1440)):
            for count in range(6):
                for over_ten in (False, True):
                    with self.subTest(size=size, count=count, over_ten=over_ten):
                        frame = card_frame(count, over_ten, size)
                        self.assertEqual(arena_cards(frame), count)
                        self.assertEqual(arena_over_ten(frame), over_ten)

    def test_dialog_white_background_is_not_a_full_hand(self):
        frame = card_frame(5)
        frame[200:350] = 240
        self.assertEqual(arena_cards(frame), 0)

    def test_red_outside_warning_region_does_not_stop_drawing(self):
        frame = card_frame(3)
        frame[:200] = (0, 0, 255)
        frame[950:] = (0, 0, 255)
        self.assertFalse(arena_over_ten(frame))

    def test_draws_once_then_stops_when_power_exceeds_ten(self):
        task = Mock()
        task.frame = card_frame(3)
        task._draw_page.return_value = True

        def flip(condition, **kwargs):
            task.frame = card_frame(4, over_ten=True)
            self.assertTrue(condition())
            self.assertGreater(kwargs["settle_time"], 0)
            return True

        task.wait_until.side_effect = flip
        BaiQuArenaTask._draw_cards(task)
        task.click_relative.assert_called_once_with(0.95, 0.76)
        task._click_transition.assert_called_once_with(task._challenge_button, {"team"})

    def test_resuming_over_ten_does_not_draw_an_extra_card(self):
        task = Mock(frame=card_frame(4, over_ten=True))
        task._draw_page.return_value = True
        BaiQuArenaTask._draw_cards(task)
        task.click_relative.assert_not_called()
        task._click_transition.assert_called_once_with(task._challenge_button, {"team"})

    def test_full_hand_below_threshold_starts_challenge(self):
        task = Mock(frame=card_frame(5))
        task._draw_page.return_value = True
        BaiQuArenaTask._draw_cards(task)
        task.click_relative.assert_not_called()
        task._click_transition.assert_called_once_with(task._challenge_button, {"team"})

    def test_missing_card_change_stops_without_retry_or_start(self):
        task = Mock(frame=card_frame(2))
        task._draw_page.return_value = True
        task.wait_until.return_value = False
        with self.assertRaisesRegex(RuntimeError, "牌面变化"):
            BaiQuArenaTask._draw_cards(task)
        task.click_relative.assert_called_once()
        task._click_transition.assert_not_called()

    def test_unknown_draw_page_never_clicks(self):
        task = Mock()
        task._draw_page.return_value = False
        with self.assertRaises(RuntimeError):
            BaiQuArenaTask._draw_cards(task)
        task.click_relative.assert_not_called()

    def test_button_pages_do_not_require_ocr(self):
        for state, detector in (
            ("next", "_next_round_button"),
            ("final", "_leave_button"),
            ("team", "_team_button"),
            ("mechanics", "_mechanics_close_button"),
        ):
            with self.subTest(state=state):
                task = Mock()
                for name in (
                    "_next_round_button",
                    "_leave_button",
                    "_team_button",
                    "_mechanics_close_button",
                    "_draw_page",
                ):
                    getattr(task, name).return_value = None
                getattr(task, detector).return_value = object()
                self.assertEqual(BaiQuArenaTask._state(task), state)
                task.ocr.assert_not_called()

    def test_battle_space_uses_template_without_round_ocr(self):
        task = Mock()
        for name in ("_next_round_button", "_leave_button", "_draw_page", "_team_button", "_mechanics_close_button"):
            getattr(task, name).return_value = None
        task.find_one.return_value = object()
        self.assertEqual(BaiQuArenaTask._state(task), "space")
        task.ocr.assert_not_called()

    def test_lower_activity_entry_is_kept_but_body_and_web_text_are_rejected(self):
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440)):
            with self.subTest(size=(width, height)):
                task = Mock(frame=np.zeros((height, width, 3), dtype=np.uint8))
                task.box.left = Box(0, 0, width // 2, height)
                task._activity_box.side_effect = lambda task=task: BaiQuArenaTask._activity_box(task)
                lower_entry = Mock(x=width * 0.05, y=height * 0.82)
                description = Mock(x=width * 0.43, y=height * 0.76)
                web_text = Mock(x=width * 0.146, y=height * 0.31)
                task.ocr.return_value = [description, web_text, lower_entry]
                self.assertEqual(BaiQuArenaTask._activity_page(task), [lower_entry])
                region = task.ocr.call_args.kwargs["box"]
                self.assertEqual((region.x, region.width), (0, width // 2))
                self.assertLess(region.y, lower_entry.y)
                self.assertGreater(region.y + region.height, lower_entry.y)

    def test_start_button_requires_draw_page_and_enabled_background(self):
        for draw_page in (False, True):
            for background in (140, 255):
                with self.subTest(draw_page=draw_page, background=background):
                    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
                    if draw_page:
                        frame[790:850, 1650:1750] = (240, 190, 80)
                    frame[950:1025, 1440:1870] = background
                    task = Mock(frame=frame)
                    task._draw_page.side_effect = lambda task=task: BaiQuArenaTask._draw_page(task)
                    button = BaiQuArenaTask._challenge_button(task)
                    self.assertEqual(button is not None, draw_page and background == 255)
                    task.ocr.assert_not_called()

    def test_transition_does_not_repeat_click_when_source_disappears(self):
        task = Mock()
        task._wait_state.return_value = None
        locate = Mock(side_effect=[object(), None])
        with self.assertRaises(RuntimeError):
            BaiQuArenaTask._click_transition(task, locate, {"draw"})
        task.click_box.assert_called_once()

    def test_failed_transition_has_bounded_retries(self):
        task = Mock()
        task._wait_state.return_value = None
        with self.assertRaises(RuntimeError):
            BaiQuArenaTask._click_transition(task, Mock(return_value=object()), {"draw"})
        self.assertEqual(task.click_box.call_count, 3)

    def test_transition_allows_loading_after_source_disappears(self):
        task = Mock()
        task._wait_state.side_effect = [None, "activity"]
        locate = Mock(side_effect=[object(), None])
        self.assertEqual(BaiQuArenaTask._click_transition(task, locate, {"activity"}), "activity")
        task.click_box.assert_called_once()
        self.assertEqual([call.args[1] for call in task._wait_state.call_args_list], [3, 15])

    def test_both_settlements_bypass_generic_combat_wait(self):
        for next_round, final in ((True, False), (False, True)):
            task = Mock()
            task._next_round_button.return_value = next_round
            task._leave_button.return_value = final
            with patch.object(BattleMixin, "is_battle_settlement") as generic:
                self.assertTrue(BaiQuArenaTask.is_battle_settlement(task))
                generic.assert_not_called()

    def test_light_search_uses_yingtuo_forward_steps(self):
        task = Mock()
        task.start_touch_challenge.side_effect = lambda: BattleMixin.start_touch_challenge(task)
        task._mechanics_close_button.return_value = None
        task.strafe_search.return_value = [object()]
        BaiQuArenaTask._fight(task)
        task.strafe_search.assert_called_once()
        options = task.strafe_search.call_args.kwargs
        self.assertEqual(options["keys"], ("w",))
        self.assertEqual(options["duration"], 0.25)
        self.assertEqual(options["time_out"], 60)
        task.press_key.assert_called_once_with("f")
        task.auto_battle.assert_called_once()

    def test_inner_combat_exit_detects_activity_settlement(self):
        for next_round, final in ((True, False), (False, True)):
            task = Mock()
            task._next_round_button.return_value = next_round
            task._leave_button.return_value = final
            with patch.object(BattleMixin, "_check_single_exit_condition") as generic:
                self.assertTrue(BaiQuArenaTask._check_single_exit_condition(task))
                generic.assert_not_called()

    def test_missing_light_never_starts_auto_battle(self):
        task = Mock()
        task.start_touch_challenge.side_effect = lambda: BattleMixin.start_touch_challenge(task)
        task._mechanics_close_button.return_value = None
        task.strafe_search.return_value = None
        with self.assertRaisesRegex(RuntimeError, "触碰点"):
            BaiQuArenaTask._fight(task)
        task.press_key.assert_not_called()
        task.auto_battle.assert_not_called()

    def test_no_settlement_is_not_treated_as_success(self):
        task = Mock()
        task.start_touch_challenge.side_effect = lambda: BattleMixin.start_touch_challenge(task)
        task._mechanics_close_button.return_value = None
        task.strafe_search.return_value = "combat"
        task._wait_state.return_value = None
        with self.assertRaisesRegex(RuntimeError, "战斗结算"):
            BaiQuArenaTask._fight(task)

    def test_two_rounds_exit_to_activity_without_main_world_wait(self):
        task = Mock()
        task._wait_state.side_effect = ["activity", "draw", "team", "space", "next", "draw", "team", "space", "final"]
        self.assertTrue(BaiQuArenaTask._run_once(task))
        self.assertEqual(task._draw_cards.call_count, 2)
        self.assertEqual(task._fight.call_count, 2)
        task._click_transition.assert_any_call(task._next_round_button, {"draw"})
        task._click_transition.assert_any_call(task._leave_button, {"activity"})
        task.ensure_main.assert_not_called()

    def test_resume_final_settlement_does_not_start_another_run(self):
        task = Mock()
        task._wait_state.return_value = "final"
        self.assertTrue(BaiQuArenaTask._run_once(task))
        task._open_activity.assert_not_called()
        task._fight.assert_not_called()

    def test_unknown_start_does_not_escape_out_of_an_unrecognized_battle(self):
        task = Mock()
        task._wait_state.return_value = None
        task.is_main.return_value = False
        with self.assertRaises(RuntimeError):
            BaiQuArenaTask._run_once(task)
        task.ensure_main.assert_not_called()
        task.press_key.assert_not_called()

    def test_final_settlement_exits_without_reading_result(self):
        task = Mock()
        task._wait_state.return_value = "final"
        task.ocr.side_effect = AssertionError("Result OCR must not run")
        self.assertTrue(BaiQuArenaTask._run_once(task))
        task._click_transition.assert_called_once_with(task._leave_button, {"activity"})
        task._open_activity.assert_not_called()
        task.ocr.assert_not_called()

    def test_failed_exit_stops_remaining_games_without_counting(self):
        task = Mock(config={"刷取次数": 3})
        task._run_once.side_effect = lambda: BaiQuArenaTask._run_once(task)
        task._wait_state.return_value = "final"
        task._click_transition.side_effect = RuntimeError("页面切换失败")
        with self.assertRaisesRegex(RuntimeError, "页面切换失败"):
            BaiQuArenaTask.run.__wrapped__(task)
        task._run_once.assert_called_once()
        self.assertFalse(any("已完成" in call.args[0] for call in task.log_info.call_args_list))

    def test_default_run_completes_one_game(self):
        task = Mock(config={})
        self.assertTrue(BaiQuArenaTask.run.__wrapped__(task))
        task._run_once.assert_called_once()

    def test_configured_runs_complete_two_rounds_each(self):
        task = Mock(config={"刷取次数": 2})
        task._run_once.side_effect = lambda: BaiQuArenaTask._run_once(task)
        task._wait_state.side_effect = [
            "activity",
            "draw",
            "team",
            "space",
            "next",
            "draw",
            "team",
            "space",
            "final",
        ] * 2
        self.assertTrue(BaiQuArenaTask.run.__wrapped__(task))
        self.assertEqual(task._open_activity.call_count, 2)
        self.assertEqual(task._fight.call_count, 4)
        self.assertEqual(task._draw_cards.call_count, 4)
        self.assertEqual(
            sum(call.args == (task._leave_button, {"activity"}) for call in task._click_transition.call_args_list),
            2,
        )

    def test_early_final_settlement_counts_and_continues_remaining_games(self):
        task = Mock(config={"刷取次数": 3})
        task._run_once.side_effect = lambda: BaiQuArenaTask._run_once(task)
        task._wait_state.side_effect = ["activity", "draw", "team", "space", "final"] * 3
        task.ocr.side_effect = AssertionError("Result OCR must not run")
        self.assertTrue(BaiQuArenaTask.run.__wrapped__(task))
        self.assertEqual(task._open_activity.call_count, 3)
        self.assertEqual(task._fight.call_count, 3)
        self.assertEqual(task._click_transition.call_count, 3)
        self.assertEqual(task._run_once.call_count, 3)
        task.ocr.assert_not_called()

    def test_out_of_range_count_never_starts(self):
        for count in (0, -1, 1000):
            with self.subTest(count=count):
                task = Mock(config={"刷取次数": count})
                with self.assertRaises(ValueError):
                    BaiQuArenaTask.run.__wrapped__(task)
                task._run_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
