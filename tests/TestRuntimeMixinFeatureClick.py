import unittest

from ok import Box

from src.core.base_mixin.runtime_mixin import RuntimeMixin


class _RuntimeFeatureClickHarness(RuntimeMixin):
    def __init__(self, result):
        self.result = result
        self.alt_clicks = []
        self.box_clicks = []
        self.sleeps = []

    def wait_until(self, func, **kwargs):
        return func()

    def find_one(self, *args, **kwargs):
        return self.result

    def click_with_alt(self, *args, **kwargs):
        self.alt_clicks.append((args, kwargs))

    def click_box(self, *args, **kwargs):
        self.box_clicks.append((args, kwargs))

    def sleep(self, timeout):
        self.sleeps.append(timeout)


class TestRuntimeMixinFeatureClick(unittest.TestCase):
    def test_wait_click_feature_alt_uses_alt_click_with_relative_point(self):
        box = Box(10, 20, 100, 50, name="target_feature")
        task = _RuntimeFeatureClickHarness(box)

        clicked = task.wait_click_feature(
            "target_feature",
            relative_x=0.25,
            relative_y=0.75,
            after_sleep=1.2,
            alt=True,
        )

        self.assertTrue(clicked)
        self.assertEqual(len(task.alt_clicks), 1)
        self.assertEqual(task.box_clicks, [])
        args, kwargs = task.alt_clicks[0]
        self.assertEqual(args[0], 35)
        self.assertEqual(args[1], 58)
        self.assertEqual(kwargs["name"], "target_feature")
        self.assertEqual(kwargs["after_sleep"], 1.2)

    def test_wait_click_feature_without_alt_keeps_click_box_path(self):
        box = Box(10, 20, 100, 50, name="target_feature")
        task = _RuntimeFeatureClickHarness(box)

        clicked = task.wait_click_feature("target_feature", relative_x=0.2, relative_y=0.3)

        self.assertTrue(clicked)
        self.assertEqual(task.alt_clicks, [])
        self.assertEqual(len(task.box_clicks), 1)
        args, kwargs = task.box_clicks[0]
        self.assertIs(args[0], box)
        self.assertEqual(args[1], 0.2)
        self.assertEqual(args[2], 0.3)

    def test_wait_click_ocr_uses_requested_recheck_time(self):
        task = _RuntimeFeatureClickHarness(result=object())
        task.wait_ocr = lambda *args, **kwargs: task.result
        task.ocr = lambda *args, **kwargs: task.result
        task.click = lambda *args, **kwargs: None

        result = task.wait_click_ocr(match="confirm", recheck_time=0.25)

        self.assertIs(result, task.result)
        self.assertEqual(task.sleeps, [0.25])


if __name__ == "__main__":
    unittest.main()
