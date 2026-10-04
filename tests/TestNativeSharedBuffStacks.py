"""Shared stacking keys limit instances without becoming aliases for buff IDs."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeTarget,
    UnresolvedMechanic,
)
from src.data.native_buff_program import compile_buff_definition
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


def literal(value):
    return CombatExpression("literal", (float(value),))


class TestNativeSharedBuffStacks(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.definition = NativeBuffProgram((("bb.value", 0.0),), (("bb.value", combat_input("bb.inherit")),),
                                            literal(3), literal(1), literal(-1), True, 2, literal(1), (),
                                            stacking_key="shared")

    def add(self, key, *, owner="1", actor="1", value=7, definition=None):
        definition = definition or self.definition
        self.world.main_control = owner
        change = NativeBuffChange(key, literal(1), selector=NativeTarget("main"), definition=definition)
        program = ActionProgram("add:" + key, actor, "normal", 0, 0, 0,
                                (CombatEvent(0, "buff", native_buffs=(change,)),), parameters=(("bb.inherit", value),))
        action_id = "cast:" + key + ":" + owner
        self.assertTrue(self.world.start(program, action_id=action_id))

    def remove(self, key, owner="1"):
        self.world.main_control = owner
        change = NativeBuffChange(key, literal(-1), remove_all=True, selector=NativeTarget("main"))
        program = ActionProgram("remove", "1", "normal", 0, 0, 0, (CombatEvent(0, "buff", native_buffs=(change,)),))
        self.world.start(program, action_id="remove:" + key)

    def test_cross_id_overflow_finishes_old_instance_and_preserves_new_blackboard(self):
        finish = ActionProgram("finish", "1", "normal", 0, 0, 0,
                               (CombatEvent(0, "finish", assignments=(("bb.EntityBB_finished", combat_input("bb.value")),)),))
        definition = replace(self.definition, callbacks=((2, finish),))
        self.add("a", value=7, definition=definition)
        old = next(iter(self.world.native_buff_instances))
        self.world.advance(1)
        self.add("b", value=11, definition=definition)
        self.assertNotIn(old, self.world.native_buff_instances)
        self.assertNotIn(("1", "a"), self.world.native_buffs)
        self.assertEqual(self.world.native_buffs["1", "b"], (1, 4))
        self.assertEqual(self.world.characters["1"].blackboard["EntityBB_finished"], 7)
        new = next(iter(self.world.native_buff_instances))
        self.assertEqual(self.world._action_inputs[new]["bb.value"], 11)
        self.world.advance(3)
        self.assertIn(new, self.world.native_buff_instances)
        self.world.advance(4)
        self.assertEqual(self.world.characters["1"].blackboard["EntityBB_finished"], 11)
        self.assertFalse(self.world.native_buffs)
        self.assertFalse(self.world.unresolved)

    def test_group_is_per_owner_and_does_not_split_by_source(self):
        self.add("a", owner="1", actor="1")
        self.add("b", owner="2", actor="1")
        self.add("c", owner="1", actor="2")
        self.assertEqual(set(self.world.native_buffs), {("2", "b"), ("1", "c")})
        self.assertEqual({(v.key, v.source) for v in self.world.native_buff_instances.values()}, {("b", "1"), ("c", "2")})

    def test_removal_uses_real_buff_id_and_does_not_remove_other_group_member(self):
        definition = replace(self.definition, maximum=literal(2))
        self.add("a", definition=definition)
        self.add("b", definition=definition)
        self.remove("a")
        self.assertEqual(set(self.world.native_buffs), {("1", "b")})
        self.remove("shared")
        self.assertEqual(set(self.world.native_buffs), {("1", "b")})
        self.assertFalse(self.world.unresolved)

    def test_id_and_stacking_key_use_same_native_string_namespace(self):
        self.add("shared", definition=replace(self.definition, stacking_key=None))
        self.add("another")
        self.assertEqual(set(self.world.native_buffs), {("1", "another")})

    def test_unique_reapplication_and_prediction_keep_first_scope(self):
        definition = replace(self.definition, stacking=7)
        self.add("a", definition=definition)
        predicted = self.world.fork()
        actual = self.world
        self.world = predicted
        self.add("b", value=99, definition=definition)
        self.assertEqual(set(predicted.native_buffs), {("1", "a")})
        uid = next(iter(predicted.native_buff_instances))
        self.assertEqual(predicted._action_inputs[uid]["bb.value"], 7)
        self.remove("a")
        self.assertTrue(actual.native_buff_instances)
        self.assertFalse(predicted.native_buff_instances)

    def test_conflicting_group_policy_is_rejected_without_finishing_old_buff(self):
        self.add("a")
        self.add("b", definition=replace(self.definition, maximum=literal(2)))
        self.assertEqual(set(self.world.native_buffs), {("1", "a")})
        self.assertTrue(any("conflicting policies" in error for error in self.world.unresolved))

    def test_real_conduction_entries_share_identity_and_stale_id_key_is_ignored(self):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        character = get_character("perlica")

        def compile(key, data):
            return compile_buff_definition(store, character, profile, "1", key, data,
                                            {"assignBlackboard": False}, attributes={}, panel=None, path=())

        for old in ("fire", "cryst", "natural"):
            key = f"buff_common_pulse_{old}_triggered"
            definition = compile(key, native_record(store, key)["data"])
            self.assertEqual(definition.stacking_key, "pulse_triggered")
            self.assertFalse(any("stacking identity" in error for error in definition.unresolved))
        key = "buff_common_pulse_pulse_triggered"
        data = copy.deepcopy(native_record(store, key)["data"])
        data["stackingSettings"].update(identifierType=0, stackingKey="stale")
        self.assertIsNone(compile(key, data).stacking_key)
        for identity, value in ((1, ""), (99, "shared")):
            data["stackingSettings"].update(identifierType=identity, stackingKey=value)
            with self.assertRaises(UnresolvedMechanic):
                compile(key, data)


if __name__ == "__main__":
    unittest.main()
