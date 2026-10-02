import unittest
from unittest.mock import patch

from src.core.base_mixin.account_override_mixin import AccountOverrideMixin
from src.gui.AccountConfigTab import AccountConfigTab


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
    def test_editor_and_runtime_coerce_saved_values_consistently(self):
        cases = [
            (False, " 开启 ", True),
            (True, "OFF", False),
            (True, "invalid", True),
            (False, 1, False),
            (5, " 12 ", 12),
            (5, "1.2", 5),
            (1.5, " 2.25 ", 2.25),
            (1.5, 2, 2.0),
            (1.5, "invalid", 1.5),
            (["original"], ["saved"], ["saved"]),
            (["original"], "saved", ["original"]),
            ("original", 12, "12"),
            (None, ["saved"], ["saved"]),
            ("original", None, None),
        ]
        for base, saved, expected in cases:
            with self.subTest(base=base, saved=saved):
                self.assertEqual(AccountOverrideMixin._coerce_override_value(base, saved), expected)
                self.assertEqual(AccountConfigTab._coerce_like(base, saved), expected)

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
