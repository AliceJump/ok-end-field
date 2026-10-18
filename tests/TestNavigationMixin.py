"""导航行走门控状态测试。"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from src.image.navigation_arrival_detector import has_tracking_arrival_band


def _load_navigation_mixin():
    """隔离平台相关基类，仅加载本测试需要的导航逻辑。"""
    pyautogui = ModuleType("pyautogui")
    search_mixin = ModuleType("src.tasks.mixin.search_mixin")
    search_mixin.SearchMixin = type("SearchMixin", (), {})
    module_path = Path(__file__).parents[1] / "src/tasks/mixin/navigation_mixin.py"
    spec = importlib.util.spec_from_file_location("navigation_mixin_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "pyautogui": pyautogui,
            "src.tasks.mixin.search_mixin": search_mixin,
        },
    ):
        spec.loader.exec_module(module)
    return module.NavigationMixin


NavigationMixin = _load_navigation_mixin()


class _Detection:
    def __init__(self, x, y=0, width=20, height=20):
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class TestNavigationMixin(unittest.TestCase):
    def test_walk_key_released_on_exit_even_when_down_result_unavailable(self):
        """Task.send_key_down 不透传底层结果（恒返回 None），退出时仍必须释放 W。"""
        events = []
        stub = SimpleNamespace()
        stub.box_of_screen = lambda *args: None
        stub.active_time = lambda: 0
        stub.send_key_down = lambda key: events.append(("down", key))
        stub.send_key_up = lambda key: events.append(("up", key))

        def raise_during_target_check(*args, **kwargs):
            raise RuntimeError("stop test loop")

        stub.find_feature = raise_during_target_check

        with self.assertRaisesRegex(RuntimeError, "stop test loop"):
            NavigationMixin.navigate_until_target(
                stub,
                target="target",
                nav=None,
                target_is_ocr=False,
            )

        self.assertFalse(stub._walk_key_held)
        self.assertEqual(events, [("down", "w"), ("up", "w")])

    def test_off_center_first_detection_stops_w_before_alignment(self):
        events = []
        clock = {"time": 0.0}
        target_checks = {"count": 0}
        stub = SimpleNamespace()
        stub.box_of_screen = lambda *args: None
        stub.active_time = lambda: clock["time"]
        stub.screen_center = lambda: (960, 540)
        stub.scale_distance = lambda distance: distance
        stub.send_key_down = lambda key: events.append(("down", key))
        stub.send_key_up = lambda key: events.append(("up", key))
        stub.press_key = lambda key, **kwargs: events.append(("press", key))
        stub.log_info = lambda message, **kwargs: None
        stub.sleep = lambda seconds=0: clock.__setitem__("time", clock["time"] + seconds)

        def find_feature(feature, **kwargs):
            if feature == "target":
                target_checks["count"] += 1
                return target_checks["count"] > 1
            return _Detection(x=100)

        stub.find_feature = find_feature

        def align_target(*args, **kwargs):
            events.append(("align", "nav"))
            return False

        stub.align_ocr_or_find_target_to_center = align_target

        result = NavigationMixin.navigate_until_target(
            stub,
            target="target",
            nav="nav",
            target_is_ocr=False,
        )

        self.assertTrue(result)
        self.assertLess(events.index(("up", "w")), events.index(("align", "nav")))


class _ArrivalTask:
    def __init__(self):
        self.now = 0.0
        self.events = []
        self.frame_number = 0
        self.markers = [_Detection(950)]
        self.target_visible = False
        self._walk_key_held = False

    def active_time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def next_frame(self):
        self.frame_number += 1
        return self.frame_number

    def box_of_screen(self, *args):
        return None

    def screen_center(self):
        return 960, 540

    def scale_distance(self, value):
        return value

    def send_key_down(self, key):
        self.events.append(("down", key))

    def send_key_up(self, key):
        self.events.append(("up", key))

    def press_key(self, key, **kwargs):
        self.events.append(("press", key))

    def log_info(self, *args):
        pass

    def ocr(self, *args, **kwargs):
        self.events.append(("ocr", self.frame_number))
        self.now += 0.01
        return self.target_visible

    def find_feature(self, *args, **kwargs):
        return self.markers

    def align_ocr_or_find_target_to_center(self, *args, **kwargs):
        assert not self._walk_key_held
        self.events.append(("align",))
        self.now += 0.1
        self.markers = [_Detection(950)]
        return 960, 540

    def rotate_search(self, check_func, **kwargs):
        self.target_visible = True
        return check_func()


class TestArrivalSlowdown(unittest.TestCase):
    def navigate(self, task, probe, **kwargs):
        with patch.dict(NavigationMixin.navigate_until_target.__globals__, {"has_tracking_arrival_band": probe}):
            return NavigationMixin.navigate_until_target(
                task, target="activate", nav="gather", arrival_slowdown=True, **kwargs
            )

    def test_far_navigation_keeps_running_without_ocr_or_repeated_alignment(self):
        task = _ArrivalTask()
        self.assertFalse(self.navigate(task, lambda frame: False, time_out=0.04))
        self.assertEqual(task.events, [("down", "w"), ("up", "w")])
        self.assertGreater(task.frame_number, 3)

    def test_band_switches_to_walk_before_any_ocr_and_does_not_confirm_arrival(self):
        task = _ArrivalTask()

        def probe(frame):
            if frame == 4:
                task.target_visible = True
                return True
            return False

        self.assertTrue(self.navigate(task, probe))
        first_ocr = next(index for index, event in enumerate(task.events) if event[0] == "ocr")
        self.assertLess(task.events.index(("press", "ctrl")), first_ocr)
        self.assertEqual(task.events[first_ocr], ("ocr", 4))
        self.assertEqual(task.events.count(("press", "ctrl")), 2)  # 最后恢复奔跑状态。

    def test_band_is_only_a_slowdown_hint_and_cannot_report_arrival(self):
        task = _ArrivalTask()
        self.assertFalse(self.navigate(task, lambda frame: True, time_out=0.05))
        self.assertTrue(any(event[0] == "ocr" for event in task.events))

    def test_band_disappearing_does_not_restore_sprint_during_approach(self):
        task = _ArrivalTask()
        self.assertFalse(self.navigate(task, lambda frame: frame == 1, time_out=0.1))
        # 仅首次文本带触发一次切慢走，退出函数时恢复一次；中途不反复切换。
        self.assertEqual(task.events.count(("press", "ctrl")), 2)
        self.assertGreater(len([event for event in task.events if event[0] == "ocr"]), 2)

    def test_missing_icon_stops_before_ocr_and_never_blindly_resumes_w(self):
        task = _ArrivalTask()
        task.markers = []
        self.assertFalse(self.navigate(task, lambda frame: False, time_out=0.05))
        first_ocr = next(index for index, event in enumerate(task.events) if event[0] == "ocr")
        self.assertLess(task.events.index(("up", "w")), first_ocr)
        self.assertEqual(task.events.count(("down", "w")), 1)

    def test_recovery_rotation_can_stop_on_interaction_without_nav_icon(self):
        task = _ArrivalTask()
        task.markers = []
        self.assertTrue(self.navigate(task, lambda frame: False, time_out=12))
        self.assertEqual(task.events.count(("down", "w")), 1)

    def test_alignment_stops_movement_and_rechecks_before_resuming(self):
        task = _ArrivalTask()
        task.markers = [_Detection(100)]
        self.assertFalse(self.navigate(task, lambda frame: False, time_out=0.2))
        self.assertLess(task.events.index(("up", "w")), task.events.index(("align",)))
        self.assertGreaterEqual(task.frame_number, 2)

    def test_arrival_band_geometry_scales_and_rejects_blank_or_solid_regions(self):
        base = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for x in range(887, 1022, 20):
            cv2.putText(base, "E", (x, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            frame = cv2.resize(base, (width, height))
            self.assertTrue(has_tracking_arrival_band(frame))
        self.assertFalse(has_tracking_arrival_band(np.zeros_like(base)))
        self.assertFalse(has_tracking_arrival_band(np.full_like(base, 255)))
        self.assertFalse(has_tracking_arrival_band(None))


if __name__ == "__main__":
    unittest.main()
