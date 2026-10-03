"""Shared combat state must execute sequences rather than value isolated casts."""

import unittest

from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    EffectRequirement,
    plan_action_sequence,
)
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.effects import EffectType
from src.data.skill_types import CombatResourceType, ResourceChangeKind, SkillEffect, SkillResourceChange


def effect(key, count=1, duration=None, target="enemy", **kwargs):
    return SkillEffect(key, value=None, count=count, duration=duration, target=target, **kwargs)


def program(key, actor="carry", *, cost=0, damage=1, effects=(), resources=(), requires=(), duration=1, cooldown=0):
    return ActionProgram(key, actor, "battle", cost, duration, cooldown, (
        CombatEvent(.5, "on_hit", tuple(effects), tuple(resources), hit=DamageHit(actor, "target", "物理", damage)),
    ), tuple(requires))


class TestCombatSimulation(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("support", "carry"), sp=100, regen=0)
        for state in self.world.characters.values():
            state.panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)

    def test_simulation_is_a_counterfactual_and_resources_apply_once(self):
        refund = SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.FIXED, amount=50)
        p = program("cast", cost=100, resources=(refund,))
        before = self.world.snapshot()
        result, outcome = self.world.simulate(p)
        self.assertEqual(self.world.snapshot(), before)
        self.assertEqual(result.sp, 50)
        self.assertEqual(outcome.damage, 100)
        self.assertTrue(self.world.start(p, action_id="actual"))
        self.assertFalse(self.world.start(p, action_id="actual"))
        self.world.advance(1)
        self.assertEqual(self.world.sp, 50)

    def test_shared_and_private_pools_have_correct_owners_and_caps(self):
        for key in (EffectType.STACK_COMBO, EffectType.STACK_MOLTEN):
            self.world.apply_effect("support", "target", effect(key, count=7, target="self"))
        self.assertEqual(self.world.count("carry", "target", EffectType.STACK_COMBO), 4)
        self.assertEqual(self.world.count("support", "target", EffectType.STACK_MOLTEN), 4)
        self.assertEqual(self.world.count("carry", "target", EffectType.STACK_MOLTEN), 0)

    def test_attachment_and_shred_predicates_read_actual_pool(self):
        self.world.apply_effect("support", "target", effect(EffectType.ATTACH_COLD, count=2))
        self.world.apply_effect("support", "target", effect(EffectType.STACK_SHRED, count=3))
        self.assertEqual(self.world.count("carry", "target", EffectType.STATUS_SPELL_INFLICT), 2)
        self.assertEqual(self.world.count("carry", "target", EffectType.STATUS_SHRED), 3)
        self.assertFalse(self.world.satisfies("carry", "target", (EffectRequirement(EffectType.STACK_SHRED, 4),)))

    def test_actual_consumption_drives_refund_after_pool_is_empty(self):
        self.world.enemies["target"].add_shred(4)
        refund = SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.PIECEWISE_BY_COUNT,
                                    source_effect_id=EffectType.EVENT_SHRED_CONSUMED, values_by_count={0: 0, 1: 5, 2: 12, 3: 20, 4: 35})
        p = program("consume", cost=100, effects=(effect(EffectType.STACK_SHRED, -1, consumes_all=True),), resources=(refund,))
        after, outcome = self.world.simulate(p)
        self.assertEqual(after.enemies["target"].shred_stacks, 0)
        self.assertEqual(after.sp, 35)
        self.assertIn(("STACK_SHRED", 4), outcome.consumed)
        self.assertEqual(after.count("carry", "target", EffectType.EVENT_SHRED_CONSUMED), 4)

    def test_replacement_requires_ready_state_and_does_not_charge_base_cost(self):
        ready = EffectType.STATUS_MIFU_ZHUIXING_READY
        self.world.apply_effect("carry", "target", effect(ready, duration=10, target="self"))
        p = program("replacement", cost=50, effects=(effect(ready, -1, target="self"),), requires=(EffectRequirement(ready),))
        after, _ = self.world.simulate(p)
        self.assertEqual(after.sp, 50)
        self.assertIsNone(after.simulate(p))

    def test_unknown_count_cannot_become_one_and_unknown_action_cannot_win(self):
        unknown = program("unresolved", cost=0, damage=100, effects=(effect(EffectType.STACK_MOLTEN, None, target="self"),))
        self.assertIsNone(self.world.simulate(unknown))
        _, outcome = self.world.simulate(unknown, strict=False)
        self.assertIn("Unknown count: STACK_MOLTEN", outcome.unresolved)
        plan = plan_action_sequence(self.world, (unknown, program("known", damage=1)), depth=1)
        self.assertEqual(plan.actions, ("known",))

    def test_low_damage_producer_wins_through_unlocked_consumer(self):
        producer = program("advance", actor="support", cost=100, damage=.1,
                           effects=(effect(EffectType.STACK_SHRED, count=3),),
                           resources=(SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.FIXED, amount=50),))
        consumer = program("followup", cost=50, damage=10, requires=(EffectRequirement(EffectType.STACK_SHRED, 3),),
                           effects=(effect(EffectType.STACK_SHRED, -1, consumes_all=True),))
        direct = program("direct", cost=100, damage=3)
        plan = plan_action_sequence(self.world, (producer, consumer, direct), depth=2)
        self.assertEqual(plan.actions, ("advance", "followup"))
        self.assertEqual(plan.damage, 1010)
        self.assertEqual(plan.ending_sp, 0)

    def test_cooldown_wait_uses_actual_regen_and_expiration(self):
        self.world.regen = 8
        p = program("attack", cost=100, cooldown=15)
        after, _ = self.world.simulate(p)
        self.assertIsNone(after.simulate(p))
        after.advance(15)
        self.assertEqual(after.sp, 120)
        self.assertIsNotNone(after.simulate(p))

    def test_death_cancels_queued_damage_and_hud_replaces_predictions(self):
        p = program("attack", cost=100)
        self.world.start(p, action_id="a")
        self.world.disable_actor("carry")
        self.world.advance(1)
        self.assertEqual(self.world.damage, 0)
        self.world.observe_resources(sp=70, energy={"carry": 30})
        self.assertEqual(self.world.sp, 70)
        self.assertEqual(self.world.characters["carry"].energy, 30)

    def test_one_arrow_is_consumed_without_emptying_private_pool(self):
        self.world.apply_effect("carry", "target", effect(EffectType.STACK_HUNTING_ARROW, 4, target="self"))
        after, outcome = self.world.simulate(program("shoot", effects=(effect(EffectType.STACK_HUNTING_ARROW, -1, target="self"),)))
        self.assertEqual(after.count("carry", "target", EffectType.STACK_HUNTING_ARROW), 3)
        self.assertIn(("STACK_HUNTING_ARROW", 1), outcome.consumed)

    def test_overlapping_actions_keep_their_own_consumption_refunds(self):
        self.world.apply_effect("carry", "target", effect(EffectType.STACK_HUNTING_ARROW, 4, target="self"))
        refund = SkillResourceChange(CombatResourceType.SKILL_POINT, "team", ResourceChangeKind.PER_EFFECT_COUNT,
                                    per_unit=10, source_effect_id=EffectType.STACK_HUNTING_ARROW, count_basis="consumed")
        slow = ActionProgram("slow", "carry", "battle", 0, .2, 0, (
            CombatEvent(0, "consume", effects=(effect(EffectType.STACK_HUNTING_ARROW, -2, target="self"),)),
            CombatEvent(2, "refund", resources=(refund,)),
        ))
        fast = ActionProgram("fast", "carry", "battle", 0, .2, 0, (
            CombatEvent(0, "consume", effects=(effect(EffectType.STACK_HUNTING_ARROW, -1, target="self"),)),
            CombatEvent(.5, "refund", resources=(refund,)),
        ))
        self.world.start(slow, action_id="slow")
        self.world.advance(.2)
        self.world.start(fast, action_id="fast")
        self.world.advance(2)
        self.assertEqual(self.world.sp, 130)

    def test_independent_layers_expire_without_refreshing_previous_layers(self):
        key = EffectType.STACK_MORALE
        inputs = {f"effect.{key.value}.independent": 1}
        self.world.apply_effect("support", "target", effect(key, 1, duration=5, target="team"), inputs)
        self.world.advance(3)
        self.world.apply_effect("support", "target", effect(key, 1, duration=5, target="team"), inputs)
        self.assertEqual(self.world.count("carry", "target", key), 2)
        self.world.advance(5)
        self.assertEqual(self.world.count("carry", "target", key), 1)
        self.world.advance(8)
        self.assertEqual(self.world.count("carry", "target", key), 0)

    def test_two_fields_keep_distinct_lifetimes_and_consumption_removes_them(self):
        key = EffectType.MECH_SUPPORT_CRYSTAL
        self.world.apply_effect("support", "target", effect(key, duration=5, target="field"))
        self.world.advance(3)
        self.world.apply_effect("support", "target", effect(key, duration=5, target="field"))
        self.assertEqual(len(self.world.damage_state.fields), 2)
        self.world.advance(5)
        self.assertEqual(self.world.count("support", "target", key), 1)
        self.assertEqual(len(self.world.damage_state.fields), 1)
        self.world.consume("support", "target", key)
        self.assertFalse(self.world.damage_state.fields)


if __name__ == "__main__":
    unittest.main()
