"""Cross-element producers retain consumed layers and native setting outputs."""

import struct
import unittest
from dataclasses import replace

from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import CombatWorldState, NativeTarget, walk_combat_events
from src.data.damage_resolution import FixedDamagePanel
from src.data.native_gameplay import native_asset
from src.data.native_reactions import reaction_enhancement
from src.data.native_spell_runtime import SPELL_ELEMENTS


class TestNativeCrossSpell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.TestNativeSpellBurst import TestNativeSpellBurst

        TestNativeSpellBurst.setUpClass()
        cls.programs = TestNativeSpellBurst.programs
        cls.rows = {r["key"]: r for r in native_asset("SkillSetting")["spellInflictionDataList"]}

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.enemies["other"] = EnemyCombatState()
        self.world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        self.world.characters["1"].attributes.update(arts_strength=50, ignite_damage_scalar=1.4)
        self.serial = 0

    def row(self, key, layers, arts=50):
        row = self.rows[key]
        value = row["values"][layers - 1]
        return value * (1 + reaction_enhancement(row["enhanceFormulaKey"], arts)) if row["enhanceFormulaKey"] else value

    def cast(self, new, old, layers, *, enemy="target", program=None):
        state = self.world.enemies[enemy]
        state.infliction_element = SPELL_ELEMENTS[old][1]
        state.infliction_stacks = layers
        state.infliction_time_left = 20
        self.serial += 1
        self.world.start(replace(program or self.programs[new], enemy=enemy), action_id=f"cross:{self.serial}")

    def instance(self, key):
        return next(v for v in self.world.native_buff_instances.values() if v.key == key)

    def test_all_twelve_pairs_and_four_consumed_levels_read_actual_rows(self):
        for new, (new_name, _) in SPELL_ELEMENTS.items():
            for old, (old_name, _) in SPELL_ELEMENTS.items():
                if old == new:
                    continue
                for layers in range(1, 5):
                    with self.subTest(new=new, old=old, layers=layers):
                        self.setUp()
                        self.cast(new, old, layers)
                        entry = self.instance(f"buff_common_try_{new_name}_{old_name}_triggered")
                        values = self.world._action_inputs[entry.uid]
                        self.assertEqual((values["bb.consumed_type"], values["bb.consumed_layer"], values["bb.count"]),
                                         (old, layers, layers))
                        self.assertAlmostEqual(values["bb.atk_scale"], self.row("异常初始伤害倍率", layers))
                        self.assertAlmostEqual(self.world.damage, 100 * self.row("异常初始伤害倍率", layers) * 1.4)
                        self.assertEqual(self.world.enemies["target"].infliction_stacks, 0)
                        self.assertIsNone(self.world.enemies["target"].infliction_element)
                        self.assertAlmostEqual(entry.expires, .1, places=6)
                        self.assertFalse(any("Unbound spell reaction" in error for error in self.world.unresolved))
                        self.world.advance(.11)
                        self.assertNotIn(entry.uid, self.world.native_buff_instances)

    def test_duration_and_secondary_parameters_are_inherited_from_native_producer(self):
        for new, output_keys in ((0, {"bb.burning_atk_scale": "燃烧每跳伤害"}),
                                 (1, {"bb.spell_resistance_decrease": "导电法术伤害提高", "bb.duration": "导电持续时间"}),
                                 (2, {"bb.shatter_dmg": "碎冰倍率", "bb.duration": "冰冻持续时间"}),
                                 (3, {"bb.def_decrease_tick": "腐蚀每跳减抗", "bb.max_def_decrease": "腐蚀减抗上限",
                                      "bb.start_def_decrease": "腐蚀初始减抗", "bb.duration": "腐蚀持续时间"})):
            with self.subTest(new=new):
                self.setUp()
                old = (new + 1) % 4
                self.cast(new, old, 3)
                key = f"buff_common_{SPELL_ELEMENTS[new][0]}_{SPELL_ELEMENTS[old][0]}_triggered"
                instance = self.instance(key)
                values = self.world._action_inputs[instance.uid]
                for output, table_key in output_keys.items():
                    self.assertAlmostEqual(values[output], self.row(table_key, 3))
                self.assertAlmostEqual(instance.expires, values["bb.duration"])

    def test_new_reaction_replaces_other_entry_in_shared_group_with_new_parameters(self):
        self.cast(1, 0, 1)
        previous = self.instance("buff_common_pulse_fire_triggered")
        self.world.advance(2)
        self.cast(1, 2, 3)
        current = self.instance("buff_common_pulse_cryst_triggered")
        self.assertNotIn(previous.uid, self.world.native_buff_instances)
        self.assertNotIn(("target", previous.key), self.world.native_buffs)
        self.assertEqual(self.world._action_inputs[current.uid]["bb.consumed_layer"], 3)
        self.assertAlmostEqual(current.expires, 2 + self.row("导电持续时间", 3))

    def test_actual_conduct_and_freeze_capture_explicit_source_abnormal_attributes(self):
        for new, attribute_id, output in (
            (1, 57, "bb.final_spell_resistance_decrease"),
            (2, 58, "bb.final_phy_dmg_up"),
        ):
            with self.subTest(new=new):
                self.setUp()
                self.world.characters["1"].attributes[f"native.final_nonconverted.{attribute_id}"] = .4
                self.cast(new, 0, 3)
                instance = self.instance(f"buff_common_{SPELL_ELEMENTS[new][0]}_fire_triggered")
                values = self.world._action_inputs[instance.uid]
                base_key = "bb.spell_resistance_decrease" if new == 1 else "bb.phy_dmg_up"
                base = struct.unpack("<f", struct.pack("<f", values[base_key]))[0]
                self.assertAlmostEqual(values[output], base * 1.4)
                captured = values[output]
                self.world.characters["1"].attributes[f"native.final_nonconverted.{attribute_id}"] = .8
                self.world.advance(.2)
                self.assertEqual(self.world._action_inputs[instance.uid][output], captured)
                self.cast(new, 0, 3, enemy="other")
                other = next(v for v in self.world.native_buff_instances.values() if v.key == instance.key and v.owner == "other")
                self.assertAlmostEqual(self.world._action_inputs[other.uid][output], base * 1.8)
                self.assertTrue(self.world.unresolved)  # Final modifier/lifetime gaps remain explicit.

    def test_setting_capture_is_per_instance_and_prediction_does_not_touch_original(self):
        actual = self.world
        self.world = actual.fork()
        self.cast(1, 0, 2)
        first = self.instance("buff_common_pulse_fire_triggered")
        captured = self.world._action_inputs[first.uid]["bb.atk_scale"]
        self.world.characters["1"].attributes["arts_strength"] = 200
        self.cast(1, 0, 2, enemy="other")
        second = next(v for v in self.world.native_buff_instances.values() if v.key == first.key and v.owner == "other")
        self.assertAlmostEqual(captured, self.row("异常初始伤害倍率", 2))
        self.assertAlmostEqual(self.world._action_inputs[second.uid]["bb.atk_scale"], self.row("异常初始伤害倍率", 2, 200))
        self.assertEqual(self.world._action_inputs[first.uid]["bb.atk_scale"], captured)
        self.assertEqual(actual.damage, 0)
        self.assertFalse(actual.native_buff_instances)

    def test_missing_strength_or_invalid_table_level_never_uses_serialized_zero(self):
        self.world.characters["1"].attributes.pop("arts_strength")
        self.cast(1, 0, 2)
        self.assertEqual(self.world.damage, 0)
        self.assertNotIn(("target", "buff_common_pulse_fire_triggered"), self.world.native_buffs)
        self.assertTrue(any("native.attribute" in error for error in self.world.unresolved))
        self.setUp()
        self.cast(1, 0, 99)
        self.assertEqual(self.world.damage, 0)
        self.assertTrue(any("table index" in error for error in self.world.unresolved))

    def test_different_actual_source_and_unknown_nested_work_remain_explicit(self):
        program = self.programs[1]
        event = program.events[0]
        change = replace(event.native_spells[0], source=NativeTarget("main"))
        program = replace(program, events=(replace(event, native_spells=(change,)),))
        self.world.main_control = "2"
        self.cast(1, 0, 2, program=program)
        self.assertEqual(self.world.damage, 0)
        self.assertFalse(self.world.native_buff_instances)
        self.assertIn("Unbound spell reaction: STATUS_CONDUCTING", self.world.unresolved)
        diagnostics = [error for e in walk_combat_events(self.programs[1].events) for error in e.unresolved]
        fire_diagnostics = [error for e in walk_combat_events(self.programs[0].events) for error in e.unresolved]
        self.assertTrue(any("child/action-bound" in error for error in fire_diagnostics))
        self.assertFalse(any("StoreAttributeValue" in error for error in diagnostics))
        self.assertTrue(any("Native buff execution" in error for error in diagnostics))


if __name__ == "__main__":
    unittest.main()
