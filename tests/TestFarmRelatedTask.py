import unittest
from unittest.mock import Mock

from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING
from src.tasks.onetime.FarmRelatedTask import FarmRelatedTask


class TestFarmRelatedTask(unittest.TestCase):
    def make_task(self):
        task = Mock()
        battle = Mock()
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
            return True

        battle.to_stage.side_effect = locate
        self.assertTrue(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertIs(battle.battle_ctx, old_context)
        battle._open_index.assert_called_once()
        task._click_farm_related.assert_called_once()
        self.assertEqual(task.scroll_relative.call_count, 3)
        task.scroll_relative.assert_called_with(0.5, 0.5, -5)
        self.assertEqual(task.wait_ui_stable.call_count, 3)
        task._collect_to_wuling_warehouse.assert_called_once_with("武陵城自动农植机")

    def test_existing_painting_collection_runs_before_wuling(self):
        task = Mock()
        task.WULING_TAB_BOX = FarmRelatedTask.WULING_TAB_BOX
        self.assertTrue(FarmRelatedTask.run.__wrapped__(task))
        self.assertEqual(task._collect_to_wuling_warehouse.call_count, 2)
        task._collect_to_wuling_warehouse.assert_any_call("牧场", max_batches=1)
        task._collect_to_wuling_warehouse.assert_any_call("自动农植机")
        task._collect_wuling_farmer.assert_called_once()

    def test_stage_failure_restores_context_and_never_collects(self):
        task, battle, old_context = self.make_task()
        battle.to_stage.return_value = False
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertIs(battle.battle_ctx, old_context)
        task._collect_to_wuling_warehouse.assert_not_called()

    def test_stage_exception_restores_context(self):
        task, battle, old_context = self.make_task()
        battle.to_stage.side_effect = RuntimeError("index error")
        with self.assertRaisesRegex(RuntimeError, "index error"):
            FarmRelatedTask._collect_wuling_farmer(task)
        self.assertIs(battle.battle_ctx, old_context)

    def test_missing_map_icon_stops_without_collecting(self):
        task, battle, old_context = self.make_task()
        task.wait_click_feature.return_value = None
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        self.assertEqual(task.scroll_relative.call_count, 3)
        task._collect_to_wuling_warehouse.assert_not_called()
        self.assertIs(battle.battle_ctx, old_context)

    def test_missing_farm_detail_stops_without_collecting(self):
        task, _, _ = self.make_task()
        task.wait_feature.side_effect = [object(), None]
        self.assertFalse(FarmRelatedTask._collect_wuling_farmer(task))
        task._collect_to_wuling_warehouse.assert_not_called()
