import unittest
from types import SimpleNamespace

from src.tasks.daily.daily_task_runner import DailyTaskRunner
from src.tasks.mixin.liaison_mixin import LiaisonMixin


class _RunnerHarness:
    def __init__(self):
        self.config = {}
        self._daily_boat_state_confirmed = False
        self.observed_states = []

    def ensure_main(self):
        pass

    def send_key(self, key):
        pass

    def log_info(self, *args, **kwargs):
        pass

    def screenshot(self, *args, **kwargs):
        pass

    def info_set(self, *args):
        pass

    def task(self, key):
        def run():
            self.observed_states.append((key, self._daily_boat_state_confirmed))
            if key.startswith("boat_"):
                self._daily_boat_state_confirmed = True
            return True

        return run


class _LiaisonHarness(LiaisonMixin):
    def __init__(self):
        self._daily_boat_state_confirmed = True
        self.main_calls = 0
        self.logs = []
        self.map_calls = 0
        self.no_boat_entrance = False

    def ensure_main(self):
        self.main_calls += 1

    def log_info(self, message):
        self.logs.append(message)

    def ensure_map(self):
        self.map_calls += 1

    def wait_click_feature(self, **kwargs):
        # 模拟未找到帝江号入口（已在帝江号区域内时地图上无入口图标）。
        if self.no_boat_entrance:
            return None
        return object()

    def find_feature(self, **kwargs):
        return object()

    def wait_feature(self, **kwargs):
        return object()

    def click(self, *args, **kwargs):
        pass


class TestDailyBoatState(unittest.TestCase):
    def test_state_is_shared_only_across_configured_boat_tasks(self):
        task = _RunnerHarness()
        task.config = {key: True for key in ("boat_a", "boat_b", "other", "boat_c")}
        items = [(key, task.task(key)) for key in task.config]
        runner = DailyTaskRunner(task, items, shared_state_task_keys={"boat_a", "boat_b", "boat_c"})

        for key, func in items:
            self.assertTrue(runner.execute_task(key, func))

        self.assertEqual(
            task.observed_states,
            [("boat_a", False), ("boat_b", True), ("other", False), ("boat_c", False)],
        )

    def test_failed_boat_task_does_not_clear_transferred_state(self):
        task = _RunnerHarness()
        task.config = {"boat_a": True, "boat_b": True, "other": False}
        runner = DailyTaskRunner(
            task,
            [("boat_a", lambda: True), ("boat_b", lambda: False), ("other", lambda: True)],
            shared_state_task_keys={"boat_a", "boat_b"},
        )

        def transfer_then_fail():
            task._daily_boat_state_confirmed = True
            return False

        self.assertTrue(runner.execute_task("boat_a", lambda: True))
        self.assertFalse(runner.execute_task("boat_b", transfer_then_fail))
        self.assertTrue(task._daily_boat_state_confirmed)
        self.assertTrue(runner.execute_task("other", lambda: True))
        self.assertFalse(task._daily_boat_state_confirmed)

    def test_skipped_task_outside_group_clears_state(self):
        task = _RunnerHarness()
        task.config = {"boat_a": True, "other": False}
        runner = DailyTaskRunner(
            task,
            [("boat_a", lambda: True), ("other", lambda: True)],
            shared_state_task_keys={"boat_a"},
        )

        def transfer():
            task._daily_boat_state_confirmed = True
            return True

        self.assertTrue(runner.execute_task("boat_a", transfer))
        self.assertTrue(task._daily_boat_state_confirmed)
        self.assertTrue(runner.execute_task("other", lambda: True))
        self.assertFalse(task._daily_boat_state_confirmed)

    def test_confirmed_boat_state_skips_map_transfer(self):
        task = _LiaisonHarness()

        self.assertTrue(task.transfer_to_home_point(box=object(), should_check_out_boat=True))
        self.assertEqual(task.main_calls, 1)
        self.assertIn("已共享帝江号状态", task.logs[0])

    def test_specific_point_transfer_publishes_boat_state(self):
        task = _LiaisonHarness()
        task._daily_boat_state_confirmed = False

        self.assertTrue(task.transfer_to_home_point(box=object()))
        self.assertTrue(task._daily_boat_state_confirmed)
        self.assertEqual(task.map_calls, 1)

    def test_already_in_boat_publishes_boat_state(self):
        # 线程7：已在帝江号区域内（wait_click_feature 找不到入口）时提前返回，
        # 仍需置位共享状态，避免后续共享帝江号状态的任务重复传送确认。
        task = _LiaisonHarness()
        task._daily_boat_state_confirmed = False
        task.no_boat_entrance = True

        self.assertTrue(task.transfer_to_home_point(box=object(), should_check_out_boat=True))
        self.assertTrue(task._daily_boat_state_confirmed)
        self.assertTrue(any("已在帝江号区域内" in log for log in task.logs))


if __name__ == "__main__":
    unittest.main()
