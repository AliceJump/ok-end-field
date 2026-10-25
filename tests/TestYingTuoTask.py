import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.YingTuoTask import YingTuoTask, _settled


class _FakeYingTuoTask:
    CHALLENGE_CLICK_ATTEMPTS = YingTuoTask.CHALLENGE_CLICK_ATTEMPTS
    DETAIL_PAGE_TIMEOUT = YingTuoTask.DETAIL_PAGE_TIMEOUT
    EMPTY_BARS_SETTLE_TIME = YingTuoTask.EMPTY_BARS_SETTLE_TIME
    LEFTMOST_POSTER_MAX_X = YingTuoTask.LEFTMOST_POSTER_MAX_X
    _same_bars = staticmethod(YingTuoTask._same_bars)

    def __init__(self):
        self.scroll_count = 0
        self.logs = []
        self.clicks = []
        self.drawn_boxes = []
        self.frame = np.zeros((100, 200, 3), dtype=np.uint8)
        self.now = 0.0
        # 每次点击灰条后是否进入详情页，用完后默认进入
        self.detail_page_results = []

    def next_frame(self):
        return self.frame

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def wait_until(self, condition, **_kwargs):
        return condition()

    def _on_detail_page(self):
        return self.detail_page_results.pop(0) if self.detail_page_results else True

    def _detect_stable_bars(self, time_out=3.0):
        return YingTuoTask._detect_stable_bars(self, time_out)

    def scroll_relative(self, *_args):
        self.scroll_count += 1

    def click_relative(self, x, y, **kwargs):
        self.clicks.append((x, y, kwargs))

    def detect_normal_challenge_bars(self, frame):
        return YingTuoTask.detect_normal_challenge_bars(self, frame)

    def _is_debug_overlay_enabled(self):
        return False

    def log_info(self, message):
        self.logs.append(message)


def _bar(center_x, center_y):
    return type("Bar", (), {"center_x": center_x, "center_y": center_y})()


def _hardship_switch_task(modes):
    """modes 依次是切换前、点击后读到的苦难模式。切换前的模式连读两次才算稳定。"""
    task = Mock()
    task.HARDSHIP_ON_POINT = YingTuoTask.HARDSHIP_ON_POINT
    task.HARDSHIP_OFF_POINT = YingTuoTask.HARDSHIP_OFF_POINT
    task.wait_until.side_effect = lambda condition, settle_time=0, **_kwargs: (
        [condition() for _ in range(2)][-1] if settle_time else condition()
    )
    task._hardship_mode.side_effect = [modes[0], *modes]
    return task


