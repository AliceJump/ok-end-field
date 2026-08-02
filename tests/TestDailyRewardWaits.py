import unittest
from types import SimpleNamespace

from src.tasks.daily.misc.daily_reward_mixin import DailyRewardMixin


class _DailyRewardHarness(DailyRewardMixin):
    def __init__(self, final_reward_clicked):
        self.click_results = iter([True, False, True, False, final_reward_clicked])
        self.popup_waits = 0
        self.box = SimpleNamespace(
            top=object(),
            right=object(),
            left=object(),
            bottom=object(),
            bottom_right=object(),
            top_right=object(),
            center=object(),
        )
        keys = {
            name: name
            for name in (
                "k_8d0e83fc",
                "k_39d12e73_1",
                "k_23926d61",
                "k_d7613f0e",
                "k_105cdd5a",
                "k_3ecdd4bb",
                "k_727d1bec",
                "k_25d2b666",
                "k_4d0b4688",
                "k_1c5ad36e",
            )
        }
        self.lang = SimpleNamespace(daily_routine_mixin=SimpleNamespace(**keys))

    def info_set(self, *args):
        pass

    def log_info(self, message):
        pass

    def press_key(self, key):
        pass

    def wait_click_ocr(self, **kwargs):
        return next(self.click_results)

    def find_one(self, **kwargs):
        return None

    def wait_pop_up(self):
        self.popup_waits += 1

    def send_key(self, key):
        pass

    def wait_until(self, condition, **kwargs):
        return None

    def mark_task_failure(self, message):
        raise AssertionError(message)


class TestDailyRewardWaits(unittest.TestCase):
    def test_missing_final_reward_does_not_wait_for_popup(self):
        task = _DailyRewardHarness(final_reward_clicked=False)

        self.assertTrue(task.claim_daily_rewards())

        self.assertEqual(task.popup_waits, 0)

    def test_clicked_final_reward_waits_for_popup(self):
        task = _DailyRewardHarness(final_reward_clicked=True)

        self.assertTrue(task.claim_daily_rewards())

        self.assertEqual(task.popup_waits, 1)


if __name__ == "__main__":
    unittest.main()
