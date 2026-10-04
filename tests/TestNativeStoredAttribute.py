"""Native attribute snapshots preserve roles, non-converted modes and arithmetic."""

import copy
import struct
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, MissingCombatInput
from src.data.combat_simulation import CombatWorldState, NativeTarget, walk_combat_events
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_enums, native_record
from src.data.skill_timing import SkillTimingStore


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


class TestNativeStoredAttribute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]
        cls.character = get_character("perlica")
        cls.nodes = {}
        for attr, key in ((57, "buff_common_pulse_fire_triggered"), (58, "buff_common_cryst_fire_triggered")):
            data = native_record(cls.store, key)["data"]
            cls.nodes[attr] = next(n for n in _nodes(data) if n["$type"].endswith(".StoreAttributeValue+Data"))

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)

    def program(self, attr=57, *, target=1, mode=1, floor=False, divisor=1, primary=0, multiplier=2, base=3):
        node = copy.deepcopy(self.nodes[57 if attr not in self.nodes else attr])
        body = node["$value"]
        body.update(attributeType=attr, primaryAttributeType=primary, storeAttributeType=mode, useFloor=floor, key="stored")
        body["targetSettings"]["targetSource"] = target
        for key, value in (("multiplierValue", multiplier), ("baseValue", base), ("divisorValue", divisor)):
            body[key].update(useBlackboardKey=False, value=value)
        if not floor:
            # Deliberately missing reference proves the inactive divisor is ignored.
            body["divisorValue"].update(useBlackboardKey=True, blackboardKey="unused_missing_divisor")
        program = compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                        event_sequence={"actionData": [node]})
        return replace(program, sp_cost=0, energy_cost=0, gate=None, duration=.1, cooldown=0,
                       events=tuple(replace(e, at=.1) for e in program.events))

    def execute(self, program, targets=None, *, initial=None):
        self.assertTrue(self.world.start(program, action_id="store"))
        self.world._action_targets["store"].update(targets or {})
        self.world._action_inputs["store"].update(initial or {})
        self.world.advance(.1)
        return self.world._action_inputs["store"]

    def test_actual_source_and_owner_are_distinct_at_snapshot_time(self):
        self.world.characters["1"].attributes["native.final_nonconverted.57"] = .1
        self.world.characters["2"].attributes["native.final_nonconverted.57"] = .4
        values = self.execute(self.program(), {"source": ("2",), "owner": ("1",)})
        self.assertAlmostEqual(values["bb.stored"], 3.8)
        self.assertFalse(self.world.unresolved)
        self.setUp()
        self.world.characters["2"].attributes["native.final_nonconverted.57"] = .6
        values = self.execute(self.program(target=4), {"source": ("1",), "owner": ("2",)})
        self.assertAlmostEqual(values["bb.stored"], 4.2)
        self.assertFalse(self.world.unresolved)

    def test_main_is_live_and_armed_does_not_alias_final_or_panel(self):
        self.world.characters["2"].attributes.update({"native.armed_nonconverted.58": 4, "native.final_nonconverted.58": 100, "ATK": 999})
        program = self.program(58, target=5, mode=0)
        self.world.start(program, action_id="store")
        self.world.main_control = "2"
        self.world.advance(.1)
        self.assertEqual(self.world._action_inputs["store"]["bb.stored"], 11)
        self.assertEqual(program.native_attribute_queries[0].attribute, "native.armed_nonconverted.58")
        self.assertEqual(program.native_attribute_queries[0].target, NativeTarget("main"))

    def test_native_floor_occurs_before_multiplier_and_base_and_uses_single_inputs(self):
        for value in (7.9, -7.9):
            with self.subTest(value=value):
                self.setUp()
                self.world.characters["1"].attributes["native.final_nonconverted.57"] = value
                values = self.execute(self.program(floor=True, divisor=3, multiplier=.123456789, base=.234567891))
                expected = single(.234567891) + (2 if value > 0 else -3) * single(.123456789)
                self.assertEqual(values["bb.stored"], expected)
                self.assertFalse(self.world.unresolved)

    def test_missing_values_invalidate_serialized_or_previously_saved_output(self):
        self.world.characters["1"].attributes.update(arts_strength=999, ATK=1000)
        values = self.execute(self.program(), initial={"bb.stored": 0})
        self.assertNotIn("bb.stored", values)
        self.assertTrue(any("native.attribute" in error for error in self.world.unresolved))
        self.setUp()
        self.world.characters["1"].attributes["native.final_nonconverted.57"] = float("nan")
        values = self.execute(self.program(), initial={"bb.stored": 4})
        self.assertNotIn("bb.stored", values)

    def test_missing_target_enemy_or_multiple_targets_do_not_choose_a_character(self):
        for targets in ((), ("target",), ("1", "2")):
            with self.subTest(targets=targets):
                self.setUp()
                self.world.characters["1"].attributes["native.final_nonconverted.57"] = 2
                values = self.execute(self.program(target=4), {"owner": targets})
                self.assertNotIn("bb.stored", values)
                self.assertTrue(self.world.unresolved)

    def test_zero_divisor_overflow_unknown_modes_and_primary_selection_stay_explicit(self):
        self.world.characters["1"].attributes["native.final_nonconverted.57"] = 3
        values = self.execute(self.program(floor=True, divisor=0))
        self.assertNotIn("bb.stored", values)
        self.assertIn("Zero combat formula divisor", self.world.unresolved)
        with self.assertRaises(MissingCombatInput):
            CombatExpression("float32", (1e100,)).evaluate({})
        for kwargs, text in (({"mode": 99}, "store mode"), ({"attr": 999}, "stored attribute"),
                             ({"primary": 1}, "primary/sub/all")):
            with self.subTest(kwargs=kwargs):
                diagnostics = [d for e in walk_combat_events(self.program(**kwargs).events) for d in e.unresolved]
                self.assertTrue(any(text in d for d in diagnostics))

    def test_native_enum_identities_retain_raw_metadata_evidence(self):
        enums = native_enums()
        for name, value in (("PulseAbnormalDamageIncrease", 57), ("CrystAbnormalDamageIncrease", 58)):
            row = enums["Beyond.GEnums.AttributeType"][name]
            self.assertEqual(row["value"], value)
            self.assertEqual(bytes.fromhex(row["raw_hex"])[0] >> 1, value)
        stores = enums["Beyond.Gameplay.Core.StoreAttributeValue+StoreAttributeType"]
        self.assertEqual(stores["BaseNonConverted"]["value"], 0)
        self.assertEqual(stores["FinalNonConverted"]["value"], 1)


if __name__ == "__main__":
    unittest.main()
