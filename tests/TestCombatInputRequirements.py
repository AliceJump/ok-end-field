"""Demanded input evaluation matches eager evaluation of actual programs."""

import unittest
from unittest.mock import patch

from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.skill_timing import SkillTimingStore


class TestCombatInputRequirements(unittest.TestCase):
    def test_expression_keys_include_all_short_circuit_arms(self):
        expression = CombatExpression("any", (combat_input("count.STACK_SHRED"), combat_input("native.attribute.2")))
        self.assertEqual(expression.referenced_inputs(), frozenset(("count.STACK_SHRED", "native.attribute.2")))

    def test_plain_event_does_not_scan_effect_counts(self):
        world = CombatWorldState(("1",))
        program = ActionProgram("plain", "1", "normal", 0, 0, 0, (CombatEvent(0, "noop"),))
        world._action_inputs["plain"] = {}
        with patch.object(world, "count", wraps=world.count) as count:
            world._execute_event("plain", 1, program, program.events[0])
        count.assert_not_called()

    def test_real_team_matches_eager_inputs_for_every_program(self):
        catalog = build_combat_catalog(("弭弗", "骏卫", "余烬", "卡缪"), SkillTimingStore())
        for character in catalog.world.characters.values():
            character.energy = character.energy_cap
        for program in catalog.candidates():
            with self.subTest(program=program.key):
                with patch("src.data.combat_simulation.required_input_keys", return_value=None):
                    eager = catalog.world.simulate(program, strict=False)
                optimized = catalog.world.simulate(program, strict=False)
                if eager is None:
                    self.assertIsNone(optimized)
                    continue
                self.assertEqual(eager[1], optimized[1])
                self.assertEqual(eager[0]._action_inputs, optimized[0]._action_inputs)
                self.assertEqual(eager[0].native_buffs, optimized[0].native_buffs)
