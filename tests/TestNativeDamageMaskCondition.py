"""Native decoration guards require an explicit damage event context."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import MissingCombatInput
from src.data.combat_simulation import CombatWorldState
from src.data.native_action_program import compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeDamageMaskCondition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("chen_qianyu")
        cls.profile = cls.store.profiles("陈千语", "battle")[0]
        cls.parent = native_record(cls.store, "buff_chr_0005_chen_talent_0")["data"]
        cls.nodes = [block["actionData"][0]
                     for entry in cls.parent["abilityEventAction"] for block in entry["actions"]]

    def program(self, *, mask=256, mode=2, invert=False):
        node = copy.deepcopy(self.nodes[0])
        node["$value"].update(mask=mask, checkType=mode)
        actions = [node]
        if invert:
            actions.insert(0, {"$type": "Beyond.Gameplay.Core.NotNextCheckAction+Data", "$value": {"isEnable": True}})
        return compile_native_action(self.store, self.character, self.profile, "1", "battle",
            event_sequence={"actionData": actions, "onlyExecuteWhenSourceIsGuard": False,
                            "onlyExecuteWhenSourceIsMainChar": False})

    def expression(self, **kwargs):
        program = self.program(**kwargs)
        self.assertFalse(any(event.unresolved for event in program.events))
        return next(expr for event in program.events for _, expr in event.assignments)

    def test_original_talent_uses_three_skill_masks_and_no_normal_damage_guard(self):
        self.assertEqual([node["$value"]["mask"] for node in self.nodes], [256, 512, 8192])
        self.assertEqual({node["$value"]["checkType"] for node in self.nodes}, {2})
        for node in self.nodes:
            mask = node["$value"]["mask"]
            expr = self.expression(mask=mask)
            self.assertEqual(expr.evaluate({"event.damage.decorate_mask": mask}), 1)
            self.assertEqual(expr.evaluate({"event.damage.decorate_mask": 0}), 0)
            self.assertEqual(expr.evaluate({"event.damage.decorate_mask": mask | 1}), 1)

    def test_exact_any_and_all_follow_native_integer_bit_comparisons(self):
        packet = lambda mask: {"event.damage.decorate_mask": mask}
        exact = self.expression(mask=768, mode=0)
        any_bit = self.expression(mask=768, mode=1)
        all_bits = self.expression(mask=768, mode=2)
        for actual, expected in ((0, (0, 0, 0)), (256, (0, 1, 0)), (768, (1, 1, 1)), (769, (0, 1, 1))):
            self.assertEqual(tuple(expr.evaluate(packet(actual)) for expr in (exact, any_bit, all_bits)), expected)
        self.assertEqual(self.expression(mask=0, mode=2).evaluate(packet(256)), 1)
        self.assertEqual(self.expression(mask=0, mode=1).evaluate(packet(256)), 0)

    def test_original_blocks_are_independent_for_composite_damage_masks(self):
        expressions = [self.expression(mask=node["$value"]["mask"]) for node in self.nodes]
        self.assertEqual([expr.evaluate({"event.damage.decorate_mask": 256 | 512}) for expr in expressions], [1, 1, 0])
        self.assertEqual([expr.evaluate({"event.damage.decorate_mask": 256 | 512 | 8192}) for expr in expressions], [1, 1, 1])

    def test_event_context_is_not_inferred_from_skill_tag_or_expected_critical(self):
        expr = self.expression()
        for inputs in ({}, {"event.skill_type": 1, "source.ATK": 100}, {"event.critical": 1}):
            with self.subTest(inputs=inputs), self.assertRaises(MissingCombatInput):
                expr.evaluate(inputs)

    def test_unreviewed_modes_and_inexact_int64_inputs_stay_unknown(self):
        for mode in (3, 4, 99):
            self.assertTrue(any(event.unresolved for event in self.program(mode=mode).events))
        self.assertTrue(any(event.unresolved for event in self.program(mask=2**53).events))
        for actual in (256.5, float("nan"), 2**53, -(2**53)):
            with self.subTest(actual=actual), self.assertRaises(MissingCombatInput):
                self.expression().evaluate({"event.damage.decorate_mask": actual})

    def test_not_next_check_preserves_inversion_and_prediction_does_not_fill_missing_context(self):
        expr = self.expression(invert=True)
        self.assertEqual(expr.evaluate({"event.damage.decorate_mask": 256}), 0)
        self.assertEqual(expr.evaluate({"event.damage.decorate_mask": 512}), 1)
        world = CombatWorldState(("1",), regen=0)
        fork = world.fork()
        program = replace(self.program(), sp_cost=0, duration=0, actor_lock=0, cooldown=0)
        self.assertTrue(fork.start(program, action_id="check"))
        self.assertTrue(fork.unresolved)
        self.assertFalse(world.unresolved)
        self.assertEqual(len(world.native_buff_instances), 0)
