import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.data.FeatureList import FeatureList as fL
from src.tasks.onetime.YingTuoTask import YingTuoTask


class _FakeYingTuoTask:
    CHALLENGE_CLICK_ATTEMPTS = YingTuoTask.CHALLENGE_CLICK_ATTEMPTS
    DETAIL_PAGE_TIMEOUT = YingTuoTask.DETAIL_PAGE_TIMEOUT
    EMPTY_BARS_SETTLE_TIME = YingTuoTask.EMPTY_BARS_SETTLE_TIME
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


class TestYingTuoTask(unittest.TestCase):
    def test_battle_and_exit_waits_for_entry_before_combat(self):
        for entered in (True, False):
            with self.subTest(entered=entered):
                task = Mock()
                task.wait_battle_space.return_value = entered
                recovery = Mock()

                self.assertEqual(YingTuoTask.battle_and_exit(task, transition_recovery=recovery), entered)

                task.wait_battle_space.assert_called_once_with(transition_recovery=recovery)
                self.assertEqual(task.auto_battle.call_count, int(entered))
                self.assertEqual(task.wait_click_feature.call_count, int(entered))

    def test_run_recovery_only_clicks_visible_entry_button(self):
        task = Mock()
        task.find_normal_challenge.side_effect = [True, False]
        task.wait_until.return_value = [object()]
        button = object()
        task.find_one.side_effect = [None, button]

        def enter(transition_recovery):
            clicks = task.click.call_count
            transition_recovery()
            self.assertEqual(task.click.call_count, clicks)
            transition_recovery()
            task.click.assert_called_with(button, after_sleep=0.5)
            return True

        task.battle_and_exit.side_effect = enter
        YingTuoTask.run.__wrapped__(task)

        task.find_one.assert_called_with(feature=fL.give_gift, box=task.box_of_screen.return_value)

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

    def test_run_stops_after_two_empty_detail_pages(self):
        """详情页没有未通关图标时，返回后仍会点同一张海报，第二次就结束。"""
        task = Mock()
        task.find_normal_challenge.return_value = True
        task.wait_until.return_value = None

        YingTuoTask.run.__wrapped__(task)

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
