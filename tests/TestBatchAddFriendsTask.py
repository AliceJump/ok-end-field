import ast
import unittest
from pathlib import Path

from ok.feature.Box import Box

from src.tasks.onetime.BatchAddFriendsTask import BatchAddFriendsTask

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _AddHarness(BatchAddFriendsTask):
    """只验证申请计数，设备与 OCR 全部用最小替身。"""

    screen_width = 1280
    screen_height = 720
    frame = None

    def __init__(self, full_flags=(), buttons=0):
        self.config = {
            BatchAddFriendsTask.CFG_MAX_COUNT: 20,
            BatchAddFriendsTask.CFG_BATCH_LIMIT: 10,
        }
        self.logs = []
        self.clicks = 0
        self._full_flags = list(full_flags)
        self._buttons = buttons

    def tr(self, text):
        return text

    def exit_is_set(self):
        return False

    def log_info(self, message, *args, **kwargs):
        self.logs.append(("info", message))

    def log_warning(self, message, *args, **kwargs):
        self.logs.append(("warning", message))

    def log_error(self, message, *args, **kwargs):
        self.logs.append(("error", message))

    def friend_list_full(self):
        return self._full_flags.pop(0) if self._full_flags else False

    def find_add_button(self):
        if self._buttons <= 0:
            return None
        self._buttons -= 1
        return Box(1200, 200, 40, 20)

    def click_box(self, box, *args, **kwargs):
        self.clicks += 1

    def change_batch(self):
        return False

    def list_at_bottom(self):
        return True

    def scroll_list(self):
        pass


class TestAddStrangersCounting(unittest.TestCase):
    def test_failed_request_when_list_is_full_is_not_counted(self):
        # 循环开头满员检查为 False，点击后才出现满员提示
        task = _AddHarness(full_flags=(False, True), buttons=1)
        task.add_strangers()
        self.assertEqual(task.clicks, 1)
        messages = [message for _, message in task.logs]
        self.assertFalse(any("已发送好友申请" in message for message in messages))
        self.assertTrue(any("本次申请未发出" in message for message in messages))
        self.assertTrue(any("没有发送任何好友申请" in message for message in messages))

    def test_full_list_at_loop_start_sends_nothing(self):
        task = _AddHarness(full_flags=(True,), buttons=1)
        task.add_strangers()
        self.assertEqual(task.clicks, 0)
        self.assertTrue(any("好友列表已满" in message for _, message in task.logs))

    def test_successful_requests_are_counted(self):
        task = _AddHarness(full_flags=(), buttons=2)
        task.add_strangers()
        self.assertEqual(task.clicks, 2)
        messages = [message for _, message in task.logs]
        self.assertTrue(any("已发送好友申请 2/20" in message for message in messages))
        self.assertTrue(any("共发送 2 个好友申请" in message for message in messages))


class TestTaskRegistration(unittest.TestCase):
    def test_batch_friend_tasks_are_appended_last(self):
        """新任务必须追加在 onetime_tasks 末尾：`-t` 是按 1 起算的列表序号。"""
        config_text = (_REPO_ROOT / "src" / "config.py").read_text(encoding="utf-8")
        tasks = None
        for node in ast.walk(ast.parse(config_text)):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "onetime_tasks":
                    tasks = [ast.literal_eval(item) for item in value.elts]
        self.assertIsNotNone(tasks)
        self.assertEqual(
            tasks[-2:],
            [
                ["src.tasks.onetime.BatchAddFriendsTask", "BatchAddFriendsTask"],
                ["src.tasks.onetime.BatchRemarkFriendsTask", "BatchRemarkFriendsTask"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