class TestYingTuoTask(unittest.TestCase):
    def test_battle_and_exit_waits_for_entry_before_combat(self):
        for entered in (True, False):
            with self.subTest(entered=entered):
                task = Mock()
                task.wait_battle_space.return_value = entered
                task.strafe_search.return_value = "combat"
                recovery = Mock()

                self.assertEqual(YingTuoTask.battle_and_exit(task, transition_recovery=recovery), entered)

                task.wait_battle_space.assert_called_once_with(transition_recovery=recovery)
                self.assertEqual(task.auto_battle.call_count, int(entered))
                self.assertEqual(task.wait_click_feature.call_count, int(entered))

    def test_challenge_moves_to_touch_prompt_before_combat(self):
        task = Mock()
        prompt = [object()]
        task.in_combat.side_effect = [False, False]
        task.ocr.side_effect = [[], prompt]

        def search(check, **kwargs):
            self.assertEqual(kwargs["keys"], ("w",))
            self.assertGreater(kwargs["time_out"], 0)
            self.assertFalse(check())
            task.move_keys("w", duration=kwargs["duration"])
            return check()

        task.strafe_search.side_effect = search
        self.assertTrue(YingTuoTask.battle_and_exit(task))
        task.press_key.assert_called_once_with("f")
        self.assertLess(
            task.mock_calls.index(unittest.mock.call.press_key("f")),
            task.mock_calls.index(unittest.mock.call.auto_battle()),
        )
        task.wait_until.assert_called_once()
        self.assertEqual(task.wait_until.call_args.kwargs["time_out"], 10)

    def test_touch_interaction_without_combat_returns_before_auto_battle(self):
        task = Mock()
        task.strafe_search.return_value = [object()]
        task.in_combat.return_value = False

        def wait_for_start(check, **kwargs):
            self.assertEqual(kwargs["time_out"], 10)
            self.assertFalse(kwargs["raise_if_not_found"])
            return check()

        task.wait_until.side_effect = wait_for_start
        self.assertFalse(YingTuoTask.battle_and_exit(task))
        task.press_key.assert_called_once_with("f")
        task.next_frame.assert_called_once_with()
        task.auto_battle.assert_not_called()
        task.wait_click_feature.assert_not_called()

    def test_touch_interaction_confirms_combat_before_auto_battle(self):
        task = Mock()
        task.strafe_search.return_value = [object()]
        task.in_combat.side_effect = [False, True]

        def wait_for_start(check, **_kwargs):
            self.assertFalse(check())
            return check()

        task.wait_until.side_effect = wait_for_start
        self.assertTrue(YingTuoTask.battle_and_exit(task))
        self.assertEqual(task.next_frame.call_count, 2)
        self.assertLess(
            task.mock_calls.index(
                unittest.mock.call.wait_until(unittest.mock.ANY, time_out=10, raise_if_not_found=False)
            ),
            task.mock_calls.index(unittest.mock.call.auto_battle()),
        )

    def test_already_started_challenge_does_not_move_or_interact(self):
        task = Mock()
        task.in_combat.return_value = True
        task.strafe_search.side_effect = lambda check, **kwargs: check()
        self.assertTrue(YingTuoTask.battle_and_exit(task))
        task.ocr.assert_not_called()
        task.move_keys.assert_not_called()
        task.press_key.assert_not_called()

    def test_missing_touch_prompt_does_not_enter_combat_or_exit(self):
        task = Mock()
        task.strafe_search.return_value = None
        self.assertFalse(YingTuoTask.battle_and_exit(task))
        task.press_key.assert_not_called()
        task.auto_battle.assert_not_called()
        task.wait_click_feature.assert_not_called()

    def test_failed_combat_does_not_try_to_exit(self):
        task = Mock()
        task.strafe_search.return_value = "combat"
        task.auto_battle.return_value = False
        self.assertFalse(YingTuoTask.battle_and_exit(task))
        task.wait_click_feature.assert_not_called()

    def test_challenge_passes_entry_retry_to_battle(self):
        task = Mock()
        task.find_normal_challenge.side_effect = [True, False]
        task.wait_until.return_value = [object()]

        self.assertTrue(YingTuoTask.challenge_normal_stages(task))

        task.battle_and_exit.assert_called_once_with(transition_recovery=task._retry_entry)

    def test_normal_stage_turns_hardship_off_before_entering(self):
        # 实录中打完普通关卡后，游戏把详情页切到了苦难模式
        task = Mock()
        task.find_normal_challenge.side_effect = [True, False]
        task.wait_until.return_value = [object()]

        self.assertTrue(YingTuoTask.challenge_normal_stages(task))

        names = [name for name, *_ in task.mock_calls]
        self.assertLess(names.index("_set_hardship_mode"), names.index("_click_challenge_and_enter"))
        task._set_hardship_mode.assert_called_once_with(False)

    def test_normal_stage_raises_when_hardship_stays_on(self):
        task = Mock()
        task.find_normal_challenge.return_value = True
        task.wait_until.return_value = [object()]
        task._set_hardship_mode.return_value = False

        with self.assertRaisesRegex(Exception, "未能关闭苦难模式"):
            YingTuoTask.challenge_normal_stages(task)

        task._click_challenge_and_enter.assert_not_called()

    def test_retry_entry_only_clicks_visible_entry_button(self):
        task = Mock()
        button = object()
        task.find_one.side_effect = [None, button]

        YingTuoTask._retry_entry(task)
        task.click.assert_not_called()
        YingTuoTask._retry_entry(task)

        task.click.assert_called_once_with(button, after_sleep=0.5)
        task.find_one.assert_called_with(feature=fL.give_gift, box=task.box_of_screen.return_value)

    def test_hardship_enters_then_evacuates(self):
        """苦难任务入场即算完成：入场后点撤离，回详情页后关掉苦难模式。"""
        task = Mock()
        task.wait_until.return_value = "detail"

        YingTuoTask.challenge_hardship(task)

        task._open_leftmost_poster.assert_called_once_with()
        task._click_challenge_and_enter.assert_called_once_with()
        task._evacuate.assert_called_once_with()
        self.assertEqual(task._set_hardship_mode.call_args_list, [call(True), call(False)])
        task.return_to_monument.assert_called_once_with()
        task.auto_battle.assert_not_called()

    def test_hardship_raises_when_mode_stays_on_after_evacuating(self):
        task = Mock()
        task.wait_until.return_value = "detail"
        task._set_hardship_mode.side_effect = [True, False]

        with self.assertRaisesRegex(Exception, "未能关闭苦难模式"):
            YingTuoTask.challenge_hardship(task)

        task.return_to_monument.assert_not_called()

    def test_hardship_skipped_when_mode_stays_off(self):
        task = Mock()
        task._set_hardship_mode.return_value = False

        YingTuoTask.challenge_hardship(task)

        task._click_challenge_and_enter.assert_not_called()
        task.return_to_monument.assert_called_once_with()

    def test_hardship_raises_when_stuck_after_evacuating(self):
        task = Mock()
        task.wait_until.return_value = None

        with self.assertRaisesRegex(Exception, "撤离后未回到"):
            YingTuoTask.challenge_hardship(task)

    def test_hardship_raises_when_evacuation_dialog_missing(self):
        task = Mock()
        task._evacuate.return_value = False

        with self.assertRaisesRegex(Exception, "撤离确认框"):
            YingTuoTask.challenge_hardship(task)

        task.return_to_monument.assert_not_called()

    def test_evacuate_presses_esc_until_dialog_shows(self):
        # 入场后撤离按钮先灰约 2 秒，这时按 ESC 可能没反应
        for dialog_at in (1, 2):
            with self.subTest(dialog_at=dialog_at):
                task = Mock()
                task.EVACUATE_ATTEMPTS = YingTuoTask.EVACUATE_ATTEMPTS
                task.EVACUATE_DIALOG_TIMEOUT = YingTuoTask.EVACUATE_DIALOG_TIMEOUT
                task.wait_until.side_effect = [None] * (dialog_at - 1) + [object()]

                self.assertIs(YingTuoTask._evacuate(task), task.click_confirm.return_value)

                self.assertEqual(task.back.call_count, dialog_at)
                task.click_confirm.assert_called_once_with(time_out=YingTuoTask.EVACUATE_DIALOG_TIMEOUT)
                task.click.assert_not_called()

    def test_evacuate_gives_up_after_attempts(self):
        task = Mock()
        task.EVACUATE_ATTEMPTS = YingTuoTask.EVACUATE_ATTEMPTS
        task.EVACUATE_DIALOG_TIMEOUT = YingTuoTask.EVACUATE_DIALOG_TIMEOUT
        task.wait_until.return_value = None

        self.assertFalse(YingTuoTask._evacuate(task))

        self.assertEqual(task.back.call_count, YingTuoTask.EVACUATE_ATTEMPTS)
        task.click_confirm.assert_not_called()

    def test_hardship_mode_already_on_is_not_clicked(self):
        # 详情页沿用上次的苦难模式时，再点「开」会把开关切回「关」
        task = _hardship_switch_task(["on"])

        self.assertTrue(YingTuoTask._set_hardship_mode(task, True))

        task.click_relative.assert_not_called()

    def test_hardship_mode_clicks_target_half_when_different(self):
        cases = (
            (True, "off", YingTuoTask.HARDSHIP_ON_POINT),
            (False, "on", YingTuoTask.HARDSHIP_OFF_POINT),
            (True, None, YingTuoTask.HARDSHIP_ON_POINT),
        )
        for on, before, point in cases:
            with self.subTest(on=on, before=before):
                task = _hardship_switch_task([before, "on" if on else "off"])

                self.assertTrue(YingTuoTask._set_hardship_mode(task, on))

                task.click_relative.assert_called_once_with(*point, after_sleep=0.5)

    def test_hardship_mode_fails_when_title_stays(self):
        task = _hardship_switch_task(["off", "off"])

        self.assertFalse(YingTuoTask._set_hardship_mode(task, True))

    def test_hardship_mode_reads_detail_title(self):
        task = Mock()
        task.DETAIL_TITLE_BOX = YingTuoTask.DETAIL_TITLE_BOX
        task.lang.daily_routine_mixin.k_ebbfb983 = re.compile("苦难")
        cases = ((["虫傀呼应·苦难"], "on"), (["虫傀呼应", "SCPVCE DEVICE"], "off"), ([], None))
        for names, expected in cases:
            with self.subTest(names=names):
                task.ocr.return_value = [SimpleNamespace(name=name) for name in names]

                self.assertEqual(YingTuoTask._hardship_mode(task), expected)

    def test_open_leftmost_poster_clicks_fixed_point(self):
        """苦难任务只针对最左侧海报，不按 OCR 读到的海报名找。"""
        for on_detail in (True, False):
            with self.subTest(on_detail=on_detail):
                task = Mock()
                task.LEFTMOST_POSTER_POINT = YingTuoTask.LEFTMOST_POSTER_POINT
                task.DETAIL_PAGE_TIMEOUT = YingTuoTask.DETAIL_PAGE_TIMEOUT
                task.wait_until.return_value = on_detail

                self.assertEqual(YingTuoTask._open_leftmost_poster(task), on_detail)

                task.click_relative.assert_called_once_with(*YingTuoTask.LEFTMOST_POSTER_POINT)
                task.scroll_relative.assert_not_called()

    def test_leftmost_only_ignores_bars_of_other_posters(self):
        task = _FakeYingTuoTask()
        with patch(
            "src.tasks.onetime.YingTuoTask.detect_gray_bars",
            return_value=[_bar(50.0, 25.0), _bar(150.0, 25.0)],
        ):
            self.assertTrue(YingTuoTask.find_normal_challenge(task, leftmost_only=True))
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_leftmost_only_stops_without_scrolling(self):
        # 最左侧海报的灰条已打完时，右侧海报的灰条不能被点到，也不滚动
        task = _FakeYingTuoTask()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(150.0, 25.0)]):
            self.assertIsNone(YingTuoTask.find_normal_challenge(task, leftmost_only=True))
        self.assertEqual(task.scroll_count, 0)
        self.assertEqual(task.clicks, [])

    def test_gray_bars_mean_current_screen_has_challenge(self):
        task = _FakeYingTuoTask()
        leftmost = type("Bar", (), {"center_x": 50.0, "center_y": 25.0})()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[leftmost]):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.scroll_count, 0)
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_stops_after_four_empty_scrolls(self):
        task = _FakeYingTuoTask()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[]):
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.scroll_count, 4)

    def test_waits_for_stable_bars_before_click(self):
        """关卡段淡入时命中会跳变，两次检测一致后才点击。"""
        task = _FakeYingTuoTask()
        left = _bar(50.0, 25.0)
        right = _bar(150.0, 25.0)
        with patch(
            "src.tasks.onetime.YingTuoTask.detect_gray_bars",
            side_effect=[[right], [left, right], [left, right]],
        ):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_empty_bars_wait_for_fade_in(self):
        """关卡段淡入前连续两次检测为空，不能就此认定没有关卡。"""
        task = _FakeYingTuoTask()
        bar = _bar(50.0, 25.0)
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", side_effect=[[], [], [bar], [bar]]):
            bars, _ = YingTuoTask._detect_stable_bars(task)
        self.assertEqual(bars, [bar])

    def test_empty_bars_time_from_first_empty_reading(self):
        """淡入中命中会先有后无；空结果从首次为空起计时，不从开始检测起计时。"""
        task = _FakeYingTuoTask()
        bar = _bar(50.0, 25.0)
        with patch(
            "src.tasks.onetime.YingTuoTask.detect_gray_bars",
            side_effect=[[bar], [], [], [], [bar], [bar]],
        ):
            bars, _ = YingTuoTask._detect_stable_bars(task)
        self.assertEqual(bars, [bar])

    def test_raises_without_click_when_bars_never_settle(self):
        """超时仍未稳定时报错，不按最后一次结果点击。"""
        task = _FakeYingTuoTask()
        with (
            patch(
                "src.tasks.onetime.YingTuoTask.detect_gray_bars",
                side_effect=[[_bar(50.0, 25.0)], [_bar(150.0, 25.0)]] * 12,
            ),
            self.assertRaisesRegex(Exception, "灰条检测.*未稳定"),
        ):
            YingTuoTask.find_normal_challenge(task)
        self.assertEqual(task.clicks, [])

    def test_raises_without_scroll_when_empty_result_never_settles(self):
        """超时时最后一次为空但未满淡入时间，不当作空结果滚动。"""
        task = _FakeYingTuoTask()
        with (
            patch(
                "src.tasks.onetime.YingTuoTask.detect_gray_bars",
                side_effect=[[_bar(50.0, 25.0)], []] * 12,
            ),
            self.assertRaisesRegex(Exception, "灰条检测.*未稳定"),
        ):
            YingTuoTask.find_normal_challenge(task)
        self.assertEqual(task.scroll_count, 0)

    def test_retries_click_until_detail_page_opens(self):
        task = _FakeYingTuoTask()
        task.detail_page_results = [False, True]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50.0, 25.0)]):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(len(task.clicks), 2)

    def test_raises_when_detail_page_never_opens(self):
        task = _FakeYingTuoTask()
        task.detail_page_results = [False] * YingTuoTask.CHALLENGE_CLICK_ATTEMPTS
        with (
            patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50.0, 25.0)]),
            self.assertRaisesRegex(Exception, "未进入关卡详情页"),
        ):
            YingTuoTask.find_normal_challenge(task)
        self.assertEqual(len(task.clicks), YingTuoTask.CHALLENGE_CLICK_ATTEMPTS)

    def test_raises_right_after_last_failed_click(self):
        """点满次数后不再检测灰条：检测为空会走空滚动，最后误报任务完成。"""
        task = _FakeYingTuoTask()
        task.detail_page_results = [False] * YingTuoTask.CHALLENGE_CLICK_ATTEMPTS
        # 每次稳定检测读 2 帧。
        bars = [[_bar(50.0, 25.0)]] * (2 * YingTuoTask.CHALLENGE_CLICK_ATTEMPTS)
        with (
            patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", side_effect=bars + [[]] * 10),
            self.assertRaisesRegex(Exception, "未进入关卡详情页"),
        ):
            YingTuoTask.find_normal_challenge(task)
        self.assertEqual(task.scroll_count, 0)

    def test_settled_returns_none_until_reading_repeats(self):
        # 苦难模式标题淡入时可能先读成「关」再读成「开」
        condition = _settled(iter(["off", "on", "on", None, None]).__next__)

        self.assertEqual([condition() for _ in range(5)], [None, None, "on", None, None])

    def test_return_restarts_settle_when_page_changes(self):
        """wait_until 只在条件为假时重新计时，页面类型一变条件须先返回假。"""
        task = Mock()
        task._monument_or_handbook.side_effect = ["handbook", "handbook", "monument", "monument"]
        results = []

        def wait_until(condition, **kwargs):
            if "settle_time" not in kwargs:
                return True
            results.extend(condition() for _ in range(4))
            return results[-1]

        task.wait_until.side_effect = wait_until

        self.assertTrue(YingTuoTask.return_to_monument(task))
        self.assertEqual(results, [None, "handbook", None, "monument"])

    def test_return_presses_back_once_while_loading(self):
        """退出副本的加载期间不再按返回，否则加载结束后海报页会被关掉。"""
        task = Mock()
        task.RETURN_LOADING_TIMEOUT = YingTuoTask.RETURN_LOADING_TIMEOUT
        task.wait_until.side_effect = [True, "monument"]

        self.assertTrue(YingTuoTask.return_to_monument(task))

        task.back.assert_called_once_with()
        _, kwargs = task.wait_until.call_args
        self.assertEqual(kwargs["time_out"], YingTuoTask.RETURN_LOADING_TIMEOUT)
        self.assertEqual(kwargs["settle_time"], 0.5)

    def test_return_retries_back_while_detail_page_stays(self):
        task = Mock()
        task.wait_until.return_value = None

        self.assertFalse(YingTuoTask.return_to_monument(task))

        self.assertEqual(task.back.call_count, 3)
        self.assertEqual(task.wait_until.call_count, 3)

    def test_return_reenters_from_handbook_when_poster_closed(self):
        task = Mock()
        task.wait_until.side_effect = [True, "handbook"]
        task._wait_monument.return_value = True
        task.wait_click_feature.return_value = True

        self.assertTrue(YingTuoTask.return_to_monument(task))

        task.back.assert_called_once_with()
        task.wait_click_feature.assert_called_once_with(
            feature=fL.yingtuo_entrance, time_out=2, raise_if_not_found=False
        )

    def test_challenge_stops_after_two_empty_detail_pages(self):
        """详情页没有未通关图标时，返回后仍会点同一张海报，第二次就结束。"""
        task = Mock()
        task.find_normal_challenge.return_value = True
        task.wait_until.return_value = None

        self.assertFalse(YingTuoTask.challenge_normal_stages(task))

        self.assertEqual(task.find_normal_challenge.call_count, 2)
        task.return_to_monument.assert_called_once_with()
        task.click.assert_not_called()

    def test_debug_overlay_draws_gray_bar_boxes(self):
        task = _FakeYingTuoTask()
        task._is_debug_overlay_enabled = lambda: True
        task.draw_boxes = lambda name, boxes, **kwargs: task.drawn_boxes.append((name, boxes, kwargs))
        bar = type("Bar", (), {"x": 10, "y": 20, "width": 30, "height": 4})()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[bar]):
            bars = YingTuoTask.detect_normal_challenge_bars(task, task.frame)
        self.assertEqual(bars, [bar])
        name, boxes, kwargs = task.drawn_boxes[0]
        self.assertEqual(name, "yingtuo_gray_bars")
        self.assertEqual((boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height), (10, 20, 30, 4))
        self.assertEqual(kwargs, {"color": "green", "debug": True})
