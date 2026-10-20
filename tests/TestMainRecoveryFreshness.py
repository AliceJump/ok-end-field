from types import SimpleNamespace
import unittest

from src.core.base_mixin.game_flow_mixin import GameFlowMixin


class _Task:
    def __init__(self, world_after_ocr):
        self.world_after_ocr = world_after_ocr
        self.available_world = False
        self.current_world = False
        self.now = 0
        self.frames = 0
        self.backs = 0
        self.once_sleep_time = 1.5
        self.box = SimpleNamespace(bottom=None)
        self.lang = SimpleNamespace(game_flow_mixin=SimpleNamespace(k_8b2ca27a="continue", k_7cd2e0c0="leave"))

    def next_frame(self):
        self.frames += 1
        self.current_world = self.available_world

    def in_world(self):
        return self.current_world

    def wait_login(self):
        return False

    def find_one(self, **kwargs):
        return None

    def box_of_screen(self, *coordinates):
        return None

    def handle_ocr_rules(self, rules):
        self.now += 3
        self.available_world = self.world_after_ocr
        return False

    def active_time(self):
        return self.now

    def log_info(self, message):
        pass

    def back(self):
        self.backs += 1


class TestMainRecoveryFreshness(unittest.TestCase):
    def test_world_appearing_during_ocr_does_not_receive_escape(self):
        task = _Task(True)
        self.assertTrue(GameFlowMixin.is_main(task, esc=True))
        self.assertEqual(task.backs, 0)
        self.assertEqual(task.frames, 2)

    def test_persistent_menu_still_receives_recovery(self):
        task = _Task(False)
        self.assertFalse(GameFlowMixin.is_main(task, esc=True))
        self.assertEqual(task.backs, 1)
        self.assertEqual(task._next_main_recovery_time, 4.5)

    def test_observation_does_not_send_escape(self):
        task = _Task(False)
        self.assertFalse(GameFlowMixin.is_main(task, esc=False))
        self.assertEqual(task.backs, 0)

    def test_cooldown_does_not_refresh_or_send_escape(self):
        task = _Task(False)
        task._next_main_recovery_time = 10
        self.assertFalse(GameFlowMixin.is_main(task, esc=True))
        self.assertEqual(task.backs, 0)
        self.assertEqual(task.frames, 1)


if __name__ == "__main__":
    unittest.main()
