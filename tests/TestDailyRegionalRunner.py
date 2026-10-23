import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock, patch

from ok.util.config import Config

from src.core.RegionConfig import (
    DEFAULT_REGION_CONFIG,
    REGION_CONFIG_MODE_KEY,
    REGION_SCOPE_AREAS_KEY,
    REGION_SCOPE_EXCLUDE,
    REGION_SCOPE_INCLUDE,
    REGION_SCOPE_MODE_KEY,
    _repair_region_segment,
    migrate_legacy_region_scope,
    resolve_region_areas,
)
from src.tasks.mixin.region_scope_mixin import RegionScopeMixin
from src.tasks.onetime.RegionalBuildTask import RegionalBuildTask


def _make_runner(
    config_options,
    buy_sell_result=True,
    went_friend_boat_result=False,
    after_buy_called=True,
    exchange_result=True,
    buy_staple_result=True,
    to_model_area_result=True,
):
    """构造一个带 mock 的 RegionalBuildTask 实例，用于地区建设分支测试。

    after_buy_called: buy_sell 是否真的触发 after_buy 回调
                      （为 False 时模拟地区未启用/未找到货物/缺买卖价跳过的场景）
    went_friend_boat_result: buy_sell 是否报告「已前往好友帝江号」。
    """
    runner = object.__new__(RegionalBuildTask)
    runner._buy_ran = False
    runner.config = SimpleNamespace(get=lambda key, default=None: config_options)
    runner.exchange_outpost_goods = Mock(return_value=exchange_result)
    runner.buy_staple_goods = Mock(return_value=buy_staple_result)
    runner.to_model_area = Mock(return_value=to_model_area_result)
    runner.safe_back = Mock()
    runner.ensure_main = Mock()
    runner.log_info = Mock()
    runner.tr = lambda message, **kwargs: message

    def buy_sell_impl(target_areas=None, keep_area_context=False, after_buy=None):
        if after_buy is not None and after_buy_called and target_areas:
            after_buy(target_areas[0])
        return buy_sell_result, went_friend_boat_result

    runner.buy_sell = Mock(side_effect=buy_sell_impl)
    return runner


