import unittest
from unittest.mock import patch

from src.tasks.test.RealtimeGrayBarDetectTask import RealtimeGrayBarDetectTask


class _FakeGrayBarTask:
    def __init__(self):
        self.drawn = []

    def _is_debug_overlay_enabled(self):
        return True

    def draw_boxes(self, name, boxes, **kwargs):
        self.drawn.append((name, boxes, kwargs))


class TestRealtimeGrayBarDetectTask(unittest.TestCase):
    def test_defaults_use_full_width_and_1920_short_bar_baseline(self):
        self.assertEqual(RealtimeGrayBarDetectTask.DEFAULT_MIN_WIDTH_RATIO, 34 / 1920)
        self.assertEqual(RealtimeGrayBarDetectTask.DEFAULT_MAX_WIDTH_RATIO, 78 / 1920)
        self.assertEqual(RealtimeGrayBarDetectTask.DEFAULT_Y_MIN_RATIO, 1077 / 1440)
        self.assertEqual(RealtimeGrayBarDetectTask.DEFAULT_Y_MAX_RATIO, 1115 / 1440)

    def test_draws_boxes_when_debug_overlay_is_enabled(self):
        task = _FakeGrayBarTask()
        bar = type("Bar", (), {"x": 10, "y": 20, "width": 34, "height": 9})()
        RealtimeGrayBarDetectTask._draw_gray_bar_boxes(task, [bar])
        name, boxes, kwargs = task.drawn[0]
        self.assertEqual(name, "stage_gray_bars")
        self.assertEqual((boxes[0].x, boxes[0].y, boxes[0].width, boxes[0].height), (10, 20, 34, 9))
        self.assertEqual(kwargs, {"color": "green", "debug": True})