"""Native replacement handles restore authored skills and cooldown fractions."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeSkillChange,
    NativeTarget,
)
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


def literal(value):
    return CombatExpression("literal", (float(value),))


class TestNativeSkillReplacement(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.base = ActionProgram("base", "1", "link", 0, 0, 20, (), native_slot=1)
        self.first = replace(self.base, key="first", cooldown=10, native_requires_override=True)
        self.second = replace(self.first, key="second", cooldown=40)
        for program in (self.base, self.first, self.second):
            self.world.register_native_program(program, default=program == self.base)

    def change(self, *, target="first", life=1, duration=0, inherit=False, revert=None, action="change", length=0):
        change = NativeSkillChange(1, target, NativeTarget("source"), life, literal(duration), inherit, revert)
        program = ActionProgram(action, "1", "normal", 0, length, 0,
                                (CombatEvent(0, "change", skill_changes=(change,)),))
        self.assertTrue(self.world.start(program, action_id=action))

    def test_variants_are_not_legal_before_native_change_and_timer_restores_explicit_skill(self):
        self.assertFalse(self.world.start(self.first, action_id="reject"))
        self.assertFalse(self.world._started_ids)
        self.change(life=0, duration=6, revert="base")
        self.assertTrue(self.world.selects_program(self.first))
        self.assertFalse(self.world.selects_program(self.base))
        self.world.advance(5.99)
        self.assertEqual(self.world.selected_native_skill("1", 1), "first")
        self.world.advance(6)
        self.assertTrue(self.world.selects_program(self.base))
        self.assertFalse(self.world.native_skill_overrides)

    def test_second_change_clears_old_handle_and_captures_current_reversion(self):
        self.change(life=0, duration=3)
        self.world.advance(1)
        self.change(target="second", life=0, duration=6, action="second_change")
        self.world.advance(3)
        self.assertEqual(self.world.selected_native_skill("1", 1), "second")
        self.world.advance(7)
        self.assertEqual(self.world.selected_native_skill("1", 1), "first")
        self.assertFalse(self.world.native_skill_overrides)

    def test_inherits_progress_both_on_change_and_reversion_using_different_cooldowns(self):
        self.world.cooldowns["base"] = 20
        self.world.advance(5)
        self.change(life=0, duration=2, inherit=True)
        self.assertAlmostEqual(self.world.cooldowns["first"], 12.5)
        self.world.advance(7)
        # 45% complete on the replacement -> 11 seconds remain on the base.
        self.assertAlmostEqual(self.world.cooldowns["base"], 18)
        self.assertFalse(self.world.unresolved)

    def test_cooldown_inheritance_reads_after_previous_handle_reversion(self):
        self.world.cooldowns["base"] = 20
        self.world.cooldowns["first"] = 2
        self.change()
        self.world.advance(5)
        self.change(target="second", inherit=True, action="later")
        self.assertAlmostEqual(self.world.cooldowns["second"], 35)

    def test_unbound_cooldown_does_not_partially_change_slot(self):
        self.change(target="missing", inherit=True)
        self.assertEqual(self.world.selected_native_skill("1", 1), "base")
        self.assertFalse(self.world.native_skill_overrides)
        self.assertIn("Unbound native replacement cooldown programs", self.world.unresolved)

    def test_finish_by_action_reverts_at_end_and_ignores_unused_duration_input(self):
        change = NativeSkillChange(1, "first", NativeTarget("source"), 2, combat_input("unused"))
        program = ActionProgram("scoped", "1", "normal", 0, 2, 0,
                                (CombatEvent(0, "change", skill_changes=(change,)),))
        self.world.start(program, action_id="scoped")
        self.assertEqual(self.world.selected_native_skill("1", 1), "first")
        self.world.advance(2)
        self.assertEqual(self.world.selected_native_skill("1", 1), "base")
        self.assertFalse(self.world.unresolved)

    def test_other_actor_cast_does_not_end_buff_scope_and_removal_reverts(self):
        change = NativeSkillChange(1, "first", NativeTarget("owner"), 2, literal(0))
        callback = ActionProgram("callback", "2", "normal", 0, 0, 0,
                                 (CombatEvent(0, "change", skill_changes=(change,)),))
        definition = NativeBuffProgram((), (), literal(5), literal(0), literal(0), True, 7, literal(1), ((5, callback),))
        addition = NativeBuffChange("stance", literal(1), selector=NativeTarget("main"), definition=definition)
        producer = ActionProgram("producer", "2", "normal", 0, 0, 0,
                                 (CombatEvent(0, "add", native_buffs=(addition,)),))
        self.world.start(producer, action_id="buff")
        self.world.start(replace(producer, events=()), action_id="another")
        self.assertEqual(self.world.selected_native_skill("1", 1), "first")
        self.world.advance(5)
        self.assertEqual(self.world.selected_native_skill("1", 1), "base")
        self.assertFalse(self.world.unresolved)

    def test_prediction_copies_replacement_timers_and_selection(self):
        self.change(life=0, duration=3)
        predicted = self.world.fork()
        predicted.advance(3)
        self.assertEqual(predicted.selected_native_skill("1", 1), "base")
        self.assertEqual(self.world.selected_native_skill("1", 1), "first")

    def test_real_rocxi_change_compiles_authored_six_second_reversion(self):
        store = SkillTimingStore()
        profile = store.profile("chr_0028_wulfa_combo_1_skill")
        data = native_record(store, profile.skill_id)["data"]
        node = next(n for n in _nodes(data) if n["$type"].endswith("ChangeSkillAction+Data"))
        sequence = copy.deepcopy(data["actionGroupData"]["timelineActions"][0]["_sequenceActionData"])
        sequence["actionData"] = [node]
        program = compile_native_action(store, get_character("rossi"), profile, "1", "link", event_sequence=sequence)
        change = program.events[0].skill_changes[0]
        self.assertEqual(change.slot, 1)
        self.assertEqual(change.target_skill, "chr_0028_wulfa_combo_2_skill")
        self.assertEqual(change.reverted_skill, "chr_0028_wulfa_combo_1_skill")
        self.assertEqual(change.duration.evaluate({}), 6)
        self.world._action_inputs["real"] = dict(program.parameters)
        self.world._action_targets["real"] = {"current": ("target",)}
        self.world._execute_event("real", 1, program, program.events[0])
        self.assertEqual(self.world.selected_native_skill("1", 1), change.target_skill)
        self.world.advance(6)
        self.assertEqual(self.world.selected_native_skill("1", 1), change.reverted_skill)

    def test_catalog_discovers_real_stance_and_link_variants_without_making_them_base_actions(self):
        catalog = build_combat_catalog(("庄方宜", "卡缪", "洛茜"), SkillTimingStore())
        keys = {p.key for p in catalog.candidates() if p.native_requires_override}
        self.assertIn("chr_0030_zhuangfy_normal_skill_ult", keys)
        self.assertIn("chr_0030_zhuangfy_combo_skill_ult", keys)
        self.assertIn("chr_0033_camille_normal_skill_2", keys)
        self.assertIn("chr_0028_wulfa_combo_2_skill", keys)
        self.assertFalse(any(p.native_requires_override for p in catalog.available("1", "battle")))
        self.assertFalse(any(p.native_requires_override for p in catalog.available("2", "battle")))
        self.assertFalse(any(p.native_requires_override for p in catalog.available("3", "link")))


if __name__ == "__main__":
    unittest.main()
