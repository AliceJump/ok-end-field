import unittest

import numpy as np
from ok import Box

from src.tasks.mixin.runtime_mixin import RuntimeMixin


class _DummyTask(RuntimeMixin):
    def __init__(self, debug=True, use_overlay=False):
        self.debug = debug
        self.ok_config = {"use_overlay": use_overlay}
        self.draw_calls = []
        self.logs = []

    def draw_boxes(self, feature_name=None, boxes=None, color="red", debug=True):
        self.draw_calls.append((feature_name, boxes or [], color, debug))

    def log_info(self, message):
        self.logs.append(message)

    def next_frame(self):
        return np.zeros((500, 500, 3), dtype=np.uint8)

    @property
    def detector(self):
        raise AssertionError("detector should not be used when detections are injected")


class TestYoloDetect(unittest.TestCase):
    def test_yolo_detect_supports_injected_detections_and_overlay_draw(self):
        task = _DummyTask(debug=False, use_overlay=True)
        roi = Box(100, 200, 300, 200)
        detections = [
            Box(10, 20, 30, 40, name="battle_end", confidence=0.91),
            Box(20, 30, 10, 10, name="other", confidence=0.88),
        ]

        results = task.yolo_detect(name="battle_end", box=roi, detections=detections)

        self.assertEqual(1, len(results))
        self.assertEqual("battle_end", results[0].name)
        self.assertEqual(110, results[0].x)
        self.assertEqual(220, results[0].y)
        self.assertEqual(2, len(task.draw_calls))
        self.assertEqual("yolo_raw_battle_end", task.draw_calls[0][0])
        self.assertEqual("yellow", task.draw_calls[0][2])
        self.assertEqual("yolo_filtered_battle_end", task.draw_calls[1][0])
        self.assertEqual("red", task.draw_calls[1][2])

    def test_yolo_detect_skips_draw_when_overlay_hidden(self):
        task = _DummyTask(debug=True, use_overlay=False)
        detections = [Box(10, 20, 30, 40, name="battle_end", confidence=0.91)]

        results = task.yolo_detect(name="battle_end", detections=detections)

        self.assertEqual(1, len(results))
        self.assertEqual(0, len(task.draw_calls))

    def test_yolo_detect_requires_name(self):
        task = _DummyTask(debug=False)
        with self.assertRaises(ValueError):
            task.yolo_detect(name=[])


if __name__ == "__main__":
    unittest.main()
