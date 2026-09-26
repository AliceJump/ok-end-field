"""DailyFeature 包装器的测试：实例解析、账号上下文注入与恢复、帝江号状态同步。

架构约定（与 ok-ap PR#30 同构，注入机制适配本仓库）：
- 子任务是注册进 onetime_tasks 的独立任务类（此处以 MailTask 为例）；
- DailyFeature 执行时从 executor 找子任务实例，注入 running=True 与宿主账号
  上下文（set_current_account 绑定账号覆盖查询），结束后恢复原状；
- 帝江号共享状态 _daily_boat_state_confirmed 由 runner 维护在宿主上，
  包装器在执行前后做 host↔impl 同步；
- 子任务未注册时记日志并把该项记为失败（返回 False）。
"""

import unittest
from unittest.mock import patch

from src.tasks.daily.daily_feature import DailyFeature
from src.tasks.onetime.MailTask import MailTask


def _make_impl():
    """构造绕过框架初始化的 MailTask 实例（只填测试所需的属性）。"""
    return object.__new__(MailTask)


class _FakeConfig(dict):
    """允许账号覆盖补丁绑定 get 方法的最小配置对象。"""


class _FakeExecutor:
    def __init__(self, tasks):
        self.onetime_tasks = tasks


class _FakeHost:
    """模拟 DailyTask 宿主：提供 executor、账号上下文与帝江号共享状态。"""

    def __init__(self, tasks, account_id="", account_name="", boat_state=False):
        self.executor = _FakeExecutor(tasks)
        self.current_account_id = account_id
        self.current_user = account_name
        self._daily_boat_state_confirmed = boat_state
        self.daily_runner = None
        self.logged = []

    def _account_override_for(self, config_name):
        return {}

    def log_info(self, message, *args, **kwargs):
        self.logged.append(str(message))

    def tr(self, message, **kwargs):
        return message


class TestDailyFeaturePlanItem(unittest.TestCase):
    def test_plan_item_returns_switch_key_and_callable(self):
        feature = DailyFeature(_FakeHost([_make_impl()]), MailTask, switch_key="⭐收邮件")
        key, func = feature.plan_item()
        self.assertEqual(key, "⭐收邮件")
        self.assertTrue(callable(func))

    def test_plan_item_with_predicate_returns_three_tuple(self):
        predicate = lambda: False  # noqa: E731
        feature = DailyFeature(
            _FakeHost([_make_impl()]), MailTask, switch_key="⭐收邮件", run_method="run_mail", predicate=predicate
        )
        item = feature.plan_item()
        self.assertEqual(len(item), 3)
        self.assertEqual(item[0], "⭐收邮件")
        self.assertIs(item[2], predicate)


