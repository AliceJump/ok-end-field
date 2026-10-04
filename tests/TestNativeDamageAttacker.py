"""Damage attacker selection is independent of the skill actor and buff owner."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_simulation import CombatWorldState, NativeTarget, walk_combat_events
from src.data.damage_resolution import FixedDamagePanel
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeDamageAttacker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]
        cls.character = get_character("perlica")
        data = native_record(cls.store, "buff_common_pulse_pulse_triggered")["data"]
        cls.node = next(n for n in _nodes(data) if n["$type"].endswith(".DamageAction+DamageActionData"))

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        self.world.characters["2"].panel = FixedDamagePanel(200, 0, 0, 1, 1, .5, damage_bonus={"电磁": .5})
        self.world.characters["1"].attributes["ignite_damage_scalar"] = 1
        self.world.characters["2"].attributes["ignite_damage_scalar"] = 1.4

    def program(self, attacker, *, only_main=False, source_main=False, snapshot=False):
        node = copy.deepcopy(self.node)
        body = node["$value"]
        body["attacker"] = attacker
        body["targetSettings"]["targetSource"] = 6
        unit = body["damageUnits"][0]
        unit["takeAtkSnapshot"] = snapshot
        unit["onlyEnableForMainChar"] = only_main
        unit["atkCalculation"]["$value"]["atkScale"].update(useBlackboardKey=False, value=2)
        program = compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                        panel=self.world.characters["1"].panel,
                                        event_sequence={"actionData": [node], "onlyExecuteWhenSourceIsMainChar": source_main})
        return replace(program, sp_cost=0, energy_cost=0, gate=None, actor_lock=0, duration=.1,
                       cooldown=0, events=tuple(replace(e, at=.1) for e in program.events))

    def execute(self, program, context=None):
        self.assertTrue(self.world.start(program, action_id="hit"))
        self.world._action_targets["hit"].update(context or {})
        self.world.advance(.1)

    def test_action_source_uses_actual_attacker_attack_critical_bonus_and_scalar(self):
        program = self.program(0)
        self.execute(program, {"source": ("2",), "owner": ("1",)})
        self.assertAlmostEqual(self.world.damage, 2 * 200 * 1.5 * 1.5 * 1.4)
        self.assertFalse(self.world.unresolved)
        hit = next(e for e in walk_combat_events(program.events) if e.hit)
        self.assertEqual(hit.hit_source, NativeTarget("source"))

    def test_action_owner_and_source_are_independent(self):
        self.execute(self.program(1), {"source": ("1",), "owner": ("2",)})
        self.assertAlmostEqual(self.world.damage, 2 * 200 * 1.5 * 1.5 * 1.4)
        self.assertFalse(self.world.unresolved)

    def test_main_attacker_is_selected_at_live_damage_time(self):
        program = self.program(5)
        self.world.start(program, action_id="hit")
        self.world.main_control = "2"
        self.world.advance(.1)
        self.assertAlmostEqual(self.world.damage, 2 * 200 * 1.5 * 1.5 * 1.4)
        self.assertFalse(self.world.unresolved)

    def test_main_only_hit_and_source_main_sequence_follow_actual_roles(self):
        self.world.main_control = "2"
        self.execute(self.program(0, only_main=True, source_main=True), {"source": ("2",)})
        self.assertAlmostEqual(self.world.damage, 2 * 200 * 1.5 * 1.5 * 1.4)
        self.assertFalse(self.world.unresolved)
        self.setUp()
        self.execute(self.program(1, only_main=True), {"owner": ("2",)})
        self.assertEqual(self.world.damage, 0)

    def test_unknown_multi_target_or_enemy_attacker_never_uses_compiled_actor_panel(self):
        for owners in ((), ("1", "2"), ("target",)):
            with self.subTest(owners=owners):
                self.setUp()
                self.execute(self.program(1), {"owner": owners})
                self.assertEqual(self.world.damage, 0)
                self.assertTrue(self.world.unresolved)

    def test_input_current_and_context_attacker_or_snapshot_remain_explicit(self):
        for attacker in (2, 3, 4, 99):
            with self.subTest(attacker=attacker):
                self.setUp()
                self.execute(self.program(attacker))
                self.assertEqual(self.world.damage, 0)
                self.assertTrue(any("attacker target" in error for error in self.world.unresolved))
        self.setUp()
        self.execute(self.program(0, snapshot=True))
        self.assertTrue(any("attacker attribute snapshot" in error for error in self.world.unresolved))


if __name__ == "__main__":
    unittest.main()
