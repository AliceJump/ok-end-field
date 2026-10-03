"""Execute client-derived mechanics and test conditional safety with native trees."""

import copy
import unittest

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression
from src.data.combat_runtime import CombatRuntime
from src.data.combat_simulation import ActionProgram, CombatEvent, NativeResourceChange, NativeTarget
from src.data.damage_resolution import FixedDamagePanel
from src.data.effects import EffectType
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_reactions import reaction_enhancement
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import CombatResourceType, SkillEffect


class TestNativeCombatPrograms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.catalog = build_combat_catalog(("弭弗", "骏卫", "余烬"), cls.store)

    def setUp(self):
        self.world = self.catalog.world.fork()
        self.world.regen = 0
        self.base, self.follow, self.finish = self.catalog.candidates("1", "battle")

    def follow_with_shred(self, layers):
        self.assertTrue(self.world.start(self.base, action_id="base"))
        self.world.advance(self.world.ready_at(self.follow))
        self.world.add_shred("target", layers)
        self.assertTrue(self.world.start(self.follow, action_id="follow"))
        self.world.advance(3)

    def test_mifu_native_cost_refund_and_actual_three_layer_consumption_unlock(self):
        self.assertEqual((self.base.sp_cost, self.follow.sp_cost, self.finish.sp_cost), (100, 50, 50))
        self.follow_with_shred(3)
        self.assertEqual(self.world.sp, 200)
        self.assertEqual(self.world.count("1", "target", EffectType.STACK_SHRED), 0)
        self.assertEqual(self.world._action_consumed["follow"]["1", EffectType.STACK_SHRED], 3)
        self.assertEqual(self.world.count("1", "target", EffectType.STATUS_MIFU_KAITIAN_READY), 1)
        self.assertTrue(self.world.start(self.finish, action_id="finish"))
        self.world.advance(4)
        self.assertEqual(self.world.sp, 150)
        self.assertEqual(self.world.count("1", "target", EffectType.STATUS_MIFU_KAITIAN_READY), 0)
        # Other unresolved geometry/state primitives are reported explicitly.
        self.assertTrue(self.world.unresolved)

    def test_returned_sp_pool_is_spent_before_fresh_sp_and_energy_uses_cast_snapshot(self):
        self.world.start(self.base, action_id="base")
        self.assertEqual(self.world.returned_sp, 50)
        self.assertEqual(self.world._action_inputs["base"]["cast.non_returned_sp"], 100)
        self.world.advance(self.world.ready_at(self.follow))
        energy = {a: c.energy for a, c in self.world.characters.items()}
        for actor, character in self.world.characters.items():
            self.assertAlmostEqual(energy[actor], 6.5 * (1 + character.attributes["energy_gain"]), places=5)
        self.world.start(self.follow, action_id="follow")
        self.assertEqual(self.world.returned_sp, 0)
        self.assertEqual(self.world._action_inputs["follow"]["cast.non_returned_sp"], 0)
        self.world.advance(3)
        self.assertEqual({a: c.energy for a, c in self.world.characters.items()}, energy)

    def resource_program(self, *, amount, coefficient=1, target="main", percent=False, ignore=False,
                         resource=CombatResourceType.ULTIMATE_ENERGY, returned=False, source="source", only_main=False):
        literal = lambda value: CombatExpression("literal", (float(value),))
        change = NativeResourceChange(resource, literal(amount), literal(coefficient), NativeTarget(source),
                                      NativeTarget(target), percent=percent, ignore_energy_gain=ignore,
                                      returned_sp=returned, only_main_source=only_main)
        return ActionProgram("resource", "1", "normal", 0, 0, 0,
                             (CombatEvent(0, "resource", native_resources=(change,)),))

    def test_energy_recipient_capacity_and_gain_are_applied_before_coefficient(self):
        self.world.main_control = "2"
        self.world.characters["1"].attributes["energy_gain"] = 9
        self.world.characters["2"].attributes["energy_gain"] = .5
        self.world.characters["2"].energy_cap = 120
        self.world.start(self.resource_program(amount=.1, coefficient=2, percent=True), action_id="percent")
        self.assertAlmostEqual(self.world.characters["2"].energy, 36)
        self.assertEqual(self.world.characters["1"].energy, 0)
        self.world.start(self.resource_program(amount=10, coefficient=-1), action_id="negative_coefficient")
        self.assertAlmostEqual(self.world.characters["2"].energy, 21)
        self.world.start(self.resource_program(amount=-10, coefficient=-1), action_id="negative_base")
        self.assertAlmostEqual(self.world.characters["2"].energy, 31)
        self.world.start(self.resource_program(amount=10, ignore=True), action_id="ignore_gain")
        self.assertAlmostEqual(self.world.characters["2"].energy, 41)

    def test_return_pool_uses_credited_gain_and_normal_gain_does_not_join_it(self):
        self.world.sp = 290
        gain = self.resource_program(amount=50, target="source", resource=CombatResourceType.SKILL_POINT, returned=True)
        self.world.start(gain, action_id="overflow_return")
        self.assertEqual((self.world.sp, self.world.returned_sp, self.world.sp_overflow), (300, 10, 40))
        self.world.start(ActionProgram("cost", "1", "battle", 100, 0, 0, ()), action_id="spend")
        self.assertEqual(self.world._action_inputs["spend"]["cast.non_returned_sp"], 90)
        ordinary = self.resource_program(amount=50, target="source", resource=CombatResourceType.SKILL_POINT)
        self.world.start(ordinary, action_id="gain")
        self.assertEqual((self.world.sp, self.world.returned_sp), (250, 0))

    def test_sp_only_main_checks_resource_source_and_enemy_is_not_energy_recipient(self):
        self.world.sp = 100
        self.world.main_control = "2"
        p = self.resource_program(amount=20, target="source", resource=CombatResourceType.SKILL_POINT, only_main=True)
        self.world.start(p, action_id="off_main")
        self.assertEqual(self.world.sp, 100)
        p = self.resource_program(amount=20, source="main", target="source",
                                  resource=CombatResourceType.SKILL_POINT, only_main=True)
        self.world.start(p, action_id="main_source")
        self.assertEqual(self.world.sp, 120)
        p = self.resource_program(amount=20, target="action_target")
        self.world.start(p, action_id="enemy_target")
        self.assertTrue(all(c.energy == 0 for c in self.world.characters.values()))

    def test_default_energy_separates_source_and_other_coefficients(self):
        literal = lambda value: CombatExpression("literal", (float(value),))
        change = NativeResourceChange(CombatResourceType.ULTIMATE_ENERGY,
                                      CombatExpression("input", ("cast.non_returned_sp",)), literal(2),
                                      NativeTarget("source"), NativeTarget("squad"), default_energy=(.1, .05))
        p = ActionProgram("default", "1", "battle", 100, 0, 0,
                          (CombatEvent(0, "default", native_resources=(change,)),))
        for c in self.world.characters.values():
            c.attributes["energy_gain"] = 0
        self.world.returned_sp = 40
        self.world.start(p, action_id="default")
        self.assertEqual([c.energy for c in self.world.characters.values()], [12, 6, 6])

    def test_two_layers_do_not_unlock_and_zero_layers_only_create_first_shred(self):
        self.follow_with_shred(2)
        self.assertEqual(self.world.count("1", "target", EffectType.STATUS_MIFU_KAITIAN_READY), 0)
        self.assertFalse(self.world.start(self.finish, action_id="finish"))
        self.setUp()
        self.follow_with_shred(0)
        self.assertEqual(self.world.count("1", "target", EffectType.STACK_SHRED), 1)
        self.assertEqual(self.world.count("1", "target", EffectType.STATUS_MIFU_KAITIAN_READY), 0)

    def test_ready_state_and_physical_pool_expire_at_native_deadline(self):
        self.world.start(self.base, action_id="base")
        self.world.advance(15)
        self.assertEqual(self.world.count("1", "target", EffectType.STATUS_MIFU_ZHUIXING_READY), 0)
        self.world.add_shred("target", 2)
        self.world.advance(34)
        self.world.add_shred("target", 1)
        self.world.advance(35)
        self.assertEqual(self.world.count("1", "target", EffectType.STACK_SHRED), 3)
        self.world.advance(54)
        self.assertEqual(self.world.count("1", "target", EffectType.STACK_SHRED), 0)
        self.assertEqual(self.world.count("1", "target", EffectType.EVENT_SHRED_CONSUMED), 0)

    def test_finish_reads_enhancement_and_uses_crush_not_battle_damage_bonus(self):
        self.follow_with_shred(3)
        self.world.start(self.finish, action_id="finish")
        self.world.advance(4)
        values = self.world._action_inputs["finish"]
        arts = self.world.characters["1"].attributes["arts_strength"]
        self.assertAlmostEqual(values["bb.yuanshi_multi"], 1 + reaction_enhancement("Damage", arts))
        hits = [e.hit for e in self.finish.events if e.hit]
        self.assertEqual(hits[-1].damage_tags, ("physical_anomaly",))
        self.assertFalse(hits[-1].can_crit)
        panel = self.world.characters["1"].panel
        self.assertAlmostEqual(hits[-1].damage_bonus, panel.bonus_for("物理", ("physical_anomaly",)))

    def test_native_blackboard_formula_is_used_instead_of_inactive_literal(self):
        p = self.catalog.candidates("2", "battle")[0]
        formulas = [e.hit_multiplier_formula for e in p.events if e.hit]
        self.assertTrue(formulas)
        inputs = dict(p.parameters)
        self.assertTrue(any(f.evaluate(inputs) != 2 for f in formulas))
        self.assertTrue(all("bb.atk_scale" in repr(f) for f in formulas))

    def compile_sequence(self, sequence):
        store = copy.copy(self.store)
        record = copy.deepcopy(store.record(self.base.key))
        record["data"]["actionGroupData"]["timelineActions"] = [{
            "_startFrame": 0, "_endFrame": 1, "_sequenceActionData": sequence,
        }]
        record["data"]["actionGroupData"]["passiveEventActions"] = []
        original = store.record
        store.record = lambda key: record if key == self.base.key else original(key)
        profile = self.store.battle_phase_profiles("弭弗")[0]
        return compile_native_action(store, get_character("mi_fu"), profile, "1", "battle")

    def test_unknown_plain_check_does_not_execute_its_following_resource_payout(self):
        root = copy.deepcopy(self.store.record(self.base.key)["data"]["actionGroupData"]["timelineActions"][0]["_sequenceActionData"])
        payout = next(n for n in _nodes(self.store.record(self.base.key)["data"])
                      if n["$type"].endswith("ObtainCostAction+Data"))
        root["actionData"] = [{"$type": "Beyond.Gameplay.CheckUnknown+Data", "$value": {
            "isEnable": True, "serverActionIndex": 999,
        }}, payout]
        p = self.compile_sequence(root)
        self.world.start(p, action_id="unknown")
        self.assertEqual(self.world.sp, 200)
        self.assertFalse(any(e.resources for e in p.events))
        self.assertTrue(self.world.unresolved)

    def test_fixed_bonuses_follow_element_and_hit_tags(self):
        panel = FixedDamagePanel(100, 0, 0, 1, 0, .5, damage_bonus={
            "all": .1, "all_skill": .2, "物理": .3, "skill": .4,
        })
        self.assertAlmostEqual(panel.bonus_for("物理", ("skill",)), 1)
        self.assertAlmostEqual(panel.bonus_for("电磁", ("skill",)), .7)
        self.assertAlmostEqual(panel.bonus_for("物理", ("physical_anomaly",)), .4)

    def test_squad_shred_consumer_creates_pogranichnik_pre_consumption_marker(self):
        self.world.add_shred("target", 4)
        self.world.apply_effect("1", "target", SkillEffect(
            EffectType.STATUS_HEAVY_STRIKE, count=1, target="enemy"), {})
        self.assertEqual(self.world.enemies["target"].shred_stacks, 0)
        marker = "buff_chr_0029_pograni_combo_skill_count4"
        self.assertEqual(self.world.native_buffs["2", marker], (1, 6))
        self.assertEqual(self.world.characters["2"].blackboard["EntityBB_noguard_count"], 4)
        self.assertNotIn("EntityBB_noguard_count", self.world.characters["1"].blackboard)
        self.world.sp = 100
        combo = self.catalog.candidates("2", "link")[0]
        self.assertTrue(self.world.start(combo, action_id="pograni_link"))
        self.world.advance(4)
        self.assertEqual(self.world.sp, 135)
        self.assertEqual(self.world.enemies["target"].shred_stacks, 0)

    def test_each_pre_consumption_tier_uses_its_own_marker_and_native_expiry(self):
        for count in range(1, 5):
            with self.subTest(count=count):
                world = self.catalog.world.fork()
                world.add_shred("target", count)
                world.dispatch_character_event("OnBeforeAddedBuff", "1", "target", "buff_physical_crushed")
                marker = f"buff_chr_0029_pograni_combo_skill_count{count}"
                self.assertEqual(world.native_buffs, {("2", marker): (1, 6)})
                self.assertEqual(world.characters["2"].blackboard["EntityBB_noguard_count"], count)
                world.advance(6)
                self.assertNotIn(("2", marker), world.native_buffs)

    def test_unrelated_buff_wrong_target_and_empty_shred_do_not_create_marker(self):
        for buff_id, target, count in (("buff_physical_crushed", "target", 0),
                                      ("buff_common_hp_recovery", "target", 4),
                                      ("buff_physical_crushed", "1", 4)):
            with self.subTest(buff_id=buff_id, target=target, count=count):
                world = self.catalog.world.fork()
                world.add_shred("target", count)
                world.dispatch_character_event("OnBeforeAddedBuff", "1", target, buff_id)
                self.assertFalse(world.native_buffs)
                self.assertNotIn("EntityBB_noguard_count", world.characters["2"].blackboard)

    def test_rejected_native_cast_cannot_publish_teammate_combo_marker(self):
        from dataclasses import replace

        catalog = replace(self.catalog, world=self.world)
        runtime = CombatRuntime(catalog, epoch=0)
        self.world.start(self.base, action_id="base")
        self.world.advance(self.world.ready_at(self.follow))
        self.world.add_shred("target", 4)
        self.assertTrue(runtime.stage(self.follow, self.world.time))
        runtime.pending.predicted.advance(3)
        self.assertIn(("2", "buff_chr_0029_pograni_combo_skill_count4"), runtime.pending.predicted.native_buffs)
        runtime.cancel()
        self.assertNotIn(("2", "buff_chr_0029_pograni_combo_skill_count4"), self.world.native_buffs)
        self.assertNotIn("EntityBB_noguard_count", self.world.characters["2"].blackboard)
        self.assertEqual(self.world.enemies["target"].shred_stacks, 4)


if __name__ == "__main__":
    unittest.main()
