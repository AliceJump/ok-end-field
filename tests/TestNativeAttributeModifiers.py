"""Akekuri's actual P3 producer supplies scoped ATK until its authored cleanup."""

import copy
import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import ActionProgram, CombatWorldState
from src.data.damage_attributes import DamageAttributeBasis
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_attribute_modifiers import AKEKURI_TEAM_ATTACK, compile_attack_additions, reviewed_attack_binding
from src.data.native_damage_processors import resolve_native_hit
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


class TestNativeAttributeModifiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("秋栗", "ult")[0]
        cls.source = native_record(cls.store, cls.profile.skill_id)
        # Keep the complete original producer block, including the P3 condition,
        # team selector and frame bounds. Other ult programs remain unverified.
        cls.window = next(window for window in cls.source["data"]["actionGroupData"]["timelineActions"]
                          if any(AKEKURI_TEAM_ATTACK in str(node) for node in _nodes(window)))
        cls.buff = native_record(cls.store, AKEKURI_TEAM_ATTACK)["data"]

    def setUp(self):
        self.world = CombatWorldState(("1", "2", "3"), regen=0)
        for state in self.world.characters.values():
            state.panel = FixedDamagePanel(100, .5, 25, 2, 0, .5)
            state.attributes["ATK"] = state.panel.attack()
            state.energy = 1000

    def producer(self, potential=5):
        record = copy.deepcopy(self.source)
        record["data"]["actionGroupData"]["timelineActions"] = [self.window]
        record["data"]["actionGroupData"]["passiveEventActions"] = []

        def lookup(store, key):
            return record if key == self.profile.skill_id else native_record(store, key)

        with patch("src.data.native_action_program.native_record", side_effect=lookup):
            program = compile_native_action(self.store, get_character("akekuri", potential=potential),
                                            self.profile, "1", "ult")
        # Deliberately different handoff proves it does not determine buff end.
        return replace(program, duration=.1, actor_lock=.1, cooldown=0)

    def result(self, actor="2"):
        state = self.world.characters[actor]
        return resolve_native_hit(self.world, state.panel, DamageHit(actor, "target", "物理", 1), {})

    def test_actual_selected_p3_producer_affects_team_and_ends_at_authored_node(self):
        self.world.characters["3"].alive = False
        self.assertTrue(self.world.start(self.producer(), action_id="ult"))
        self.assertFalse(self.world.unresolved)
        self.assertEqual({instance.owner for instance in self.world.native_buff_instances.values()}, {"1", "2"})
        amount = single(get_character("akekuri").progression.active_potentials[2].parameters["atk"])
        for actor in ("1", "2"):
            self.assertAlmostEqual(self.result(actor).non_crit, (100 * (1.5 + amount) + 25) * 2)
            self.assertEqual(self.world.effective_attributes(actor)["ATK"], self.result(actor).non_crit)
        self.assertEqual(self.result("3").non_crit, 350)
        self.world.advance(.2)
        self.assertTrue(self.world.native_buff_instances)
        self.world.advance(self.window["_endFrame"] / 30)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.result().non_crit, 350)
        self.assertEqual(self.world.characters["2"].attributes["ATK"], 350)

    def test_potential_gate_and_failed_or_duplicate_release_never_add_twice(self):
        self.assertTrue(self.world.start(self.producer(2), action_id="p2"))
        self.assertFalse(self.world.native_buff_instances)
        self.world.advance(.2)
        self.world.characters["1"].alive = False
        self.assertFalse(self.world.start(self.producer(), action_id="dead"))
        self.assertFalse(self.world.native_buff_instances)
        self.world.characters["1"].alive = True
        self.assertTrue(self.world.start(self.producer(), action_id="p3"))
        self.assertFalse(self.world.start(self.producer(), action_id="p3"))
        self.assertEqual(len(self.world.native_buff_instances), 3)

    def test_cancellation_removes_all_recipients_without_late_deadline_removing_new_instances(self):
        program = self.producer()
        self.world.start(program, action_id="first")
        first = set(self.world.native_buff_instances)
        self.world.advance(1)
        self.world.start(program, action_id="next")
        self.assertFalse(first.intersection(self.world.native_buff_instances))
        self.world.advance(5)
        self.assertEqual(len(self.world.native_buff_instances), 3)
        self.assertGreater(self.result().non_crit, 350)
        self.world.end_native_scope("next", interrupted=True)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.result().non_crit, 350)

    def test_actual_removal_fork_and_teammate_action_preserve_correct_ownership(self):
        self.world.start(self.producer(), action_id="ult")
        self.world.start(ActionProgram("ally", "2", "normal", 0, .1, 0, ()), action_id="ally")
        self.assertEqual(len(self.world.native_buff_instances), 3)
        fork = self.world.fork()
        fork.end_native_scope("ult", interrupted=True)
        self.assertFalse(fork.native_buff_instances)
        self.assertEqual(len(self.world.native_buff_instances), 3)
        from src.data.native_buff_runtime import finish_buff_instances

        uid = next(uid for uid, instance in self.world.native_buff_instances.items() if instance.owner == "2")
        finish_buff_instances(self.world, (uid,))
        self.assertEqual(self.result().non_crit, 350)
        self.assertGreater(self.result("1").non_crit, 350)

    def test_inherited_value_is_captured_once_and_fixed_flat_attack_is_not_percent_scaled(self):
        self.world.start(self.producer(), action_id="ult")
        old = self.result().non_crit
        for instance in self.world.native_buff_instances.values():
            self.world._action_inputs[instance.uid]["bb.atk"] = 9
        self.assertEqual(self.result().non_crit, old)
        spec = DamageModifierSpec("other", DamageBucket.ATTACK, ("all",), "self",
                                  ModifierMagnitude(.2), "confirmed", duration=10)
        self.world.damage_state.apply(spec, source_actor="2", now=0, event="confirmed")
        self.assertAlmostEqual(self.result().non_crit, old + 100 * .2 * 2)

    def test_dynamic_final_attributes_and_native_attack_share_one_attack_basis(self):
        basis = DamageAttributeBasis.from_dict({"schema_version": 2, "domain": "final_panel",
                "attack_conversion": "floor_final_main_sub",
                "primary": "力量", "secondary": "敏捷",
                "unverified_attack_dependencies": [],
                "totals": {"力量": 100, "敏捷": 250, "智识": 0, "意志": 0}})
        state = self.world.characters["2"]
        state.panel = replace(state.panel, attribute_basis=basis)
        self.world.start(self.producer(), action_id="ult")
        self.world.damage_state.apply_final_attribute_delta(actor="2", key="str", source="confirmed",
                    attribute="力量", amount=20, now=0, duration=1)
        self.assertEqual(self.world.effective_attributes("2")["ATK"], self.result().non_crit)
        self.world.advance(1)
        self.assertEqual(self.world.effective_attributes("2")["ATK"], self.result().non_crit)
        self.assertGreater(self.result().non_crit, 350)

    def test_missing_captured_native_input_does_not_fall_back_to_fixed_panel(self):
        self.world.start(self.producer(), action_id="ult")
        instance = next(instance for instance in self.world.native_buff_instances.values() if instance.owner == "2")
        instance.attack_addition = None
        self.assertIsNone(self.result().non_crit)
        self.assertNotIn("ATK", self.world.effective_attributes("2"))

    def test_unreviewed_or_changed_attribute_semantics_remain_unbound(self):
        additions, bound = compile_attack_additions(AKEKURI_TEAM_ATTACK, self.buff)
        self.assertTrue(bound)
        self.assertEqual(len(additions), 1)
        self.assertFalse(compile_attack_additions("other", self.buff)[1])
        for field, value in (("formulaItem", 5), ("attributeType", 41), ("modifyAttributeType", 1)):
            changed = copy.deepcopy(self.buff)
            changed["attributeModifier"]["attributeModifiers"][0][field] = value
            self.assertFalse(compile_attack_additions(AKEKURI_TEAM_ATTACK, changed)[1])
        changed = copy.deepcopy(self.buff)
        changed["attributeModifier"]["isConvertedAttribute"] = True
        self.assertFalse(compile_attack_additions(AKEKURI_TEAM_ATTACK, changed)[1])

    def test_audit_keeps_native_p3_ownership_separate_and_respects_selection(self):
        self.assertIsNone(reviewed_attack_binding(get_character("akekuri", potential=2), self.store))
        binding = reviewed_attack_binding(get_character("akekuri"), self.store)
        self.assertEqual(binding["passive_id"], "chr_0019_karin_potential_3")
        self.assertEqual(binding["buff_id"], AKEKURI_TEAM_ATTACK)
        self.assertEqual(binding["sources"]["producer_sha256"], self.source["source"]["sha256"])
        self.assertEqual(binding["evaluation"], "instance_creation_snapshot")
