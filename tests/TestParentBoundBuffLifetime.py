"""Children belong to native Buff instances, including their finish callbacks."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    NativeBuffChange,
    NativeBuffQuery,
    NativeResourceChange,
    NativeTarget,
    walk_combat_events,
)
from src.data.native_action_program import compile_native_action
from src.data.native_buff_program import compile_buff_definition
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import CombatResourceType
from tests import TestNativeBuffLifecycle as buff_tests

literal = buff_tests.literal


class TestParentBoundBuffLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = buff_tests.TestNativeBuffLifecycle()
        self.fixture.setUp()
        self.world = self.fixture.world

    def callback(self, *, changes=(), amount=0, query=None):
        resources = () if amount == 0 else (NativeResourceChange(
            CombatResourceType.ULTIMATE_ENERGY, amount if isinstance(amount, CombatExpression) else literal(amount), literal(1),
            NativeTarget("source"), NativeTarget("owner"), ignore_energy_gain=True),)
        return ActionProgram("callback", "1", "normal", 0, 0, 0,
                             (CombatEvent(0, "callback", native_buffs=changes, native_resources=resources),),
                             native_buff_queries=() if query is None else (query,))

    def definition(self, *, duration=3, callbacks=(), maximum=3, stacking=2):
        return self.fixture.definition(duration=duration, period=0, limit=0, callbacks=callbacks,
                                       maximum=maximum, stacking=stacking)

    def child(self, *, key="child", duration=10, callbacks=(), stacking=2, target="owner"):
        return NativeBuffChange(key, literal(1), selector=NativeTarget(target), child_of_buff=True,
                                definition=replace(self.definition(duration=duration, callbacks=callbacks,
                                                                   stacking=stacking), inherited=()))

    def add_parent(self, definition, action_id="parent"):
        self.fixture.add(definition, action_id=action_id)
        return next(v for v in reversed(tuple(self.world.native_buff_instances.values())) if v.key == "test_buff")

    def remove(self, key):
        remove = NativeBuffChange(key, literal(-1), remove_all=True, selector=NativeTarget("main"))
        self.assertTrue(self.world.start(self.callback(changes=(remove,)), action_id="remove:" + key))

    def test_finish_callback_observes_child_then_recursive_cleanup_cancels_subscriptions(self):
        payout = self.callback(amount=5)
        child_definition = replace(self.definition(duration=10, callbacks=((2, payout),)),
                                   inherited=(), subscriptions=(("OnBeforeOutputPhysicalInfliction", payout),))
        child = replace(self.child(), definition=child_definition)
        query = NativeBuffQuery("children", NativeTarget("owner"), ("child",))
        finish = self.callback(amount=combat_input("children"), query=query)
        self.add_parent(self.definition(callbacks=((0, self.callback(changes=(child,))), (2, finish))))
        self.world.dispatch_native("OnBeforeOutputPhysicalInfliction", "2")
        self.assertEqual(self.world.characters["2"].energy, 5)
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 11)  # Parent sees 1 child, then child pays 5.
        self.assertFalse(self.world.native_buff_instances)
        self.world.dispatch_native("OnBeforeOutputPhysicalInfliction", "2")
        self.world.advance(20)
        self.assertEqual(self.world.characters["2"].energy, 11)
        self.assertFalse(self.world.unresolved)
        self.assertIsNone(self.world._native_buff_context)

    def test_layers_have_distinct_parents_and_overflow_removes_only_its_descendants(self):
        grandchild = self.child(key="grandchild", callbacks=((2, self.callback(amount=2)),))
        child = self.child(callbacks=((0, self.callback(changes=(grandchild,))), (2, self.callback(amount=3))))
        definition = self.definition(duration=10, maximum=2, callbacks=((0, self.callback(changes=(child,))),))
        first = self.add_parent(definition)
        self.world.advance(1)
        second = self.add_parent(definition, "second")
        self.world.advance(2)
        third = self.add_parent(definition, "third")
        self.assertNotIn(first.uid, self.world.native_buff_instances)
        self.assertEqual(self.world.characters["2"].energy, 5)
        self.assertEqual({v.parent_scope for v in self.world.native_buff_instances.values() if v.key == "child"},
                         {second.uid, third.uid})
        self.assertEqual(len(self.world.native_buff_instances), 6)
        self.world.advance(11)
        self.assertEqual(self.world.characters["2"].energy, 10)
        self.assertEqual({v.parent_scope for v in self.world.native_buff_instances.values() if v.key == "child"},
                         {third.uid})
        self.world.advance(12)
        self.assertEqual(self.world.characters["2"].energy, 15)
        self.assertFalse(self.world.native_buff_instances)

    def test_early_child_removal_and_teammate_action_do_not_finish_parent(self):
        child = self.child(duration=.5, callbacks=((2, self.callback(amount=4)),))
        parent = self.add_parent(self.definition(callbacks=((0, self.callback(changes=(child,))),)))
        self.world.advance(.5)
        other = ActionProgram("teammate", "2", "normal", 0, 0, 0, ())
        self.assertTrue(self.world.start(other, action_id="teammate"))
        self.assertIn(parent.uid, self.world.native_buff_instances)
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 4)
        self.assertFalse(self.world.native_buff_instances)

    def test_children_created_during_parent_finish_are_removed_in_same_finish(self):
        child = self.child(callbacks=((2, self.callback(amount=9)),))
        self.add_parent(self.definition(callbacks=((2, self.callback(changes=(child,))),)))
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 9)
        self.assertFalse(self.world.native_buff_instances)
        self.world.advance(20)
        self.assertEqual(self.world.characters["2"].energy, 9)

    def test_explicit_parent_removal_ends_permanent_child_once_with_captured_parameters(self):
        payout = self.callback(amount=combat_input("bb.gain"))
        child = self.child(duration=None, callbacks=((2, payout),))
        child = replace(child, definition=replace(child.definition,
                        inherited=(("bb.gain", combat_input("bb.gain")),)))
        parent = self.add_parent(self.definition(callbacks=((0, self.callback(changes=(child,))),)))
        self.world._action_inputs[parent.uid]["bb.gain"] = 99
        self.remove("test_buff")
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.world.characters["2"].energy, 7)
        self.world.advance(30)
        self.assertEqual(self.world.characters["2"].energy, 7)

    def test_cross_holder_child_and_fork_keep_instance_links(self):
        child = self.child(target="source", callbacks=((2, self.callback(amount=8)),))
        parent = self.add_parent(self.definition(callbacks=((0, self.callback(changes=(child,))),)))
        actual_child = next(v for v in self.world.native_buff_instances.values() if v.key == "child")
        self.assertEqual((actual_child.owner, actual_child.parent_scope), ("1", parent.uid))
        predicted = self.world.fork()
        predicted.advance(3)
        self.assertEqual(predicted.characters["1"].energy, 8)
        self.assertFalse(predicted.native_buff_instances)
        self.assertEqual(self.world.characters["1"].energy, 0)
        self.assertEqual(self.world.time, 0)
        self.assertEqual(len(self.world.native_buff_instances), 2)

    def test_absent_root_is_explicitly_unresolved(self):
        self.assertIsNone(self.world.simulate(self.callback(changes=(self.child(),))))
        self.assertFalse(self.world.native_buff_instances)
        self.assertIsNone(self.world._native_buff_context)

    def test_unique_reapplication_does_not_attach_old_instance_to_new_parent(self):
        child = self.child(duration=9, stacking=7, callbacks=((0, self.callback(amount=4)),
                                                           (2, self.callback(amount=6))))
        first = self.add_parent(self.definition(duration=5, callbacks=((0, self.callback(changes=(child,))),)))
        actual_child = next(v for v in self.world.native_buff_instances.values() if v.key == "child")
        self.world.advance(1)
        second = self.add_parent(self.definition(duration=1, callbacks=((0, self.callback(changes=(child,))),)), "second")
        self.assertEqual(actual_child.parent_scope, first.uid)
        self.assertEqual(actual_child.expires, 9)
        self.assertEqual(self.world.characters["2"].energy, 4)  # No repeated OnStart payout.
        self.world.advance(2)
        self.assertNotIn(second.uid, self.world.native_buff_instances)
        self.assertIn(actual_child.uid, self.world.native_buff_instances)
        self.world.advance(5)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.world.characters["2"].energy, 10)
        self.world.advance(20)
        self.assertEqual(self.world.characters["2"].energy, 10)
        self.assertFalse(self.world.unresolved)

    def test_unique_reapplication_keeps_captured_values_when_first_parent_ends_before_next(self):
        payout = self.callback(amount=combat_input("bb.gain"))
        child = self.child(duration=None, stacking=7, callbacks=((2, payout),))
        child = replace(child, definition=replace(child.definition,
                        inherited=(("bb.gain", combat_input("bb.gain")),)))
        self.add_parent(self.definition(duration=1, callbacks=((0, self.callback(changes=(child,))),)))
        self.world.advance(.5)
        self.fixture.add(self.definition(duration=4, callbacks=((0, self.callback(changes=(child,))),)),
                         action_id="next", amount=50)
        self.world.advance(1)
        self.assertEqual(self.world.characters["2"].energy, 7)
        self.assertEqual(len(self.world.native_buff_instances), 1)  # Only second parent survives.
        self.world.advance(4.5)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.world.characters["2"].energy, 7)

    def test_unique_child_in_validated_liino_subscription_compiles_with_priority_gap_retained(self):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        parent_id = "buff_chr_0035_liino_potential"
        definition = compile_buff_definition(store, get_character("perlica"), profile, "1", parent_id,
            native_record(store, parent_id)["data"], {"assignBlackboard": False}, attributes={}, panel=None, path=())
        changes = [v for _, program in definition.subscriptions for event in walk_combat_events(program.events)
                   for v in event.native_buffs if v.count.evaluate({}) > 0]
        self.assertEqual([v.child_of_buff for v in changes], [True])
        self.assertEqual([v.definition.stacking for v in changes], [7])
        self.assertTrue(any("priority" in reason for v in changes for reason in v.definition.unresolved))

    def test_validated_ikut_child_node_binds_only_with_buff_root(self):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        parent_id = "buff_chr_0007_ikut_atk_buff_talent"
        data = native_record(store, parent_id)["data"]
        # Unmodified parent callback and leaf record; the parent's attribute
        # modifier remains a separate blocker, never asserted as priceable.
        definition = compile_buff_definition(store, get_character("perlica"), profile, "1", parent_id, data,
            {"assignBlackboard": False}, attributes={}, panel=None, path=())
        changes = [v for _, program in definition.callbacks for event in walk_combat_events(program.events)
                   for v in event.native_buffs]
        self.assertEqual([v.child_of_buff for v in changes], [True])
        self.assertTrue(any("attributeModifier" in reason for reason in definition.unresolved))
        # Supply the parent duration for runtime testing; native unknowns persist.
        definition = replace(definition, parameters=(*definition.parameters, ("bb.duration", 1.0)))
        self.add_parent(definition)
        self.assertEqual(len(self.world.native_buff_instances), 2)
        self.world.advance(1)
        self.assertFalse(self.world.native_buff_instances)
        sequence = copy.deepcopy(data["buffEventAction"][0]["actions"][0])
        standalone = compile_native_action(store, get_character("perlica"), profile, "1", "normal",
                                            event_sequence=sequence)
        self.assertTrue(any("child/action-bound" in reason for event in walk_combat_events(standalone.events)
                            for reason in event.unresolved))
        self.assertFalse(any(v.child_of_buff for event in walk_combat_events(standalone.events)
                             for v in event.native_buffs))


if __name__ == "__main__":
    unittest.main()
