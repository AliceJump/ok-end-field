import unittest
from unittest.mock import patch

from src.core.base_mixin.account_override_mixin import AccountOverrideMixin


class _Config(dict):
    pass


class _Task(AccountOverrideMixin):
    def __init__(self):
        self.config = _Config({"配置选择": "默认"})
        self.current_account_id = "account-1"
        self.current_user = ""
        self.running = False
        self._bind_account_aware_config_get()


class TestAccountOverrideMixin(unittest.TestCase):
    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    def test_config_get_uses_overrides_only_while_task_runs(self, get_overrides):
        get_overrides.return_value = {"配置选择": "账号覆盖"}
        task = _Task()

        self.assertEqual(task.config.get("配置选择"), "默认")

        task.running = True
        self.assertEqual(task.config.get("配置选择"), "账号覆盖")

        task.running = False
        self.assertEqual(task.config.get("配置选择"), "默认")


if __name__ == "__main__":
    unittest.main()
