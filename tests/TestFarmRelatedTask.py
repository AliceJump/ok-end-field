import unittest
from unittest.mock import Mock, patch

from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING
from src.core.global_config_store import KEY_CONFIG_NAME
from src.tasks.onetime.FarmRelatedTask import FarmRelatedTask
from tests.TestAccountKeyConfig import BASE_KEYS, _PressTask


class TestFarmRelatedTask(unittest.TestCase):
    def make_task(self):
        task = Mock()
        battle = Mock()
        task.current_user = "current user"
        task.current_account_id = "current account"
        task._is_account_override_enabled.return_value = True
        battle.running = False
        battle.current_user = "old user"
        battle.current_account_id = "old account"
        battle._daily_host_account_overrides_enabled = False

        def bind_account(user, account_id):
            battle.current_user = user
            battle.current_account_id = account_id

        battle.set_current_account.side_effect = bind_account
        old_context = object()
        battle.battle_ctx = old_context
        task.get_task_by_class.return_value = battle
        task._click_farm_related.return_value = True
        task.wait_click_feature.return_value = object()
        task.wait_feature.return_value = object()
        task._collect_to_wuling_warehouse.return_value = True
        return task, battle, old_context

    def test_wuling_farm_reuses_stage_and_collects_direct_details(self):
        task, battle, old_context = self.make_task()

        def locate():
            self.assertEqual(battle.battle_ctx.stage_name, "武陵城")
            self.assertEqual(battle.battle_ctx.category_name, STAGE_CATEGORY_ENERGY_POOLING)
            self.assertTrue(battle.running)
            self.assertEqual(battle.current_account_id, "current account")
            self.assertEqual(battle.current_user, "current user")
            self.assertTrue(battle._daily_host_account_overrides_enabled)
            return True

        battle.to_stage.side_effect = locate
        self.assertTrue(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertIs(battle.battle_ctx, old_context)
        self.assertFalse(battle.running)
        self.assertEqual(battle.current_account_id, "old account")
        self.assertEqual(battle.current_user, "old user")
        self.assertFalse(battle._daily_host_account_overrides_enabled)
        battle._open_index.assert_called_once()
        task._click_farm_related.assert_called_once()
        task.click_relative.assert_called_once_with(0.966, 0.565)
        task.wait_ui_stable.assert_called_once()
        task._collect_to_wuling_warehouse.assert_called_once_with("武陵城自动农植机")

    def test_existing_painting_collection_runs_before_wuling(self):
        task = Mock()
        task.config = {"祀的画": True, "武陵城": True}
        self.assertTrue(FarmRelatedTask.run.__wrapped__(task))
        task._collect_painting.assert_called_once()
        task._collect_wuling_farmer.assert_called_once()

    def test_region_switches(self):
        for painting, wuling in ((True, False), (False, True), (False, False)):
            with self.subTest(painting=painting, wuling=wuling):
                task = Mock()
                task.config = {"祀的画": painting, "武陵城": wuling}
                self.assertTrue(FarmRelatedTask.run.__wrapped__(task))
                self.assertEqual(task._collect_painting.call_count, int(painting))
                self.assertEqual(task._collect_wuling_farmer.call_count, int(wuling))

    def test_painting_failure_stops_wuling(self):
        task = Mock()
        task.config = {"祀的画": True, "武陵城": True}
        task._collect_painting.return_value = False
        self.assertFalse(FarmRelatedTask.run.__wrapped__(task))
        task._collect_wuling_farmer.assert_not_called()

    def test_stage_failure_restores_context_and_never_collects(self):
        task, battle, old_context = self.make_task()
        battle.to_stage.return_value = False
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertIs(battle.battle_ctx, old_context)
        self.assertEqual(battle.current_account_id, "old account")
        self.assertFalse(battle._daily_host_account_overrides_enabled)
        task._collect_to_wuling_warehouse.assert_not_called()

    def test_stage_exception_restores_context(self):
        task, battle, old_context = self.make_task()
        battle.to_stage.side_effect = RuntimeError("index error")
        with self.assertRaisesRegex(RuntimeError, "index error"):
            FarmRelatedTask._collect_wuling_farmer(task)
        self.assertIs(battle.battle_ctx, old_context)
        self.assertFalse(battle.running)
        self.assertEqual(battle.current_account_id, "old account")
        self.assertFalse(battle._daily_host_account_overrides_enabled)

    @patch("src.core.base_mixin.account_override_mixin.get_account_task_overrides")
    @patch("src.core.base_mixin.runtime_mixin.get_global_config", return_value=BASE_KEYS)
    def test_wuling_index_uses_current_account_key(self, _get_config, get_overrides):
        task, _, _ = self.make_task()
        battle = _PressTask(running=False, account_id="old account", account_name="old user")
        battle.battle_ctx = None
        def bind_account(user, account_id):
            battle.current_user = user
            battle.current_account_id = account_id

        battle.set_current_account = bind_account
        battle._open_index = lambda: battle.press_key("f8")
        battle.to_stage = Mock(return_value=True)
        task.get_task_by_class.return_value = battle
        get_overrides.return_value = {"Handbook Key": "f6"}

        self.assertTrue(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertEqual(battle.sent_keys, ["f6"])
        get_overrides.assert_called_with("current account", KEY_CONFIG_NAME, account_name="current user")
        self.assertEqual(battle.current_account_id, "old account")
        self.assertFalse(battle.running)

    def test_missing_map_icon_stops_without_collecting(self):
        task, battle, old_context = self.make_task()
        task.wait_click_feature.return_value = None
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        task.click_relative.assert_called_once_with(0.966, 0.565)
        task._collect_to_wuling_warehouse.assert_not_called()
        self.assertIs(battle.battle_ctx, old_context)

    def test_missing_farm_detail_stops_without_collecting(self):
        task, _, _ = self.make_task()
        task.wait_feature.side_effect = [object(), None]
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        task._collect_to_wuling_warehouse.assert_not_called()
