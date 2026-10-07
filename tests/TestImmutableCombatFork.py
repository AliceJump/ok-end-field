"""Shared definitions must preserve the results and isolation of full deepcopy."""

import copy
import unittest
from unittest.mock import patch

from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState
from src.data.damage_resolution import FixedDamagePanel
from src.data.effects import EffectType
from src.data.immutable_combat_value import ImmutableCombatValue
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import SkillEffect


def unshared_copy(value, memo):
    result = type(value).__new__(type(value))
    memo[id(value)] = result
    for key, field in value.__dict__.items():
        object.__setattr__(result, key, copy.deepcopy(field, memo))
    return result


class TestImmutableCombatFork(unittest.TestCase):
    def test_safe_definition_is_shared_but_mutable_descendant_is_copied(self):
        safe = ActionProgram("safe", "1", "normal", 0, 1, 0, (CombatEvent(0, "event"),))
        self.assertIs(copy.deepcopy(safe), safe)
        effect = SkillEffect(EffectType.STACK_SHRED, count=1)
        detached = ActionProgram("detached", "1", "normal", 0, 1, 0, (CombatEvent(0, "effect", effects=(effect,)),))
        effect.count = 9
        self.assertEqual(detached.events[0].effects[0].count, 1)
        self.assertIs(copy.deepcopy(detached), detached)
        expression = CombatExpression("add", [1, 2])
        other = copy.deepcopy(expression)
        other.operands[0] = 4
        self.assertEqual(expression.evaluate({}), 3)

    def test_fork_does_not_share_panel_maps_resources_attributes_or_event_blackboards(self):
        world = CombatWorldState(("1",), sp=100)
        world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5, damage_bonus={"skill": .1})
        world.characters["1"].attributes["ATK"] = 100
        world._action_inputs["a"] = {"bb.count": 1}
        before = world.snapshot()
        fork = world.fork()
        fork.characters["1"].panel.damage_bonus["skill"] = 99
        fork.characters["1"].attributes["ATK"] = 9
        fork._action_inputs["a"]["bb.count"] = 5
        fork.advance(2)
        fork.sp = 0
        self.assertEqual(world.snapshot(), before)
        self.assertEqual(world.characters["1"].panel.damage_bonus, {"skill": .1})
        self.assertEqual(world.characters["1"].attributes["ATK"], 100)
        self.assertEqual(world._action_inputs["a"], {"bb.count": 1})

    def test_real_team_all_programs_match_unoptimized_full_copy(self):
        catalog = build_combat_catalog(("弭弗", "骏卫", "余烬", "卡缪"), SkillTimingStore())
        for state in catalog.world.characters.values():
            state.energy = state.energy_cap
        original = catalog.world.snapshot()
        for program in catalog.candidates():
            with self.subTest(program=program.key):
                with patch.object(ImmutableCombatValue, "__deepcopy__", unshared_copy):
                    baseline = catalog.world.simulate(program, strict=False)
                optimized = catalog.world.simulate(program, strict=False)
                if baseline is None:
                    self.assertIsNone(optimized)
                    continue
                self.assertEqual(baseline[1], optimized[1])
                self.assertEqual(baseline[0]._action_inputs, optimized[0]._action_inputs)
                self.assertEqual(baseline[0].native_buffs, optimized[0].native_buffs)
                self.assertEqual(baseline[0].unresolved, optimized[0].unresolved)
                if optimized[0].native_buff_instances:
                    uid = next(iter(optimized[0].native_buff_instances))
                    optimized[0].native_buff_instances[uid].remaining = 999
                    self.assertNotEqual(baseline[0].native_buff_instances[uid].remaining, 999)
        self.assertEqual(catalog.world.snapshot(), original)