class TestDailyRegionalRunner(unittest.TestCase):
    AREAS: ClassVar = ["武陵", "试验园区"]

    def test_buy_staple_goods_accepts_config_keys_before_navigation(self):
        runner = object.__new__(RegionalBuildTask)
        runner.config = {"购物白名单": []}
        runner.info_set = Mock()
        runner.log_info = Mock()

        self.assertTrue(runner.buy_staple_goods(target_areas=[]))
        runner.info_set.assert_called_once_with("current_task", "buy_staple_goods")

    def run_runner(self, runner):
        runner.get_region_areas = Mock(return_value=list(self.AREAS))
        with patch("src.tasks.onetime.RegionalBuildTask.areas_list", self.AREAS):
            return runner.run_regional()

    # ── 仅「买物资」──
    def test_buy_only_navigates_to_material_dispatch_before_buy(self):
        runner = _make_runner(["买物资"])
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 必须先进入物资调度再购买
        self.assertEqual(
            runner.to_model_area.call_args_list[0].args,
            ("武陵", "物资调度"),
        )
        # 每个地区都执行了购买
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        # 仅买物资时不应触发买卖货
        runner.buy_sell.assert_not_called()

    # ── 买物资 + 买卖货，回调正常触发 ──
    def test_buy_with_trade_uses_after_buy_callback(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # buy_sell 的 after_buy 回调触发了 buy_staple_goods
        self.assertEqual(runner.buy_sell.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        # 回调已执行，不重复补充
        runner.to_model_area.assert_not_called()

    # ── 买物资 + 买卖货，回调被跳过 → 补充执行 ──
    def test_buy_retried_when_after_buy_callback_skipped(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 回调被跳过，应补充进入物资调度执行买物资
        self.assertEqual(runner.buy_sell.call_count, 2)
        self.assertEqual(runner.to_model_area.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)

    # ── 买卖货失败 → 记录日志并继续下一地区 ──
    def test_trade_failure_logs_and_continues(self):
        runner = _make_runner(["买卖货"], buy_sell_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买卖货失败", logged)

    # ── 已前往好友帝江号 → 直接回主界面、跳过 safe_back、继续下一区域 ──
    def test_trade_went_friend_boat_ensure_main_skips_safe_back_and_continues(self):
        runner = _make_runner(["买卖货"], went_friend_boat_result=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        # 每个区域前往好友船后都直接 ensure_main，不再走 safe_back 逐层返回
        self.assertEqual(runner.ensure_main.call_count, 2)
        runner.safe_back.assert_not_called()
        # 继续处理下一区域
        self.assertEqual(runner.buy_sell.call_count, 2)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("已前往好友帝江号", logged)

    # ── 未前往好友船时仍走原 safe_back 返回路径，不调用 ensure_main ──
    def test_trade_not_went_friend_boat_keeps_safe_back_path(self):
        runner = _make_runner(["买卖货"], went_friend_boat_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        runner.ensure_main.assert_not_called()
        self.assertEqual(runner.safe_back.call_count, 2)

    # ── 据点兑换失败 → 记录日志并继续下一地区 ──
    def test_outpost_failure_logs_and_continues(self):
        runner = _make_runner(["据点兑换"], exchange_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("据点兑换失败", logged)

    # ── 仅买物资时无法进入物资调度 → 记录日志并继续 ──
    def test_buy_only_to_model_area_failure_logs_and_continues(self):
        runner = _make_runner(["买物资"], to_model_area_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("无法进入", logged)

    # ── 仅买物资时购买失败 → 记录日志并继续 ──
    def test_buy_only_buy_staple_failure_logs_and_continues(self):
        runner = _make_runner(["买物资"], buy_staple_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买物资失败", logged)

    # ── 买物资 + 买卖货，回调被跳过且补充购买失败 → 记录日志并继续 ──
    def test_fallback_buy_failure_logs_and_continues(self):
        runner = _make_runner(["买物资", "买卖货"], after_buy_called=False, buy_staple_result=False)
        result = self.run_runner(runner)

        self.assertTrue(result)
        logged = " ".join(call.args[0] for call in runner.log_info.call_args_list)
        self.assertIn("买物资失败", logged)

    # ── 买物资 + 买卖货 + 据点兑换：回调正常，不重复补充 ──
    def test_all_options_callback_not_duplicated(self):
        runner = _make_runner(["据点兑换", "买物资", "买卖货"], after_buy_called=True)
        result = self.run_runner(runner)

        self.assertTrue(result)
        self.assertEqual(runner.exchange_outpost_goods.call_count, 2)
        self.assertEqual(runner.buy_staple_goods.call_count, 2)
        runner.to_model_area.assert_not_called()


class TestRegionScopeConfig(unittest.TestCase):
    def test_exclude_mode_keeps_future_regions_enabled(self):
        self.assertEqual(
            resolve_region_areas(
                REGION_SCOPE_EXCLUDE,
                ["四号谷地"],
                ["武陵", "四号谷地", "新地区"],
            ),
            ["武陵", "新地区"],
        )

    def test_include_mode_only_runs_selected_regions(self):
        self.assertEqual(
            resolve_region_areas(
                REGION_SCOPE_INCLUDE,
                ["武陵"],
                ["武陵", "四号谷地", "新地区"],
            ),
            ["武陵"],
        )

    def test_unmigrated_legacy_trade_flags_do_not_create_region_exclusion(self):
        segment = {"武陵": True, "四号谷地": False}
        original = dict(segment)
        self.assertFalse(_repair_region_segment(segment, original, ["武陵", "四号谷地"]))
        self.assertEqual(segment, original)

    def test_existing_scope_and_trade_flags_are_preserved(self):
        segment = {
            "武陵": True,
            "四号谷地": False,
            REGION_CONFIG_MODE_KEY: True,
            REGION_SCOPE_MODE_KEY: REGION_SCOPE_INCLUDE,
            REGION_SCOPE_AREAS_KEY: ["武陵"],
        }
        original = dict(segment)
        self.assertFalse(_repair_region_segment(segment, original, ["武陵", "四号谷地"]))
        self.assertEqual(segment, original)

    def test_all_true_legacy_flags_do_not_pin_current_region_set(self):
        segment = {"武陵": True, "四号谷地": True}
        self.assertFalse(_repair_region_segment(segment, dict(segment), ["武陵", "四号谷地"]))
        self.assertNotIn(REGION_CONFIG_MODE_KEY, segment)
        self.assertNotIn(REGION_SCOPE_AREAS_KEY, segment)

    def test_migration_failure_does_not_block_config_load(self):
        scope = object.__new__(RegionScopeMixin)
        scope.migrate_legacy_region_bools = True
        scope.log_warning = Mock()

        with (
            patch(
                "src.tasks.mixin.region_scope_mixin.migrate_legacy_region_scope",
                side_effect=OSError("disk full"),
            ),
            patch("src.core.BaseEfTask.BaseEfTask.load_config", return_value="loaded") as base_load,
        ):
            self.assertEqual(RegionScopeMixin.load_config(scope), "loaded")

        base_load.assert_called_once_with()
        scope.log_warning.assert_called_once()
        self.assertIn("旧地区配置迁移失败", scope.log_warning.call_args.args[0])

    def test_repair_preserves_scope_or_trade_changes_after_v1(self):
        original = {"武陵": True, "四号谷地": False}
        generated = {
            "武陵": True,
            "四号谷地": True,
            REGION_CONFIG_MODE_KEY: True,
            REGION_SCOPE_MODE_KEY: REGION_SCOPE_EXCLUDE,
            REGION_SCOPE_AREAS_KEY: ["四号谷地"],
        }
        for changes in (
            {REGION_CONFIG_MODE_KEY: False},
            {REGION_SCOPE_MODE_KEY: REGION_SCOPE_INCLUDE},
            {REGION_SCOPE_AREAS_KEY: ["武陵"]},
            {"四号谷地": False},
        ):
            with self.subTest(changes=changes):
                segment = {**generated, **changes}
                expected = dict(segment)
                self.assertFalse(_repair_region_segment(segment, original, ["武陵", "四号谷地"]))
                self.assertEqual(segment, expected)

    def test_persisted_repair_updates_task_and_account_and_survives_framework_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_file = root / "RegionalBuildTask.json"
            account_file = root / "account_scoped_overrides.json"
            original = {"武陵": True, "四号谷地": False}
            generated = {
                "武陵": True,
                "四号谷地": True,
                REGION_CONFIG_MODE_KEY: True,
                REGION_SCOPE_MODE_KEY: REGION_SCOPE_EXCLUDE,
                REGION_SCOPE_AREAS_KEY: ["四号谷地"],
            }
            backup_dir = root / "region_scope_migration_backup" / "v1"
            backup_dir.mkdir(parents=True)
            (backup_dir / task_file.name).write_text(json.dumps(original), encoding="utf-8")
            (backup_dir / account_file.name).write_text(
                json.dumps(
                    {
                        "accounts": {
                            "acc-a": {"RegionalBuildTask": original},
                            "legacy-user": {"RegionalBuildTask": original},
                            "acc-c": {"RegionalBuildTask": original},
                        }
                    }
                ),
                encoding="utf-8",
            )
            task_file.write_text(
                json.dumps(generated, ensure_ascii=False),
                encoding="utf-8",
            )
            account_file.write_text(
                json.dumps(
                    {
                        "accounts": {
                            "acc-a": {
                                "RegionalBuildTask": generated,
                            },
                            "acc-b": {"RegionalBuildTask": generated},
                            "acc-c": {"RegionalBuildTask": {**generated, REGION_SCOPE_AREAS_KEY: ["武陵"]}},
                        },
                        "account_registry": {
                            "acc-a": {"username": "test-account", "aliases": ["test-account"]},
                            "acc-b": {"username": "legacy-user", "aliases": ["legacy-user"]},
                            "acc-c": {"username": "edited-user", "aliases": ["edited-user"]},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_config_path(*parts):
                return str(root.joinpath(*parts))

            with (
                patch("src.core.RegionConfig.config_path", side_effect=fake_config_path),
                patch("src.tasks.account.account_scope_store.get_store_path", return_value=str(account_file)),
            ):
                with (
                    patch("src.core.RegionConfig._backup_file_once", side_effect=OSError("backup denied")),
                    self.assertRaisesRegex(OSError, "backup denied"),
                ):
                    migrate_legacy_region_scope("RegionalBuildTask", ["武陵", "四号谷地"])
                self.assertEqual(json.loads(task_file.read_text(encoding="utf-8")), generated)
                self.assertFalse(
                    (root / "region_scope_migration_backup" / "trade_only_v2" / "RegionalBuildTask.done.json").exists()
                )
                migrate_legacy_region_scope("RegionalBuildTask", ["武陵", "四号谷地"])
                first_task = json.loads(task_file.read_text(encoding="utf-8"))
                first_account = json.loads(account_file.read_text(encoding="utf-8"))
                migrate_legacy_region_scope("RegionalBuildTask", ["武陵", "四号谷地"])
                second_task = json.loads(task_file.read_text(encoding="utf-8"))
                second_account = json.loads(account_file.read_text(encoding="utf-8"))
                from src.tasks.account.account_scope_store import get_account_task_overrides

                self.assertEqual(get_account_task_overrides("acc-a", "RegionalBuildTask"), original)

            self.assertEqual(first_task, second_task)
            self.assertEqual(first_account, second_account)
            self.assertEqual(first_task, original)

            account_segment = first_account["accounts"]["acc-a"]["RegionalBuildTask"]
            self.assertEqual(account_segment, original)
            self.assertEqual(first_account["accounts"]["acc-b"]["RegionalBuildTask"], original)
            self.assertEqual(
                first_account["accounts"]["acc-c"]["RegionalBuildTask"], {**generated, REGION_SCOPE_AREAS_KEY: ["武陵"]}
            )

            repair_dir = root / "region_scope_migration_backup" / "trade_only_v2"
            self.assertEqual(json.loads((repair_dir / task_file.name).read_text(encoding="utf-8")), generated)
            self.assertTrue((repair_dir / account_file.name).is_file())
            self.assertTrue((repair_dir / "RegionalBuildTask.done.json").is_file())

            executor = SimpleNamespace(scene=None, pause_start=None)
            with patch.object(Config, "config_folder", tmp):
                task = RegionalBuildTask(executor, SimpleNamespace())
                loaded = Config("RegionalBuildTask", task.default_config)
                loaded.save_file()
                reloaded = Config("RegionalBuildTask", task.default_config)
            self.assertFalse(reloaded["四号谷地"])
            self.assertFalse(reloaded[REGION_CONFIG_MODE_KEY])
            self.assertEqual(reloaded[REGION_SCOPE_AREAS_KEY], [])

            # A later intentional choice identical to v1 must not be undone again.
            task_file.write_text(json.dumps(generated), encoding="utf-8")
            with patch("src.core.RegionConfig.config_path", side_effect=fake_config_path):
                migrate_legacy_region_scope("RegionalBuildTask", ["武陵", "四号谷地"])
            self.assertEqual(json.loads(task_file.read_text(encoding="utf-8")), generated)

    def test_no_backup_leaves_old_flags_and_explicit_scope_unchanged(self):
        for config in (
            {},
            {"武陵": True, "四号谷地": False},
            {
                "武陵": True,
                "四号谷地": True,
                REGION_CONFIG_MODE_KEY: True,
                REGION_SCOPE_MODE_KEY: REGION_SCOPE_EXCLUDE,
                REGION_SCOPE_AREAS_KEY: ["四号谷地"],
            },
        ):
            with self.subTest(config=config), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                task_file = root / "RegionalBuildTask.json"
                task_file.write_text(json.dumps(config), encoding="utf-8")
                with (
                    patch(
                        "src.core.RegionConfig.config_path",
                        side_effect=lambda *parts, root=root: str(root.joinpath(*parts)),
                    ),
                    patch(
                        "src.tasks.account.account_scope_store.get_store_path",
                        return_value=str(root / "account_scoped_overrides.json"),
                    ),
                    patch("src.tasks.account.account_scope_store.update_overrides") as update,
                ):
                    migrate_legacy_region_scope("RegionalBuildTask", ["武陵", "四号谷地"])
                self.assertEqual(json.loads(task_file.read_text(encoding="utf-8")), config)
                update.assert_not_called()

    def test_trade_switches_are_visible_under_trade_settings(self):
        executor = SimpleNamespace(scene=None, pause_start=None)
        task = RegionalBuildTask(executor, SimpleNamespace())
        trade_keys = task.config_type["⭐地区建设"]["sub_configs"]["买卖货"]
        for area in ("武陵", "四号谷地"):
            self.assertIn(area, trade_keys)
            self.assertFalse(task.config_type.get(area, {}).get("hidden", False))
            self.assertIs(task.default_config[area], True)

    def test_disabled_trade_does_not_skip_outpost_or_staple_purchase(self):
        runner = self._make_scoped_runner(["四号谷地"])
        runner.config = {
            "⭐地区建设": ["据点兑换", "买物资", "买卖货"],
            "四号谷地": False,
            REGION_CONFIG_MODE_KEY: False,
        }
        del runner.get_region_areas
        runner.buy_sell = RegionalBuildTask.buy_sell.__get__(runner)
        runner.to_model_area = Mock()
        with (
            patch("src.tasks.mixin.region_scope_mixin.get_region_config", return_value=DEFAULT_REGION_CONFIG),
            patch("src.tasks.onetime.RegionalBuildTask.areas_list", ["四号谷地"]),
        ):
            self.assertIn("四号谷地", runner.get_region_areas())
            self.assertTrue(runner.run_regional())
        runner.exchange_outpost_goods.assert_any_call(target_areas=["四号谷地"], keep_area_context=True)
        runner._buy_staple_in_area.assert_any_call("四号谷地")
        # Only the disabled area is exercised; any trade navigation is a regression.
        runner.to_model_area.assert_not_called()

    def _make_scoped_runner(self, scoped_areas):
        runner = object.__new__(RegionalBuildTask)
        runner.DEFAULT_OPTIONS = ["据点兑换", "买卖货"]
        runner._buy_ran = False
        runner.config = SimpleNamespace(
            get=lambda key, default=None: ["据点兑换", "买物资", "买卖货"] if key == "⭐地区建设" else default
        )
        runner.get_region_areas = Mock(return_value=list(scoped_areas))
        runner.exchange_outpost_goods = Mock(return_value=True)
        runner.buy_sell = Mock(return_value=(True, False))
        runner._buy_staple_in_area = Mock(return_value=True)
        runner.safe_back = Mock()
        runner.ensure_main = Mock()
        runner.log_info = Mock()
        runner.tr = lambda message, **kwargs: message
        return runner

    def test_scoped_runner_applies_same_area_to_all_regional_operations(self):
        runner = self._make_scoped_runner(["武陵"])

        self.assertTrue(runner.run_regional())
        runner.exchange_outpost_goods.assert_called_once_with(
            target_areas=["武陵"],
            keep_area_context=True,
        )
        self.assertEqual(runner.buy_sell.call_count, 1)
        self.assertEqual(runner.buy_sell.call_args.kwargs["target_areas"], ["武陵"])
        runner._buy_staple_in_area.assert_called_once_with("武陵")

    def test_empty_scope_is_clean_noop(self):
        runner = self._make_scoped_runner([])

        self.assertTrue(runner.run_regional())
        runner.exchange_outpost_goods.assert_not_called()
        runner.buy_sell.assert_not_called()
        runner._buy_staple_in_area.assert_not_called()
        runner.safe_back.assert_not_called()


if __name__ == "__main__":
    unittest.main()
