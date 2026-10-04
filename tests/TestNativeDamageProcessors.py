"""Native defender zones use scoped values and actual target/lifetime boundaries."""

import copy
import struct
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression
from src.data.combat_model import EnemyCombatState
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState, NativeBuffChange, NativeTarget
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_resolution import DamageHit, FixedDamagePanel
from src.data.native_buff_program import compile_buff_definition
from src.data.native_damage_processors import compile_damage_processors
from src.data.native_gameplay import native_asset, native_enums, native_record
from src.data.skill_timing import SkillTimingStore


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


class TestNativeDamageProcessors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]
        cls.character = get_character("perlica")
        cls.data = native_record(cls.store, "buff_common_pulse_fire_triggered")["data"]

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.world.enemies["other"] = EnemyCombatState()
        for actor in self.world.characters:
            self.world.characters[actor].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        self.world.characters["1"].attributes["native.final_nonconverted.57"] = .25
        self.serial = 0

    def definition(self, key="buff_common_pulse_fire_triggered", value=.2, duration=2, *, data=None):
        data = data if data is not None else native_record(self.store, key)["data"]
        reference = {"assignBlackboard": True, "assignItems": [
            {"directValueType": 0, "targetKey": k, "numericValue": v, "useDirectValue": True}
            for k, v in (("spell_resistance_decrease", value), ("duration", duration))]}
        return compile_buff_definition(self.store, self.character, self.profile, "1", key, data, reference,
                                       attributes={}, panel=None, path=())

    def add(self, definition=None, key="buff_common_pulse_fire_triggered", *, enemy="target", world=None):
        world = world or self.world
        definition = definition or self.definition(key)
        change = NativeBuffChange(key, CombatExpression("literal", (1,)), selector=NativeTarget("action_target"), definition=definition)
        self.serial += 1
        program = ActionProgram(key, "1", "normal", 0, 0, 0, (CombatEvent(0, "add", native_buffs=(change,)),), enemy=enemy)
        self.assertTrue(world.start(program, action_id=f"add:{self.serial}"))
        return next(i for i in world.native_buff_instances.values() if i.key == key and i.owner == enemy)

    def hit(self, element, *, enemy="target", actor="1", world=None, bonus=0):
        world = world or self.world
        self.serial += 1
        program = ActionProgram("hit", actor, "normal", 0, 0, 0,
                                (CombatEvent(0, "hit", hit=DamageHit(actor, enemy, element, 1, bonus)),), enemy=enemy)
        before = world.damage
        self.assertTrue(world.start(program, action_id=f"hit:{self.serial}"))
        return world.damage - before

    def test_actual_conduct_producer_affects_all_four_spell_types_and_other_attackers(self):
        instance = self.add()
        value = single(self.world._action_inputs[instance.uid]["bb.final_spell_resistance_decrease"])
        self.assertEqual(len(instance.definition.damage_scales), 4)
        self.assertFalse(any("damage modifier" in d for d in instance.definition.unresolved))
        for element in ("灼热", "电磁", "寒冷", "自然"):
            self.assertAlmostEqual(self.hit(element, actor="2", bonus=.3), 130 * (1 + value))
        self.assertEqual(self.hit("物理"), 100)
        self.assertEqual(self.hit("电磁", enemy="other"), 100)

    def test_snapshot_is_retained_and_modifier_reads_its_own_blackboard_at_hit(self):
        instance = self.add()
        captured = single(self.world._action_inputs[instance.uid]["bb.final_spell_resistance_decrease"])
        self.world.characters["1"].attributes["native.final_nonconverted.57"] = 9
        self.assertAlmostEqual(self.hit("电磁"), 100 * (1 + captured))
        self.world._action_inputs[instance.uid]["bb.final_spell_resistance_decrease"] = .123456789
        self.assertEqual(self.hit("电磁"), 100 * (1 + single(.123456789)))

    def test_expiry_and_shared_replacement_remove_the_old_modifier(self):
        old = self.add(self.definition(value=.2))
        new = self.add(self.definition("buff_common_pulse_cryst_triggered", value=.5), "buff_common_pulse_cryst_triggered")
        self.assertNotIn(old.uid, self.world.native_buff_instances)
        value = single(self.world._action_inputs[new.uid]["bb.final_spell_resistance_decrease"])
        self.assertAlmostEqual(self.hit("电磁"), 100 * (1 + value))
        self.world.advance(2)
        self.assertEqual(self.hit("电磁"), 100)

    def test_multiple_buffs_add_in_one_defender_zone_and_merge_with_existing_taken_bonus(self):
        base = self.definition()
        a = replace(base, callbacks=(), subscriptions=(), unresolved=(), stacking_key=None,
                    parameters=(("bb.final_spell_resistance_decrease", .2),), inherited=(),
                    duration=CombatExpression("literal", (2,)))
        b = replace(a, parameters=(("bb.final_spell_resistance_decrease", .3),))
        self.add(a, "a")
        self.add(b, "b")
        spec = DamageModifierSpec("taken", DamageBucket.DAMAGE_TAKEN, ("all",), "enemy", ModifierMagnitude(.4),
                                  "gain", duration=2)
        self.world.damage_state.apply(spec, source_actor="1", enemy="target", now=0, event="gain")
        self.assertAlmostEqual(self.hit("电磁", bonus=.5), 150 * (1 + single(.2) + single(.3) + .4))
        self.assertEqual(self.hit("物理"), 140)

    def test_missing_saved_value_blocks_only_matching_hits_and_never_uses_fallback(self):
        instance = self.add()
        self.world._action_inputs[instance.uid].pop("bb.final_spell_resistance_decrease")
        self.assertEqual(self.hit("物理"), 100)
        self.assertEqual(self.hit("电磁"), 0)
        self.assertTrue(any("Native damage modifier input" in d for d in self.world.unresolved))

    def test_forecast_expiry_and_blackboard_edits_do_not_mutate_live_world(self):
        instance = self.add()
        predicted = self.world.fork()
        predicted._action_inputs[instance.uid]["bb.final_spell_resistance_decrease"] = .7
        self.assertAlmostEqual(self.hit("寒冷", world=predicted), 100 * (1 + single(.7)))
        predicted.advance(2)
        self.assertTrue(self.world.native_buff_instances)
        self.assertEqual(self.world.time, 0)
        self.assertEqual(self.world.damage, 0)
        self.assertNotEqual(self.world._action_inputs[instance.uid]["bb.final_spell_resistance_decrease"], .7)

    def test_unknown_sides_zones_conditions_processors_remain_explicit(self):
        for field, value in (("side", 0), ("zoneName", "ProdCalcZone"), ("zoneName", "missing")):
            data = copy.deepcopy(self.data)
            data["damageModifier"] = [data["damageModifier"][0]]
            data["damageModifier"][0]["damageProcessors"][0]["$value"][field] = value
            scales, unknown = compile_damage_processors(data, "test")
            self.assertFalse(scales)
            self.assertTrue(unknown)
        for change in ("source_guard", "multiple_conditions", "other_processor", "enabled_side"):
            data = copy.deepcopy(self.data)
            data["damageModifier"] = [data["damageModifier"][0]]
            modifier = data["damageModifier"][0]
            if change == "source_guard":
                modifier["condition"]["onlyExecuteWhenSourceIsGuard"] = True
            elif change == "multiple_conditions":
                modifier["condition"]["actionData"] *= 2
            elif change == "other_processor":
                modifier["damageProcessors"][0]["$type"] = "Unknown"
            else:
                modifier["enableSide"] = 0
            scales, unknown = compile_damage_processors(data, "test")
            self.assertFalse(scales)
            self.assertTrue(unknown)

    def test_zero_clamp_and_raw_native_configuration_evidence(self):
        base = self.definition()
        negative = replace(base, callbacks=(), subscriptions=(), unresolved=(),
                           parameters=(("bb.final_spell_resistance_decrease", -2),), inherited=(),
                           duration=CombatExpression("literal", (2,)))
        self.add(negative)
        self.assertEqual(self.hit("电磁"), 0)
        zones = {r["name"]: r for r in native_asset("DamageScaleProcessorConfig")["allZones"]}
        self.assertFalse(zones["NormalCalcZone"]["isMultiplyZone"])
        self.assertFalse(zones["NormalCalcZone"]["mergeAttackerAndDefender"])
        for name in ("Beyond.Gameplay.Core.DamageScaleProcessor+DamageScaleSide", "Beyond.Gameplay.Core.DamageModifier+Data+EnableSide"):
            row = native_enums()[name]["Defender"]
            self.assertEqual(row["value"], 1)
            self.assertEqual(bytes.fromhex(row["raw_hex"])[0] >> 1, 1)


if __name__ == "__main__":
    unittest.main()
