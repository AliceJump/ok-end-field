"""Authored spell burst buffs resolve once after one second in their own scopes."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffQuery,
    NativeListener,
    NativeTarget,
    walk_combat_events,
)
from src.data.damage_resolution import FixedDamagePanel
from src.data.effects import EffectType
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_asset, native_record
from src.data.native_reactions import reaction_enhancement
from src.data.native_spell_runtime import SPELL_ELEMENTS, attachment_policies
from src.data.skill_timing import SkillTimingStore


class TestNativeSpellBurst(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        record = native_record(store, profile.skill_id)["data"]
        spell = next(n for n in _nodes(record) if n["$type"].endswith(".SpellInfliction+Data"))
        cls.programs = {}
        for kind in SPELL_ELEMENTS:
            node = copy.deepcopy(spell)
            node["$value"]["inflictionType"] = kind
            node["$value"]["target"]["targetSource"] = 6
            program = compile_native_action(store, get_character("perlica"), profile, "1", "normal",
                                            panel=FixedDamagePanel(100, 0, 0, 1, 0, .5),
                                            event_sequence={"actionData": [node]})
            cls.programs[kind] = replace(program, sp_cost=0, energy_cost=0, gate=None, duration=0,
                                         actor_lock=0, cooldown=0)

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.enemies["other"] = EnemyCombatState()
        self.world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        self.world.characters["2"].panel = FixedDamagePanel(999, 0, 0, 1, 0, .5)
        self.world.characters["1"].attributes.update(arts_strength=50, ignite_damage_scalar=1.4)
        self.serial = 0

    def cast(self, kind=1, enemy="target", program=None):
        self.serial += 1
        self.assertTrue(self.world.start(replace(program or self.programs[kind], enemy=enemy),
                                         action_id=f"cast{self.serial}"))

    def expected(self, arts=50):
        row = next(r for r in native_asset("SkillSetting")["spellInflictionDataList"] if r["key"] == "法术爆发伤害倍率")
        return 100 * row["values"][0] * (1 + reaction_enhancement("Damage", arts)) * 1.4

    def test_all_four_real_buffs_delay_one_second_and_trigger_once(self):
        for kind, (element, _) in SPELL_ELEMENTS.items():
            with self.subTest(element=element):
                self.setUp()
                self.cast(kind)
                self.assertFalse(self.world.native_buff_instances)
                self.cast(kind)
                key = f"buff_common_{element}_{element}_triggered"
                instance = next(iter(self.world.native_buff_instances.values()))
                self.assertEqual((instance.key, instance.source, instance.owner, instance.period, instance.remaining),
                                 (key, "1", "target", 1, 1))
                self.assertNotIn("STATUS_SPELL_BURST", self.world._events)
                self.world.advance(.999)
                self.assertEqual(self.world.damage, 0)
                self.world.advance(1)
                self.assertAlmostEqual(self.world.damage, self.expected(), places=5)
                self.assertEqual(self.world._events.count("STATUS_SPELL_BURST"), 1)
                self.world.advance(2)
                self.assertAlmostEqual(self.world.damage, self.expected(), places=5)
                self.world.advance(10)
                self.assertNotIn(("target", key), self.world.native_buffs)
                self.assertFalse(any("TriggerSpellBurstEventAction" in error for error in self.world.unresolved))
                self.assertNotIn("Unbound spell reaction: STATUS_SPELL_BURST", self.world.unresolved)

    def test_repeated_inflictions_have_independent_clocks_and_blackboards(self):
        self.cast()
        self.cast()
        self.world.advance(.25)
        self.cast()
        instances = list(self.world.native_buff_instances.values())
        self.assertEqual(len(instances), 2)
        self.assertEqual([v.next_trigger for v in instances], [1, 1.25])
        self.world.advance(1)
        self.assertAlmostEqual(self.world.damage, self.expected(), places=5)
        first_scale = self.world._action_inputs[instances[0].uid]["bb.atk_scale"]
        self.world.characters["1"].attributes["arts_strength"] = 200
        self.world.advance(1.25)
        self.assertAlmostEqual(self.world.damage, self.expected() + self.expected(200), places=5)
        self.assertEqual(self.world._action_inputs[instances[0].uid]["bb.atk_scale"], first_scale)
        self.assertNotEqual(self.world._action_inputs[instances[1].uid]["bb.atk_scale"], first_scale)

    def test_switching_main_does_not_change_original_caster_or_target(self):
        self.cast(enemy="other")
        self.cast(enemy="other")
        self.world.main_control = "2"
        self.world.advance(1)
        self.assertAlmostEqual(self.world.damage, self.expected(), places=5)
        self.assertEqual(self.world.enemies["other"].infliction_stacks, 2)
        self.assertEqual(self.world.enemies["target"].infliction_stacks, 0)

    def test_removing_attachment_does_not_cancel_independent_burst(self):
        self.cast()
        self.cast()
        self.world.consume("1", "target", EffectType.ATTACH_ELECTROMAGNETIC)
        self.world.advance(1)
        self.assertAlmostEqual(self.world.damage, self.expected(), places=5)

    def test_native_finish_buff_cancels_pending_burst(self):
        self.cast()
        self.cast()
        change = NativeBuffChange("buff_common_pulse_pulse_triggered", CombatExpression("literal", (-1,)),
                                  remove_all=True, selector=NativeTarget("main_target"))
        program = ActionProgram("finish", "1", "normal", 0, 0, 0,
                                (CombatEvent(0, "remove", native_buffs=(change,)),))
        self.world.start(program, action_id="remove")
        self.world.advance(2)
        self.assertEqual(self.world.damage, 0)
        self.assertNotIn("STATUS_SPELL_BURST", self.world._events)

    def test_caster_and_enemy_before_burst_callbacks_receive_type_and_actual_target(self):
        key = attachment_policies()[1][0]
        query = NativeBuffQuery("q", NativeTarget("action_target"), (key,))
        observe = ActionProgram("observe", "1", "normal", 0, 0, 0, (), native_buff_queries=(query,))
        for owner, trigger in (("1", "OnCharBeforeOutputSpellBurst"), ("other", "OnEnemyBeforeTakeSpellBurst")):
            scope = "observe:" + owner
            self.world.native_buffs[owner, "listen"] = (1, None)
            self.world._action_inputs[scope] = {}
            self.world._action_targets[scope] = {"current": ("1",)}
            callback = CombatEvent(0, "observe", assignments=(("bb.type", combat_input("event.spell_type")),
                                                               ("bb.layers", combat_input("q"))))
            self.world.native_listeners.append((owner, scope, observe, NativeListener("listen", trigger, (callback,))))
        self.cast(enemy="other")
        self.cast(enemy="other")
        self.assertNotIn("bb.type", self.world._action_inputs["observe:1"])
        self.world.advance(1)
        for owner in ("1", "other"):
            self.assertEqual(self.world._action_inputs["observe:" + owner], {"bb.type": 1, "bb.layers": 2})
            self.assertEqual(self.world._action_targets["observe:" + owner]["current"], ("1",))

    def test_forecast_isolated_and_nested_unknown_callbacks_still_block_complete_pricing(self):
        self.cast()
        original = self.world
        self.world = original.fork()
        self.cast()
        self.world.advance(1)
        self.assertGreater(self.world.damage, 0)
        self.assertEqual(original.damage, 0)
        self.assertFalse(original.native_buff_instances)
        events = tuple(walk_combat_events(self.programs[1].events))
        self.assertTrue(any(e.native_spell_bursts for e in events))
        self.assertTrue(any("OnSpellAbnormalStartFinish" in error for e in events for error in e.unresolved))

    def test_different_actual_source_keeps_unbound_reaction_explicit(self):
        program = self.programs[1]
        event = program.events[0]
        change = replace(event.native_spells[0], source=NativeTarget("main"))
        program = replace(program, events=(replace(event, native_spells=(change,)),))
        self.world.main_control = "2"
        self.cast(program=program)
        self.cast(program=program)
        self.world.advance(1)
        self.assertEqual(self.world.damage, 0)
        self.assertFalse(self.world.native_buff_instances)
        self.assertIn("Unbound spell reaction: STATUS_SPELL_BURST", self.world.unresolved)


if __name__ == "__main__":
    unittest.main()
