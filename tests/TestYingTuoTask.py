import unittest
from unittest.mock import patch

import numpy as np

from src.tasks.onetime.YingTuoTask import YingTuoTask


class _FakeYingTuoTask:
    def __init__(self):
        self.scroll_count = 0
        self.logs = []
        self.clicks = []
        self.drawn_boxes = []
        self.frame = np.zeros((100, 200, 3), dtype=np.uint8)

    def next_frame(self):
        return self.frame

    def scroll_relative(self, *_args):
        self.scroll_count += 1

    def click_relative(self, x, y):
        self.clicks.append((x, y))

    def detect_normal_challenge_bars(self, frame):
        return YingTuoTask.detect_normal_challenge_bars(self, frame)

    def _is_debug_overlay_enabled(self):
        return False

    def log_info(self, message):
        self.logs.append(message)


class TestYingTuoTask(unittest.TestCase):
    def test_gray_bars_mean_current_screen_has_challenge(self):
        task = _FakeYingTuoTask()
        leftmost = type("Bar", (), {"center_x": 50.0, "center_y": 25.0})()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[leftmost]):
            self.assertTrue(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.scroll_count, 0)
        self.assertEqual(task.clicks, [(0.25, 0.25)])

    def test_stops_after_four_empty_scrolls(self):
        task = _FakeYingTuoTask()
        with patch("src.tasks.onetime.YingTuoTask.detect_gray_bars", return_value=[]):
            self.assertIsNone(YingTuoTask.find_normal_challenge(task))
        self.assertEqual(task.scroll_count, 4)

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