import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.data.FeatureList import FeatureList as fL
from src.image.yingtuo_poster_template import (
    crop_poster_template,
    matches_entered_template,
    nearby_poster_templates,
    poster_view_changed,
)
from src.tasks.onetime.YingTuoTask import YingTuoError, YingTuoTask, _settled


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
        self.frame = np.random.default_rng(123).integers(0, 255, (100, 200, 3), dtype=np.uint8)
        self._entered_poster_templates = []
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

    def _scroll_poster_after_matching(self, frame):
        return YingTuoTask._scroll_poster_after_matching(self, frame)

    def _click_bar_until_detail(self, bar, frame_height, frame_width):
        return YingTuoTask._click_bar_until_detail(self, bar, frame_height, frame_width)

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

        with self.assertRaisesRegex(YingTuoError, "未能关闭苦难模式"):
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

        with self.assertRaisesRegex(YingTuoError, "未能关闭苦难模式"):
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

        with self.assertRaisesRegex(YingTuoError, "撤离后未回到"):
            YingTuoTask.challenge_hardship(task)

    def test_hardship_raises_when_evacuation_dialog_missing(self):
        task = Mock()
        task._evacuate.return_value = False

        with self.assertRaisesRegex(YingTuoError, "撤离确认框"):
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
            self.assertRaisesRegex(YingTuoError, "灰条检测.*未稳定"),
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
            self.assertRaisesRegex(YingTuoError, "灰条检测.*未稳定"),
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
            self.assertRaisesRegex(YingTuoError, "未进入关卡详情页"),
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
            self.assertRaisesRegex(YingTuoError, "未进入关卡详情页"),
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

    def test_poster_template_uses_only_poster_above_gray_bar(self):
        frame = np.random.default_rng(19).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        template = crop_poster_template(frame, 124, 828)
        self.assertEqual(template.shape, (191, 70))
        self.assertTrue(np.array_equal(template, cv2.cvtColor(frame[562:753, 89:159], cv2.COLOR_BGR2GRAY)))

    def test_template_matches_same_poster_but_not_different_poster(self):
        task = _FakeYingTuoTask()
        a = crop_poster_template(task.frame, 50, 25)
        b = crop_poster_template(task.frame, 150, 25)
        self.assertTrue(matches_entered_template(a, [a]))
        self.assertFalse(matches_entered_template(b, [a]))

    def test_any_entered_gray_bar_scrolls_without_clicking_other_bars(self):
        # 即使右侧还有未匹配条，只要屏上任意一条已经进入就向下滚动。
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        task._detect_stable_bars = Mock(
            side_effect=[
                ([_bar(50, 25), _bar(150, 25)], task.frame),
                ([_bar(50, 25)], task.frame),
                ([_bar(50, 25)], task.frame),
            ]
        )
        task._scroll_poster_after_matching = Mock(side_effect=[True, False])
        self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 2)
        self.assertEqual(task.clicks, [])

    def test_entered_later_gray_bar_also_scrolls_instead_of_clicking_first(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 150, 25)]
        task._detect_stable_bars = Mock(
            side_effect=[
                ([_bar(50, 25), _bar(150, 25)], task.frame),
                ([_bar(150, 25)], task.frame),
                ([_bar(150, 25)], task.frame),
            ]
        )
        task._scroll_poster_after_matching = Mock(side_effect=[True, False])
        self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 2)
        self.assertEqual(task.clicks, [])

    def test_unseen_screen_selects_first_bar_and_nearby_templates(self):
        task = _FakeYingTuoTask()
        bars = [_bar(50, 25), _bar(60, 25), _bar(150, 25)]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=bars):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])
        self.assertEqual(len(task._entered_poster_templates), 2)
        self.assertTrue(np.array_equal(task._entered_poster_templates[0], crop_poster_template(task.frame, 50, 25)))
        self.assertTrue(np.array_equal(task._entered_poster_templates[1], crop_poster_template(task.frame, 60, 25)))

    def test_nearby_poster_grouping_stops_at_span_limit(self):
        template = np.arange(60, dtype=np.uint8).reshape(6, 10)
        # 相邻间距 10 < 12，只有总跨度超过 46 时才被截断。
        bars = [_bar(x, 25) for x in (50, 60, 70, 80, 90, 100)]
        pairs = [(bar, template.copy()) for bar in bars]
        self.assertEqual(len(nearby_poster_templates(pairs, 200)), 5)

    def test_partial_rightmost_poster_does_not_block_complete_group(self):
        task = _FakeYingTuoTask()
        bars = [_bar(50, 25), _bar(60, 25), _bar(198, 25)]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=bars):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(len(task._entered_poster_templates), 2)
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_six_short_gray_bars_survive_yingtuo_width_filter(self):
        task = _FakeYingTuoTask()
        task.frame = np.full((1080, 1920, 3), 48, dtype=np.uint8)
        for x, width in ((122, 33), (161, 33), (200, 33), (239, 33), (279, 32), (318, 32)):
            task.frame[824:832, x : x + width] = 120
        task.frame[824:832, 400:428] = 120  # 小于影拓宽度下限的杂点。
        task.frame[824:832, 500:533] = 250  # 亮白条仍应排除。
        bars = task.detect_normal_challenge_bars(task.frame)
        self.assertEqual([bar.width for bar in bars], [33, 33, 33, 33, 32, 32])

    def test_only_incomplete_posters_fail_without_clicking_or_recording(self):
        task = _FakeYingTuoTask()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(198, 25)]):
            with self.assertRaisesRegex(YingTuoError, "均无法完整截取"):
                YingTuoTask.find_normal_challenge(task)
        self.assertEqual(task._entered_poster_templates, [])
        self.assertEqual(task.clicks, [])
        self.assertFalse(task._last_normal_attempt_recorded)
        self.assertEqual(task.scroll_count, 2)

    def test_incomplete_poster_scrolls_to_complete_candidate(self):
        task = _FakeYingTuoTask()
        task._detect_stable_bars = Mock(side_effect=[([_bar(198, 25)], task.frame), ([_bar(50, 25)], task.frame)])
        task._scroll_poster_after_matching = Mock(return_value=True)
        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        task._scroll_poster_after_matching.assert_called_once_with(task.frame)
        self.assertEqual(len(task._entered_poster_templates), 1)
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_leftmost_only_incomplete_poster_does_not_scroll_to_other_posters(self):
        task = _FakeYingTuoTask()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(2, 25)]):
            with self.assertRaisesRegex(YingTuoError, "均无法完整截取"):
                YingTuoTask.find_normal_challenge(task, leftmost_only=True)
        self.assertEqual(task.scroll_count, 0)
        self.assertEqual(task.clicks, [])

    def test_animated_clipped_poster_stops_when_bar_positions_do_not_advance(self):
        task = _FakeYingTuoTask()
        task._detect_stable_bars = Mock(return_value=([_bar(198, 25)], task.frame))
        task._scroll_poster_after_matching = Mock(return_value=True)
        with self.assertRaisesRegex(YingTuoError, "连续三次滚动未推进"):
            YingTuoTask.find_normal_challenge(task)
        self.assertEqual(task._scroll_poster_after_matching.call_count, 3)
        self.assertEqual(task.clicks, [])
        self.assertEqual(task._entered_poster_templates, [])

    def test_clipped_poster_progress_resets_consecutive_stall_count(self):
        task = _FakeYingTuoTask()
        task._detect_stable_bars = Mock(
            side_effect=[([_bar(x, 25)], task.frame) for x in (198, 198, 2, 2, 198, 198, 50)]
        )
        task._scroll_poster_after_matching = Mock(return_value=True)
        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 6)
        self.assertEqual(task.clicks, [(0.25, 0.25, {"after_sleep": 0.25})])

    def test_nearby_poster_grouping_stops_at_gap_limit(self):
        template = np.arange(60, dtype=np.uint8).reshape(6, 10)
        bars = [_bar(x, 25) for x in (50, 60, 70, 80, 100, 150)]
        pairs = [(bar, template.copy()) for bar in bars]
        self.assertEqual(len(nearby_poster_templates(pairs, 200)), 4)

    def test_view_changed_uses_poster_area_not_gray_bar_region(self):
        first = np.zeros((1080, 1920, 3), dtype=np.uint8)
        after = first.copy()
        after[808:836, :] = 240
        self.assertFalse(poster_view_changed(first, after))
        after[400:600, 100:1800] = 240
        self.assertTrue(poster_view_changed(first, after))

    def test_several_entered_screens_do_not_consume_empty_scroll_budget(self):
        task = _FakeYingTuoTask()
        bar0 = _bar(50, 25)
        bar1 = _bar(150, 25)
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        task._detect_stable_bars = Mock(side_effect=[([bar0], task.frame)] * 6 + [([bar1], task.frame)])
        task._scroll_poster_after_matching = Mock(return_value=True)

        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 6)
        self.assertEqual(task.clicks, [(0.75, 0.25, {"after_sleep": 0.25})])

    def test_matched_screen_stops_when_real_scroll_cannot_advance(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50, 25)]):
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        # 连续两次滚动后海报仍无变化，而非消耗四次无灰条预算。
        self.assertEqual(task.scroll_count, 2)

    def test_animated_matched_posters_stop_when_positions_do_not_advance(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        task._detect_stable_bars = Mock(return_value=([_bar(50, 25)], task.frame))
        task._scroll_poster_after_matching = Mock(return_value=True)
        self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 6)
        self.assertEqual(task.clicks, [])

    def test_matched_position_progress_keeps_scanning_beyond_empty_budget(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, x, 25) for x in (50, 60)]
        task._detect_stable_bars = Mock(side_effect=[([_bar(x, 25)], task.frame) for x in [50, 60] * 5 + [150]])
        task._scroll_poster_after_matching = Mock(return_value=True)
        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 10)
        self.assertEqual(task.clicks, [(0.75, 0.25, {"after_sleep": 0.25})])

    def test_end_page_attempts_each_remaining_group_and_records_before_click(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, x, 25) for x in (50, 60)]
        bars = [_bar(x, 25) for x in (50, 60, 100, 110, 150, 160)]
        recorded_at_click = []
        original_click = task.click_relative

        def click_with_record_check(x, y, **kwargs):
            recorded_at_click.append(len(task._entered_poster_templates))
            original_click(x, y, **kwargs)

        task.click_relative = click_with_record_check
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=bars):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(recorded_at_click, [4, 6])
        self.assertEqual([click[0] for click in task.clicks], [0.5, 0.75])
        self.assertEqual(len(task._entered_poster_templates), 6)

    def test_all_recorded_stalled_screen_completes_without_clicking(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, x, 25) for x in (50, 60, 150)]
        task.find_normal_challenge = lambda leftmost=False: YingTuoTask.find_normal_challenge(task, leftmost)
        with patch(
            "src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50, 25), _bar(60, 25), _bar(150, 25)]
        ):
            self.assertTrue(YingTuoTask.challenge_normal_stages(task))
        self.assertEqual(task.clicks, [])

    def test_end_page_uses_fresh_detection_instead_of_pre_scroll_coordinates(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        task._detect_stable_bars = Mock(
            side_effect=[
                ([_bar(50, 25), _bar(150, 25)], task.frame),
                ([_bar(50, 25), _bar(100, 25)], task.frame),
            ]
        )
        task._scroll_poster_after_matching = Mock(return_value=False)
        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.clicks, [(0.5, 0.25, {"after_sleep": 0.25})])
        self.assertEqual(len(task._entered_poster_templates), 2)

    def test_position_stall_at_end_still_selects_unattempted_group(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        task._detect_stable_bars = Mock(return_value=([_bar(50, 25), _bar(150, 25)], task.frame))
        task._scroll_poster_after_matching = Mock(return_value=True)
        self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task._scroll_poster_after_matching.call_count, 6)
        self.assertEqual(task.clicks, [(0.75, 0.25, {"after_sleep": 0.25})])

    def test_when_all_visible_gray_bars_entered_scrolls(self):
        task = _FakeYingTuoTask()
        task._entered_poster_templates = [crop_poster_template(task.frame, 50, 25)]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50, 25)]):
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.scroll_count, 2)
        self.assertEqual(task.clicks, [])

    def test_battle_does_not_defer_or_duplicate_template_recording(self):
        task = Mock()
        template = np.arange(30, dtype=np.uint8).reshape(5, 6)
        task._entered_poster_templates = [template]
        task.wait_battle_space.return_value = True
        task.strafe_search.return_value = "combat"
        task.auto_battle.return_value = False
        self.assertFalse(YingTuoTask.battle_and_exit(task))
        self.assertEqual(len(task._entered_poster_templates), 1)
        self.assertTrue(np.array_equal(task._entered_poster_templates[0], template))

    def test_click_attempt_records_even_when_detail_never_opens(self):
        task = _FakeYingTuoTask()
        task.detail_page_results = [False] * YingTuoTask.CHALLENGE_CLICK_ATTEMPTS
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[_bar(50, 25)]):
            with self.assertRaisesRegex(YingTuoError, "未进入关卡详情页"):
                YingTuoTask.find_normal_challenge(task)
        self.assertEqual(len(task._entered_poster_templates), 1)
        self.assertTrue(task._last_normal_attempt_recorded)

    def test_selected_group_is_skipped_on_next_scan(self):
        task = _FakeYingTuoTask()
        bars = [_bar(50, 25), _bar(60, 25), _bar(150, 25)]
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=bars):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(len(task._entered_poster_templates), 3)
        # 静止海报连续两次滚动均未推进后结束，不使用无灰条的四次预算。
        self.assertEqual(task.scroll_count, 4)
        self.assertEqual(len(task.clicks), 2)

    def test_failed_entered_poster_resumes_other_poster(self):
        task = Mock()
        task.find_normal_challenge.side_effect = [True, False]
        task.wait_until.return_value = [object()]
        task._last_normal_attempt_recorded = True
        task.battle_and_exit.return_value = False

        self.assertTrue(YingTuoTask.challenge_normal_stages(task))
        task._recover_entered_normal_challenge.assert_called_once_with("战斗未正常完成")
        task.return_to_monument.assert_not_called()

    def test_failed_unentered_poster_still_raises(self):
        task = Mock()
        task._last_normal_attempt_recorded = False
        task.find_normal_challenge.return_value = True
        task.wait_until.return_value = [object()]
        task.battle_and_exit.return_value = False
        task._recover_entered_normal_challenge.side_effect = lambda reason: (
            YingTuoTask._recover_entered_normal_challenge(task, reason)
        )
        with self.assertRaisesRegex(YingTuoError, "战斗未正常完成"):
            YingTuoTask.challenge_normal_stages(task)
        task.ensure_main.assert_not_called()

    def test_recover_entered_challenge_reenters_from_main(self):
        task = Mock()
        task._last_normal_attempt_recorded = True
        task._abandoned_normal_challenges = 0
        task._wait_monument.return_value = False
        task.enter_yingtuo.return_value = True

        YingTuoTask._recover_entered_normal_challenge(task, "挑战退出到主界面")

        self.assertEqual(task._abandoned_normal_challenges, 1)
        task.ensure_main.assert_called_once_with(time_out=180)
        task.enter_yingtuo.assert_called_once_with()

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
