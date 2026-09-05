import types
import unittest
from unittest.mock import patch

from src.core.base_mixin.account_override_mixin import AccountOverrideMixin
from src.core.base_mixin.runtime_mixin import RuntimeMixin
from src.core.global_config_store import KEY_CONFIG_DEFAULTS, KEY_CONFIG_NAME


class _Task(AccountOverrideMixin):
    def __init__(self, running=True, independent=True, account_id="account-1", account_name="13800001111"):
        self.config = {"多账户独立配置": independent}
        self.running = running
        self.current_account_id = account_id
        self.current_user = account_name


class _PressTask(RuntimeMixin, AccountOverrideMixin):
    """只实现 press_key 依赖的最小接口，send_key 记录按键便于断言。"""

    def __init__(self, running=True, independent=True, account_id="account-1", account_name="13800001111"):
        self.sent_keys = []
        self.config = {"多账户独立配置": independent}
        self.running = running
        self.current_account_id = account_id
        self.current_user = account_name

    def send_key(self, key, **kwargs):
        self.sent_keys.append(key)
        return key


BASE_KEYS = dict(KEY_CONFIG_DEFAULTS)


class TestAccountOverrideFor(unittest.TestCase):
    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    def test_returns_override_only_while_task_runs(self, get_overrides):
        get_overrides.return_value = {"Dodge Key": "capslock"}
        task = _Task(running=False)

        self.assertEqual(task._account_override_for(KEY_CONFIG_NAME), {})

        task.running = True
        self.assertEqual(task._account_override_for(KEY_CONFIG_NAME), get_overrides.return_value)

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    def test_respects_independent_config_switch(self, get_overrides):
        get_overrides.return_value = {"a": "b"}

        task = _Task(running=True, independent=False)
        self.assertEqual(task._account_override_for(KEY_CONFIG_NAME), {})
        get_overrides.assert_not_called()

        task.config["多账户独立配置"] = True
        self.assertEqual(task._account_override_for(KEY_CONFIG_NAME), {"a": "b"})

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    def test_requires_account_context(self, get_overrides):
        task = _Task(running=True, account_id="", account_name="")
        self.assertEqual(task._account_override_for(KEY_CONFIG_NAME), {})
        get_overrides.assert_not_called()


class TestAccountKeyConfig(unittest.TestCase):
    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_press_key_uses_account_override(self, _get_config, get_overrides):
        get_overrides.return_value = {"Dodge Key": "capslock"}
        task = _PressTask()

        task.press_key("lshift")

        self.assertEqual(task.sent_keys, ["capslock"])

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_press_key_without_override_keeps_global_value(self, _get_config, get_overrides):
        get_overrides.return_value = {}
        task = _PressTask()

        task.press_key("lshift")
        task.press_key("space")

        self.assertEqual(task.sent_keys, ["lshift", "space"])

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_press_key_ignores_override_when_not_running(self, _get_config, get_overrides):
        get_overrides.return_value = {"Dodge Key": "capslock"}
        task = _PressTask(running=False)

        task.press_key("lshift")

        self.assertEqual(task.sent_keys, ["lshift"])
        get_overrides.assert_not_called()

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_press_key_unmapped_key_passes_through(self, _get_config, get_overrides):
        get_overrides.return_value = {"Dodge Key": "capslock"}
        task = _PressTask()

        # 移动键不在默认键位表中，即使存在账号覆盖也原样发送
        task.press_key("w")

        self.assertEqual(task.sent_keys, ["w"])

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_press_industry_and_combat_keys_use_account_override(self, _get_config, get_overrides):
        get_overrides.return_value = {"Industry Plan Key": "g", "Link Skill Key": "q"}
        task = _PressTask()

        task.press_industry_key("t")
        task.press_combat_key("e")

        self.assertEqual(task.sent_keys, ["g", "q"])

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_account_key_config_filters_unknown_keys(self, _get_config, get_overrides):
        get_overrides.return_value = {"不存在键": "zz", "Dodge Key": "capslock"}
        task = _PressTask()

        effective = task._account_key_config()

        self.assertNotIn("不存在键", effective)
        self.assertEqual(effective["Dodge Key"], "capslock")

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_account_key_config_without_override_returns_base(self, _get_config, get_overrides):
        get_overrides.return_value = {}
        task = _PressTask()

        self.assertIs(task._account_key_config(), BASE_KEYS)


class TestGlobalKeyConfigProxy(unittest.TestCase):
    def test_proxy_exposes_hotkey_schema_for_account_editor(self):
        from src.gui.AccountConfigTab import GlobalKeyConfigProxy

        self.assertEqual(GlobalKeyConfigProxy.account_override_name, KEY_CONFIG_NAME)
        self.assertEqual(GlobalKeyConfigProxy.name, "键位配置")
        self.assertTrue(GlobalKeyConfigProxy.support_multi_account)
        self.assertEqual(GlobalKeyConfigProxy.default_config, KEY_CONFIG_DEFAULTS)

    def test_collect_tasks_includes_global_proxies(self):
        from src.gui.AccountConfigTab import AccountConfigTab, GlobalKeyConfigProxy, GlobalZipLineConfigProxy

        class _Executor:
            def __init__(self):
                self.onetime_tasks = []
                self.trigger_tasks = []

        stub = types.SimpleNamespace(executor=_Executor())
        tasks = AccountConfigTab._collect_tasks(stub)

        self.assertIn(GlobalZipLineConfigProxy, tasks)
        self.assertIn(GlobalKeyConfigProxy, tasks)


if __name__ == "__main__":
    unittest.main()
