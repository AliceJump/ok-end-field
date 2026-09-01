"""导航行走门控状态测试。"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


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
    def test_failed_initial_walk_key_down_is_not_treated_as_held(self):
        events = []
        stub = SimpleNamespace()
        stub.box_of_screen = lambda *args: None
        stub.active_time = lambda: 0
        stub.send_key_down = lambda key: events.append(("down", key)) or False
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
        self.assertEqual(events, [("down", "w")])

    def test_off_center_first_detection_stops_w_before_alignment(self):
        events = []
        clock = {"time": 0.0}
        target_checks = {"count": 0}
        stub = SimpleNamespace()
        stub.box_of_screen = lambda *args: None
        stub.active_time = lambda: clock["time"]
        stub.screen_center = lambda: (960, 540)
        stub.scale_distance = lambda distance: distance
        stub.send_key_down = lambda key: events.append(("down", key)) or True
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


if __name__ == "__main__":
    unittest.main()
