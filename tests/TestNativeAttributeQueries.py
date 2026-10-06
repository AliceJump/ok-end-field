"""Native enhancement attributes follow the authored recipient at callback time."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import CombatEvent, CombatWorldState, NativeTarget
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.native_reactions import reaction_enhancement
from src.data.skill_timing import SkillTimingStore


class TestNativeAttributeQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]
        cls.character = get_character("perlica")
        data = native_record(cls.store, "buff_common_fire_fire_triggered")["data"]
        cls.request = next(n for n in _nodes(data) if n["$type"].endswith(".ReadSkillSettingData+Data"))

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.characters["1"].attributes["arts_strength"] = 10
        self.world.characters["2"].attributes["arts_strength"] = 200

    def program(self, source, key=""):
        request = copy.deepcopy(self.request)
        request["$value"]["dataList"][0]["enhanceAttributeSource"].update(targetSource=source, targetGroupKey=key)
        return compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                     event_sequence={"actionData": [request]})

    def execute(self, program, context=None):
        # Delay permits the scenario to provide a real callback Source/Owner.
        program = replace(program, sp_cost=0, energy_cost=0, gate=None, actor_lock=0, duration=.1, cooldown=0,
                          events=tuple(replace(e, at=.1) for e in program.events))
        self.assertTrue(self.world.start(program, action_id="read"))
        self.world._action_targets["read"].update(context or {})
        self.world.advance(.1)
        return self.world._action_inputs["read"]

    def amount(self, strength):
        return 1.6 * (1 + reaction_enhancement("Damage", strength))

    def test_source_and_buff_owner_have_distinct_attributes(self):
        program = self.program(4)
        self.assertEqual(program.native_attribute_queries[0].target, NativeTarget("owner"))
        values = self.execute(program, {"source": ("1",), "owner": ("2",)})
        self.assertAlmostEqual(values["bb.atk_scale"], self.amount(200), places=6)
        self.assertNotAlmostEqual(values["bb.atk_scale"], self.amount(10))
        self.assertFalse(self.world.unresolved)

    def test_source_selector_uses_actual_source_not_program_actor(self):
        values = self.execute(self.program(1), {"source": ("2",), "owner": ("1",)})
        self.assertAlmostEqual(values["bb.atk_scale"], self.amount(200), places=6)

    def test_main_character_and_context_group_read_selected_character(self):
        self.world.main_control = "2"
        values = self.execute(self.program(5))
        self.assertAlmostEqual(values["bb.atk_scale"], self.amount(200), places=6)
        self.setUp()
        values = self.execute(self.program(2, "recipient"), {"recipient": ("2",)})
        self.assertAlmostEqual(values["bb.atk_scale"], self.amount(200), places=6)

    def test_unknown_enemy_multi_target_or_missing_attribute_never_reads_actor_fallback(self):
        for context in ({"recipient": ("target",)}, {"recipient": ("1", "2")}, {"recipient": ()}, {}):
            with self.subTest(context=context):
                self.setUp()
                values = self.execute(self.program(2, "recipient"), context)
                self.assertNotIn("bb.atk_scale", values)
                self.assertTrue(any("native.attribute" in error for error in self.world.unresolved))
        self.setUp()
        self.world.characters["2"].attributes.clear()
        values = self.execute(self.program(4), {"owner": ("2",)})
        self.assertNotIn("bb.atk_scale", values)
        self.assertTrue(self.world.unresolved)

    def test_unused_missing_query_in_false_branch_and_prediction_are_safe(self):
        program = self.program(2, "recipient")
        program = replace(program, events=tuple(replace(e, condition=CombatExpression("literal", (0,)))
                                                for e in program.events))
        self.execute(program)
        self.assertFalse(self.world.unresolved)
        self.setUp()
        original = self.world
        self.world = original.fork()
        values = self.execute(self.program(4), {"owner": ("2",)})
        self.assertAlmostEqual(values["bb.atk_scale"], self.amount(200), places=6)
        self.assertEqual(original.time, 0)
        self.assertFalse(original._action_inputs)

    def test_failed_enhancement_cannot_use_serialized_multiplier_for_later_damage(self):
        self.world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        program = self.program(4)
        self.assertIn("bb.atk_scale", dict(program.parameters))
        hit = CombatEvent(0, "following_hit", hit=DamageHit("1", "target", "灼热", 0),
                          hit_multiplier_formula=combat_input("bb.atk_scale"))
        program = replace(program, events=(*program.events, hit))
        values = self.execute(program, {"owner": ("target",)})
        self.assertNotIn("bb.atk_scale", values)
        self.assertEqual(self.world.damage, 0)
        self.assertTrue(any("bb.atk_scale" in error for error in self.world.unresolved))


if __name__ == "__main__":
    unittest.main()
