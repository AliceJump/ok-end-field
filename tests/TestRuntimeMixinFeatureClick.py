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


class _ClickBase:
    def click(self, *args, **kwargs):
        self.clicks.append((args, kwargs))
        return self.click_result


class _RuntimeClickHarness(RuntimeMixin, _ClickBase):
    def __init__(self, danger=False):
        self.danger = danger
        self.danger_checks = 0
        self.kills = 0
        self.sleeps = []
        self.logs = []
        self.clicks = []
        self.click_result = object()

    def sleep(self, timeout):
        self.sleeps.append(timeout)

    def find_danger(self):
        self.danger_checks += 1
        return self.danger

    def kill_game(self):
        self.kills += 1

    def log_info(self, message):
        self.logs.append(message)


class TestRuntimeMixinFeatureClick(unittest.TestCase):
    def test_default_and_explicit_left_click_keep_danger_check(self):
        for kwargs in ({}, {"key": "left"}):
            with self.subTest(kwargs=kwargs):
                task = _RuntimeClickHarness()

                result = task.click(10, 20, after_sleep=0.3, **kwargs)

                self.assertIs(result, task.click_result)
                self.assertEqual(1, task.danger_checks)
                self.assertEqual([0.1], task.sleeps)
                self.assertEqual(0, task.kills)
                self.assertEqual((10, 20), task.clicks[0][0])
                self.assertEqual("left", task.clicks[0][1]["key"])
                self.assertEqual(0.3, task.clicks[0][1]["after_sleep"])

    def test_dangerous_left_click_still_exits_before_clicking(self):
        for kwargs in ({}, {"key": "left"}):
            with self.subTest(kwargs=kwargs):
                task = _RuntimeClickHarness(danger=True)

                with self.assertRaisesRegex(Exception, "dangerous"):
                    task.click(10, 20, **kwargs)

                self.assertEqual(1, task.danger_checks)
                self.assertEqual(1, task.kills)
                self.assertEqual(["dangerous"], task.logs)
                self.assertEqual([], task.clicks)

    def test_right_and_middle_click_skip_danger_check_and_its_wait(self):
        for key in ("right", "middle"):
            with self.subTest(key=key):
                task = _RuntimeClickHarness(danger=True)

                result = task.click(10, 20, key=key, down_time=0.2, after_sleep=0.3)

                self.assertIs(result, task.click_result)
                self.assertEqual(0, task.danger_checks)
                self.assertEqual(0, task.kills)
                self.assertEqual([], task.sleeps)
                self.assertEqual([], task.logs)
                self.assertEqual((10, 20), task.clicks[0][0])
                self.assertEqual(key, task.clicks[0][1]["key"])
                self.assertEqual(0.2, task.clicks[0][1]["down_time"])
                self.assertEqual(0.3, task.clicks[0][1]["after_sleep"])

    def test_positional_non_left_key_also_skips_danger_check(self):
        task = _RuntimeClickHarness(danger=True)

        result = task.click(10, 20, False, None, -1, True, 0.01, 0, "middle")

        self.assertIs(result, task.click_result)
        self.assertEqual(0, task.danger_checks)
        self.assertEqual("middle", task.clicks[0][1]["key"])

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
        args, _kwargs = task.box_clicks[0]
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
