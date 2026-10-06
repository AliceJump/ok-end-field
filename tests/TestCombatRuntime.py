"""Accepted action evidence commits one fork; HUD observations replace predictions."""

import unittest

from src.data.combat_catalog import CombatCatalog
from src.data.combat_runtime import CombatRuntime
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.damage_resolution import FixedDamagePanel
from src.data.skill_types import CombatResourceType, ResourceChangeKind, SkillResourceChange


class TestCombatRuntime(unittest.TestCase):
    def setUp(self):
        world = CombatWorldState(("1", "2"), sp=200, regen=0)
        self.runtime = CombatRuntime(CombatCatalog(world, {}, ()), epoch=100)
        self.refund = SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.FIXED, amount=50)
        self.program = ActionProgram("skill", "1", "battle", 100, 1, 0, (
            CombatEvent(.2, "refund", resources=(self.refund,)),
            CombatEvent(.8, "later_refund", resources=(self.refund,)),
        ))

    def test_attempt_and_rejection_do_not_spend_or_grant_refunds(self):
        world = self.runtime.world
        self.assertTrue(self.runtime.stage(self.program, 100))
        self.assertEqual(world.sp, 200)
        self.runtime.cancel("1", "battle")
        self.runtime.advance(101)
        self.assertEqual(world.sp, 200)
        self.assertFalse(self.runtime.confirm("1", "battle", 101))

    def test_fresh_hud_sample_replaces_earlier_refund_but_retains_later_event(self):
        world = self.runtime.world
        self.runtime.stage(self.program, 100)
        self.runtime.observe_sp(140, 100.3)
        self.assertTrue(self.runtime.confirm("1", "battle", 101))
        self.assertIs(world, self.runtime.world)
        self.assertEqual(world.sp, 190)
        self.runtime.advance(102)
        self.assertEqual(world.sp, 190)
        self.assertFalse(self.runtime.confirm("1", "battle", 102))

    def test_pre_attempt_sample_is_not_replayed_over_cast_spend(self):
        self.runtime.observe_sp(200, 100)
        self.runtime.stage(self.program, 100)
        self.runtime.confirm("1", "battle", 101)
        self.assertEqual(self.runtime.world.sp, 200)
        self.assertEqual(self.runtime.world._events.count("refund"), 1)

    def test_another_actor_death_survives_pending_commit(self):
        self.runtime.stage(self.program, 100)
        self.runtime.disable_actor("2", 100.1)
        self.runtime.confirm("1", "battle", 101)
        self.assertFalse(self.runtime.world.characters["2"].alive)

    def test_failed_new_attempt_clears_old_prediction(self):
        self.runtime.stage(self.program, 100)
        unavailable = ActionProgram("unavailable", "1", "battle", 300, 1, 0, ())
        self.assertFalse(self.runtime.stage(unavailable, 100))
        self.assertIsNone(self.runtime.pending)
        self.assertFalse(self.runtime.confirm("1", "battle", 101))

    def test_observed_main_control_survives_pending_commit(self):
        self.runtime.stage(self.program, 100)
        self.assertTrue(self.runtime.observe_main_control("2", 100.1))
        self.runtime.confirm("1", "battle", 101)
        self.assertEqual(self.runtime.world.main_control, "2")

    def test_hud_ready_energy_is_local_and_discarded_after_rejection(self):
        ult = ActionProgram("ultimate", "2", "ult", 0, 1, 0, (), energy_cost=100)
        self.assertTrue(self.runtime.stage(ult, 100, energy_ready=True))
        self.assertEqual(self.runtime.world.characters["2"].energy, 0)
        self.runtime.cancel()
        self.assertEqual(self.runtime.world.characters["2"].energy, 0)

    def completed_catalog(self):
        world = CombatWorldState(("1", "2"), regen=0)
        world.characters["2"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        world.characters["2"].attributes["level"] = 80
        world.characters["2"].energy_cap = 120
        program = ActionProgram("new_skill", "2", "battle", 0, 1, 0, (), native_slot=0)
        world.register_native_program(program, default=True)
        return CombatCatalog(world, {("2", "battle"): (program,)}, ())

    def test_completing_unknown_slot_preserves_world_pending_and_observations(self):
        world = self.runtime.world
        world.characters["2"].energy = 37
        world.characters["2"].alive = False
        world.enemies["target"].shred_stacks = 3
        self.assertTrue(self.runtime.stage(self.program, 100))
        pending = self.runtime.pending
        self.runtime._resource_version = 5
        self.runtime.extend_catalog(self.completed_catalog(), ("2",), 100.1)
        self.assertIs(self.runtime.world, world)
        self.assertIs(self.runtime.pending, pending)
        self.assertEqual(self.runtime.epoch, 100)
        self.assertEqual(self.runtime._resource_version, 5)
        self.assertEqual(world.characters["2"].energy, 37)
        self.assertFalse(world.characters["2"].alive)
        self.assertEqual(world.enemies["target"].shred_stacks, 3)
        self.assertEqual(world.selected_native_skill("2", 0), "new_skill")
        self.assertIsNotNone(pending.predicted.characters["2"].panel)
        self.runtime.confirm("1", "battle", 101)
        self.assertIs(self.runtime.world, world)
        self.assertIsNotNone(world.characters["2"].panel)
        self.assertFalse(world.characters["2"].alive)
        self.assertEqual(world.sp, 200)
        self.assertTrue(any("before slot identification: 2" in error for error in world.unresolved))

    def test_completion_cannot_replace_an_already_identified_actor(self):
        catalog = self.completed_catalog()
        self.runtime.extend_catalog(catalog, ("2",), 100)
        before = self.runtime.world.snapshot()
        with self.assertRaises(ValueError):
            self.runtime.extend_catalog(catalog, ("2",), 100)
        self.assertEqual(self.runtime.world.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
