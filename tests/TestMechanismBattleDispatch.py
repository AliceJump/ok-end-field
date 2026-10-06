"""Runtime search must remain bounded and dispatch only evidenced legal actions."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.combat_catalog import CombatCatalog
from src.data.combat_observation import EnemyPresence
from src.data.combat_runtime import CombatRuntime
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatSearchLimit,
    CombatWorldState,
    EffectRequirement,
    plan_action_sequence,
)
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.effects import EffectType
from src.data.skill_types import CombatResourceType, ResourceChangeKind, SkillEffect, SkillResourceChange


class TestMechanismBattleDispatch(unittest.TestCase):
    def setUp(self):
        # Import fixture functions, not their unittest classes, into discovery.
        from tests.TestSkillTiming import FakeTask, logic_for

        self.task = FakeTask()
        self.logic = logic_for(self.task)
        self.logic.last_enemy_presence = EnemyPresence.PRESENT
        self.world = CombatWorldState(("1", "2"), sp=100, regen=0)
        for state in self.world.characters.values():
            state.panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        key1 = self.logic.store.profiles(self.logic.team[0], "battle")[0].skill_id
        key2 = self.logic.store.profiles(self.logic.team[1], "battle")[0].skill_id
        self.producer = ActionProgram(key1, "1", "battle", 100, 1, 0, (
            CombatEvent(.5, "producer", effects=(SkillEffect(EffectType.STACK_SHRED, count=3, target="enemy"),),
                        resources=(SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.FIXED, amount=50),),
                        hit=DamageHit("1", "target", "物理", .1)),
        ))
        self.consumer = ActionProgram(key2, "2", "battle", 50, 1, 0, (
            CombatEvent(.5, "consumer", hit=DamageHit("2", "target", "物理", 10)),
        ), requires=(EffectRequirement(EffectType.STACK_SHRED, 3),))
        self.catalog = CombatCatalog(self.world, {("1", "battle"): (self.producer,), ("2", "battle"): (self.consumer,)}, ())
        self.runtime = CombatRuntime(self.catalog, epoch=0)
        self.runtime.observe_sp(100, 0)
        self.logic.combat_runtime = self.runtime

    def test_search_realizes_unlock_and_does_not_mutate_live_state(self):
        before = self.world.snapshot()
        with patch("src.data.combat_runtime.plan_action_sequence", wraps=plan_action_sequence) as search:
            result = self.runtime.recommend_battle(0)
        self.assertEqual(result.program, self.producer)
        self.assertEqual(result.plan.actions, (self.producer.key, self.consumer.key))
        self.assertEqual(result.plan.damage, 1010)
        self.assertEqual(self.world.snapshot(), before)
        self.assertIsNone(self.runtime.pending)
        self.assertEqual(search.call_args.kwargs["max_expansions"], 144)
        self.assertEqual(search.call_args.kwargs["timeout"], .025)

    def test_selected_action_passes_existing_dispatch_and_guard_rejection_falls_back(self):
        self.logic.order = ["2", "1"]
        with patch.object(self.logic, "_try_battle_token", return_value=True) as dispatch:
            self.assertTrue(self.logic._try_planned_battle_skill(100))
        dispatch.assert_called_once_with("1", 100, overflow=False, advance_cursor=False)
        self.assertTrue(any("机制选择" in text for text in self.task.messages))
        self.task.now = .5
        self.runtime.observe_sp(100, .5)
        with patch.object(self.logic, "_try_battle_token", side_effect=[False, True]) as dispatch:
            self.assertTrue(self.logic._try_planned_battle_skill(100))
        self.assertEqual([call.args[0] for call in dispatch.call_args_list], ["1", "2"])
        self.assertTrue(any("existing_cast_guard" in text for text in self.task.messages))

    def test_real_dispatch_stages_then_confirms_once_on_existing_sp_evidence(self):
        self.assertTrue(self.logic._try_mechanism_battle(100))
        self.assertEqual(self.task.keys, ["1"])
        self.assertIsNotNone(self.runtime.pending)
        self.assertEqual(self.world.sp, 100)
        self.task.now = .2
        self.task.sp = 0
        self.logic._confirm_battle(.2)
        self.assertIsNone(self.runtime.pending)
        self.assertEqual(self.world.sp, 0)
        self.runtime.advance(.5)
        self.assertEqual(self.world.sp, 50)
        self.assertEqual(self.world.enemies["target"].shred_stacks, 3)
        self.assertFalse(self.runtime.confirm("1", "battle", .5))

    def test_gaps_stale_resource_and_incomplete_team_never_choose(self):
        self.runtime.catalog = replace(self.catalog, diagnostics=("Missing producer",))
        self.assertEqual(self.runtime.recommend_battle(0).reason, "unresolved_model")
        self.runtime.catalog = self.catalog
        self.world.unresolved.add("Unknown damage")
        self.assertEqual(self.runtime.recommend_battle(0).reason, "unresolved_model")
        self.world.unresolved.clear()
        self.assertEqual(self.runtime.recommend_battle(.6).reason, "stale_sp_observation")
        self.runtime.observe_sp(100, .6)
        self.world.characters["2"].panel = None
        self.assertEqual(self.runtime.recommend_battle(.6).reason, "incomplete_team")
        self.world.characters["2"].panel = self.world.characters["1"].panel
        unknown = replace(self.consumer, events=(CombatEvent(0, "unknown", unresolved=("Missing processor",)),))
        self.runtime.catalog = replace(self.catalog, programs={("1", "battle"): (self.producer,), ("2", "battle"): (unknown,)})
        self.assertEqual(self.runtime.recommend_battle(.6).reason, "unresolved_action")
        self.assertEqual(self.task.keys, [])

    def test_pending_blocks_but_observation_gaps_are_diagnostic_only(self):
        self.runtime.stage(self.producer, 0)
        self.assertEqual(self.runtime.recommend_battle(0).reason, "cast_pending")
        self.runtime.cancel()
        self.runtime.note_observation_gap("Link actor/phase not observed")
        self.assertEqual(self.runtime.recommend_battle(0).program, self.producer)
        self.world.start(self.producer, action_id="accepted")
        self.task.probe_enemy_presence = lambda: EnemyPresence.ABSENT
        self.assertTrue(self.logic._enemy_operation_paused())
        self.task.probe_enemy_presence = lambda: EnemyPresence.PRESENT
        self.assertFalse(self.logic._enemy_operation_paused())
        self.assertNotEqual(self.runtime.recommend_battle(0).reason, "unobserved_action_or_target")

    def test_unknown_presence_does_not_block_mechanism_and_throttle_still_applies(self):
        self.logic.last_enemy_presence = EnemyPresence.UNKNOWN
        with patch.object(self.logic, "_try_battle_token", return_value=True) as dispatch:
            self.assertTrue(self.logic._try_mechanism_battle(100))
            self.assertFalse(self.logic._try_mechanism_battle(100))
        dispatch.assert_called_once_with("1", 100, overflow=False, advance_cursor=True)
        self.assertFalse(any("enemy_not_confirmed" in text for text in self.task.messages))

        self.task.now = .5
        self.runtime.observe_sp(100, .5)
        with patch.object(self.logic, "_battle_context", return_value=((), None, None)), patch.object(self.logic, "_try_battle_token") as dispatch:
            self.assertFalse(self.logic._try_mechanism_battle(100))
        dispatch.assert_not_called()
        self.assertTrue(any("button_phase_mismatch" in text for text in self.task.messages))

    def test_waiting_first_action_and_budget_exhaustion_do_not_win(self):
        self.world.cooldowns[self.producer.key] = 1
        self.assertEqual(self.runtime.recommend_battle(0).reason, "first_action_requires_wait")
        self.world.cooldowns.clear()
        with patch("src.data.combat_runtime.plan_action_sequence", side_effect=CombatSearchLimit("timeout")):
            self.assertEqual(self.runtime.recommend_battle(0).reason, "search_budget")
        with self.assertRaises(CombatSearchLimit):
            plan_action_sequence(self.world, (self.producer, self.consumer), depth=2, max_expansions=1)
        with patch("src.data.combat_simulation.time.monotonic", side_effect=[0, 1]):
            with self.assertRaises(CombatSearchLimit):
                plan_action_sequence(self.world, (self.producer,), timeout=.025)
        with self.assertRaises(ValueError):
            plan_action_sequence(self.world, (self.producer,), timeout=float("nan"))

    def test_actual_link_dispatch_marks_history_without_disabling_search(self):
        self.task.link = True
        with patch.object(self.logic, "_allowed", return_value=True):
            self.logic.step()
        self.assertEqual(self.task.keys, ["e"])
        self.assertIn("Link actor/phase not observed", self.runtime.observation_gaps)
        self.assertEqual(self.world.enemies["target"].shred_stacks, 0)
        self.assertEqual(self.world.damage, 0)
        self.assertNotEqual(self.runtime.recommend_battle(self.task.now).reason, "unobserved_action_or_target")

    def test_real_native_team_retains_legacy_dispatch_when_model_is_incomplete(self):
        from src.data.combat_catalog import build_combat_catalog

        catalog = build_combat_catalog(("弭弗", "骏卫", "余烬"), self.logic.store)
        runtime = CombatRuntime(catalog, epoch=0)
        runtime.observe_sp(200, 0)
        self.logic.combat_runtime = runtime
        with patch.object(self.logic, "_try_battle_token", return_value=True) as dispatch:
            self.assertTrue(self.logic._try_planned_battle_skill(200))
        dispatch.assert_called_once_with("1", 200, overflow=False, advance_cursor=True)
        self.assertTrue(any("unresolved_model" in text for text in self.task.messages))
        self.assertEqual(runtime.world.sp, 200)
        self.assertIsNone(runtime.pending)


if __name__ == "__main__":
    unittest.main()
