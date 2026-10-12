import unittest

from src.tasks.daily.daily_task_runner import DailyTaskRunner


class _MultiAccountHarness:
    def __init__(self, account_count=2):
        self.config = {"critical": True, "after": True, "多账户模式": True}
        self._daily_boat_state_confirmed = False
        self.debug = False
        self._logged_in = True
        self.killed = False
        self.account_count = account_count
        self.current_account_index = 0
        self.current_user = ""
        self.current_account_id = ""

    def ensure_main(self, *args, **kwargs):
        pass

    def send_key(self, key):
        pass

    def log_info(self, *args, **kwargs):
        pass

    def tr(self, message, **kwargs):
        return message

    def screenshot(self, *args, **kwargs):
        pass

    def info_set(self, *args):
        pass

    def kill_game(self):
        self.killed = True

    def iter_multi_account_context(self, **kwargs):
        for index in range(self.account_count):
            self.current_account_index = index
            self.current_user = f"user-{index}"
            self.current_account_id = f"account-{index}"
            yield index, self.account_count


class TestDailyFatalAccountScope(unittest.TestCase):
    def test_fatal_false_skips_only_current_account_and_continues_next_account(self):
        task = _MultiAccountHarness()
        calls = []

        def critical():
            calls.append((task.current_account_index, "critical"))
            return task.current_account_index != 0

        def after():
            calls.append((task.current_account_index, "after"))
            return True

        runner = DailyTaskRunner(
            task,
            [("critical", critical), ("after", after)],
            fatal_task_keys={"critical"},
        )
        runner.run()

        self.assertFalse(task.killed)
        self.assertEqual(calls, [(0, "critical"), (1, "critical"), (1, "after")])
        self.assertEqual(len(runner.final_summary["per_round"]), 2)
        self.assertEqual(runner.final_summary["per_round"][0]["failed"], ["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["skipped"], ["after"])
        self.assertEqual(runner.final_summary["per_round"][1]["success"], ["critical", "after"])
        self.assertEqual(runner.final_summary["status"], "部分失败")

    def test_fatal_exception_skips_only_current_account_and_continues_next_account(self):
        task = _MultiAccountHarness()
        calls = []

        def critical():
            calls.append((task.current_account_index, "critical"))
            if task.current_account_index == 0:
                raise RuntimeError("boom")
            return True

        def after():
            calls.append((task.current_account_index, "after"))
            return True

        runner = DailyTaskRunner(
            task,
            [("critical", critical), ("after", after)],
            fatal_task_keys={"critical"},
        )
        runner.run()

        self.assertFalse(task.killed)
        self.assertEqual(calls, [(0, "critical"), (1, "critical"), (1, "after")])
        self.assertIn("boom", runner.failure_details["account-0"]["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["skipped"], ["after"])
        self.assertEqual(runner.final_summary["per_round"][1]["success"], ["critical", "after"])

    def test_dynamic_false_result_can_continue_despite_static_fatal_key(self):
        task = _MultiAccountHarness(account_count=1)
        calls = []

        def critical():
            calls.append("critical")
            return False

        def after():
            calls.append("after")
            return True

        runner = DailyTaskRunner(
            task,
            [("critical", critical, None, lambda: False), ("after", after)],
            fatal_task_keys={"critical"},
        )
        runner.run()

        self.assertEqual(calls, ["critical", "after"])
        self.assertEqual(runner.final_summary["per_round"][0]["failed"], ["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["skipped"], [])

    def test_dynamic_exception_can_continue_when_failure_is_not_fatal(self):
        task = _MultiAccountHarness(account_count=1)
        calls = []

        def critical():
            calls.append("critical")
            raise RuntimeError("recoverable")

        def after():
            calls.append("after")
            return True

        runner = DailyTaskRunner(
            task,
            [("critical", critical, None, lambda: False), ("after", after)],
            fatal_task_keys={"critical"},
        )
        runner.run()

        self.assertEqual(calls, ["critical", "after"])
        self.assertIn("recoverable", runner.failure_details["account-0"]["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["failed"], ["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["success"], ["after"])

    def test_dynamic_true_result_still_aborts_current_account(self):
        task = _MultiAccountHarness(account_count=1)
        calls = []

        def critical():
            calls.append("critical")
            return False

        def after():
            calls.append("after")
            return True

        runner = DailyTaskRunner(
            task,
            [("critical", critical, None, lambda: True), ("after", after)],
            fatal_task_keys={"critical"},
        )
        runner.run()

        self.assertEqual(calls, ["critical"])
        self.assertEqual(runner.final_summary["per_round"][0]["skipped"], ["after"])


if __name__ == "__main__":
    unittest.main()
