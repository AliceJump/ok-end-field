"""Declared scene omissions preserve priced outcomes; numeric distance reads remain blocked."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import CombatWorldState, walk_combat_events
from src.data.damage_resolution import FixedDamagePanel
from src.data.native_action_program import _SCENARIO_IGNORED, _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestScenarioIgnoredNodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]
        cls.character = get_character("perlica")
        data = native_record(cls.store, "buff_common_pulse_pulse_triggered")["data"]
        cls.damage = copy.deepcopy(next(n for n in _nodes(data) if n["$type"].endswith(".DamageAction+DamageActionData")))
        cls.damage["$value"]["attacker"] = 0
        cls.damage["$value"]["targetSettings"]["targetSource"] = 6
        unit = cls.damage["$value"]["damageUnits"][0]
        unit["takeAtkSnapshot"] = False
        unit["atkCalculation"]["$value"]["atkScale"].update(useBlackboardKey=False, value=2)

    def compile(self, nodes):
        return compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                     event_sequence={"actionData": nodes})

    def outcome(self, program):
        world = CombatWorldState(("1",), regen=0)
        world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        world.characters["1"].attributes["ignite_damage_scalar"] = 1
        program = replace(program, sp_cost=20, energy_cost=0, gate=None, duration=.1, cooldown=0)
        return world.simulate(program, strict=False)[1]

    def test_all_eight_scene_types_preserve_damage_sp_and_energy(self):
        base = self.compile([self.damage])
        for name in sorted(_SCENARIO_IGNORED):
            with self.subTest(name=name):
                node = {"$type": "Beyond.Gameplay.Core." + name,
                        "$value": {"isEnable": True, "serverActionIndex": 999,
                                   "bbKey": "unused_test_distance", "desiredAlphaSetting": 1}}
                with patch("src.data.native_action_program._SCENARIO_IGNORED", set()):
                    before = self.compile([node])
                after = self.compile([node])
                old = self.outcome(replace(base, events=(*before.events, *base.events)))
                new = self.outcome(replace(base, events=(*after.events, *base.events)))
                self.assertEqual(old.damage, new.damage)
                self.assertGreater(new.damage, 0)
                self.assertEqual(old.after.sp, new.after.sp)
                self.assertEqual(old.after.energy, new.after.energy)
                self.assertTrue(old.unresolved)
                self.assertFalse(new.unresolved)
                self.assertIn(name, after.scenario_ignored_nodes)

    def test_distance_consumed_by_numeric_comparison_remains_unresolved(self):
        save = {"$type": "Beyond.Gameplay.Core.SaveTargetDistanceAction+Data",
                "$value": {"isEnable": True, "serverActionIndex": 1, "bbKey": "distance_for_damage"}}
        compare = {"$type": "Beyond.Gameplay.Core.Conditions.CompareFloat+Data",
                   "$value": {"isEnable": True, "serverActionIndex": 2, "compare": 0,
                              "valueA": {"useBlackboardKey": True, "blackboardKey": "distance_for_damage", "value": 0},
                              "valueB": {"useBlackboardKey": False, "value": 1}}}
        program = self.compile([save, compare, self.damage])
        reasons = [r for event in walk_combat_events(program.events) for r in event.unresolved]
        self.assertTrue(any("SaveTargetDistanceAction" in reason for reason in reasons))
        self.assertNotIn("SaveTargetDistanceAction+Data", program.scenario_ignored_nodes)