class TestDailyFeatureRun(unittest.TestCase):
    def _feature_and_impl(self, tasks=None, account_id="", account_name="", boat_state=False, run_method="run_mail"):
        impl = _make_impl()
        captured = {}

        def fake_run():
            # 执行期间应看到注入的 running 与宿主账号上下文
            captured["running"] = getattr(impl, "running", None)
            captured["account_id"] = getattr(impl, "current_account_id", None)
            captured["user"] = getattr(impl, "current_user", None)
            captured["boat_state"] = getattr(impl, "_daily_boat_state_confirmed", None)
            return "done"

        setattr(impl, run_method, fake_run)
        host = _FakeHost(
            tasks if tasks is not None else [impl],
            account_id=account_id,
            account_name=account_name,
            boat_state=boat_state,
        )
        feature = DailyFeature(host, MailTask, switch_key="⭐收邮件", run_method=run_method)
        return feature, impl, captured, host

    def test_impl_config_reads_registered_impl(self):
        """impl_config 读已注册实例的配置；实例缺失回落 default。"""
        impl = _make_impl()
        impl.config = _FakeConfig({"⭐帝江号一键存放": True})
        host = _FakeHost([impl])
        feature = DailyFeature(host, MailTask, switch_key="⭐收邮件")
        self.assertTrue(feature.impl_config("⭐帝江号一键存放"))
        self.assertFalse(feature.impl_config("⭐简易制作", False))

        missing_host = _FakeHost([])
        missing_feature = DailyFeature(missing_host, MailTask, switch_key="⭐收邮件")
        self.assertIsNone(missing_feature.impl_config("⭐简易制作"))
        self.assertFalse(missing_feature.impl_config("⭐简易制作", False))

    def test_impl_config_uses_current_account_override(self):
        """日常谓词在运行前也须按宿主账号读取子任务覆盖。"""
        impl = _make_impl()
        impl.config = _FakeConfig({"⭐地区建设": ["据点兑换"]})
        impl.running = False
        impl.current_account_id = ""
        impl.current_user = ""
        host = _FakeHost([impl], account_id="acc_x", account_name="user")
        feature = DailyFeature(host, MailTask, switch_key="⭐地区建设")
        with patch(
            "src.core.base_mixin.account_override_mixin.get_account_task_overrides",
            return_value={"⭐地区建设": []},
        ):
            self.assertEqual(feature.impl_config("⭐地区建设"), [])
        self.assertFalse(impl.running)
        self.assertEqual(impl.current_account_id, "")
        self.assertEqual(impl.current_user, "")

    def test_host_account_override_switch_controls_child_config(self):
        """宿主关闭账号独立配置时，子任务谓词和执行都只读全局值。"""
        impl = _make_impl()
        impl.config = _FakeConfig({"参数": "全局值"})
        impl.run_mail = lambda: impl.config.get("参数")
        host = _FakeHost([impl], account_id="acc_x", account_name="user")
        feature = DailyFeature(host, MailTask, switch_key="⭐收邮件", run_method="run_mail")

        with patch(
            "src.core.base_mixin.account_override_mixin.get_account_task_overrides",
            return_value={"参数": "账号值"},
        ):
            host._is_account_override_enabled = lambda: False
            self.assertEqual(feature.impl_config("参数"), "全局值")
            self.assertEqual(feature.run(), "全局值")

            host._is_account_override_enabled = lambda: True
            self.assertEqual(feature.impl_config("参数"), "账号值")
            self.assertEqual(feature.run(), "账号值")

        self.assertFalse(hasattr(impl, "_daily_host_account_overrides_enabled"))

    def test_run_resolves_impl_and_injects_context(self):
        feature, _, captured, _host = self._feature_and_impl(account_id="acc_x", account_name="0705", boat_state=True)
        self.assertEqual(feature.run(), "done")
        self.assertTrue(captured["running"])
        self.assertEqual(captured["account_id"], "acc_x")
        self.assertEqual(captured["user"], "0705")
        # 宿主的帝江号共享状态在执行前同步给子任务
        self.assertTrue(captured["boat_state"])

    def test_run_restores_impl_state_afterwards(self):
        feature, impl, _, _host = self._feature_and_impl()
        feature.run()
        self.assertFalse(getattr(impl, "running", True))
        self.assertEqual(getattr(impl, "current_account_id", "x"), "")
        self.assertEqual(getattr(impl, "current_user", "x"), "")

    def test_run_restores_state_even_when_impl_raises(self):
        feature, impl, _, _host = self._feature_and_impl()

        def boom():
            raise RuntimeError("boom")

        impl.run_mail = boom
        with self.assertRaises(RuntimeError):
            feature.run()
        self.assertFalse(getattr(impl, "running", True))
        self.assertEqual(getattr(impl, "current_account_id", "x"), "")
        self.assertFalse(hasattr(impl, "_daily_host_account_overrides_enabled"))

    def test_run_returns_false_when_impl_missing(self):
        feature, _, _, host = self._feature_and_impl(tasks=[])
        self.assertFalse(feature.run())
        self.assertTrue(host.logged)  # 有日志说明走的是「未找到实例」分支

    def test_boat_state_reset_by_impl_syncs_back_to_host(self):
        """子任务内重置帝江号状态（如普通传送）后，宿主侧同步为 False。"""
        feature, impl, _, host = self._feature_and_impl(boat_state=True)

        def reset_boat_state():
            impl._daily_boat_state_confirmed = False
            return True

        impl.run_mail = reset_boat_state
        feature.run()
        self.assertFalse(host._daily_boat_state_confirmed)

    def test_boat_state_confirmed_by_impl_syncs_back_to_host(self):
        """子任务内确认帝江号状态（如整理任务传送成功）后，宿主侧同步为 True。"""
        feature, impl, _, host = self._feature_and_impl(boat_state=False)

        def confirm_boat_state():
            impl._daily_boat_state_confirmed = True
            return True

        impl.run_mail = confirm_boat_state
        feature.run()
        self.assertTrue(host._daily_boat_state_confirmed)
        self.assertFalse(hasattr(impl, "_daily_boat_state_confirmed"))

    def test_run_forwards_failure_details_to_daily_runner(self):
        """子任务的 mark_task_failure 应进入日常汇总并在结束后撤销注入。"""
        feature, impl, _, host = self._feature_and_impl()

        class _Runner:
            def __init__(self):
                self.failures = []

            def get_current_task_name(self):
                return "⭐收邮件"

            def set_task_failure(self, message, task_name=None, screenshot_taken=False):
                self.failures.append((message, task_name, screenshot_taken))

        host.daily_runner = _Runner()
        impl.screenshot = lambda *_args, **_kwargs: None
        impl.run_mail = lambda: impl.mark_task_failure("收邮件失败")
        feature.run()

        self.assertEqual(host.daily_runner.failures, [("收邮件失败", None, True)])
        self.assertFalse(hasattr(impl, "daily_runner"))


if __name__ == "__main__":
    unittest.main()
