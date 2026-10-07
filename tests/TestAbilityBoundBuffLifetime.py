"""Ability children survive casts and mappings; Disable cleans their actual root."""

import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    NativeBuffChange,
    NativeSkillChange,
    NativeTarget,
    walk_combat_events,
)
from src.data.native_ability_runtime import disable_ability, enable_ability
from src.data.native_action_program import compile_native_action
from src.data.native_passive_program import NativePassiveProgram
from src.data.native_passive_runtime import activate_passive, dispatch_passive_event
from src.data.skill_timing import SkillTimingStore
from tests import TestParentBoundBuffLifetime as parent_tests

literal = parent_tests.literal


class TestAbilityBoundBuffLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = parent_tests.TestParentBoundBuffLifetime()
        self.fixture.setUp()
        self.world = self.fixture.world
        self.world.main_control = "2"

    def producer(self, *, key="base", duration=None, actor="1", unique=False, callbacks=None):
        definition = replace(self.fixture.definition(duration=duration, stacking=7 if unique else 2,
                             callbacks=((2, self.fixture.callback(amount=5)),) if callbacks is None else callbacks),
                             inherited=())
        child = NativeBuffChange("child", literal(1), selector=NativeTarget("main"),
                                 definition=definition, child_of_ability=True)
        return ActionProgram(key, actor, "battle", 0, 1, 0,
                             (CombatEvent(0, "add", native_buffs=(child,)),), native_slot=0)

    def test_cast_end_and_next_same_actor_action_do_not_disable_ability(self):
        program = self.producer()
        self.world.start(program, action_id="first")
        child = next(iter(self.world.native_buff_instances.values()))
        self.assertEqual(child.parent_scope, "ability:1:base")
        self.world.advance(1)
        self.world.start(replace(program, key="another", events=(), native_slot=None), action_id="next")
        self.world.advance(2)
        self.assertIn(child.uid, self.world.native_buff_instances)
        self.assertTrue(disable_ability(self.world, "1", "base"))
        self.assertEqual(self.world.characters["2"].energy, 5)
        self.assertFalse(self.world.native_buff_instances)
        self.assertFalse(disable_ability(self.world, "1", "base"))
        self.assertEqual(self.world.characters["2"].energy, 5)

    def test_multiple_casts_share_root_but_children_capture_independent_values(self):
        payout = self.fixture.callback(amount=parent_tests.combat_input("bb.gain"))
        program = self.producer(callbacks=((2, payout),))
        child = program.events[0].native_buffs[0]
        child = replace(child, definition=replace(child.definition,
                        inherited=(("bb.gain", parent_tests.combat_input("bb.value")),)))
        program = replace(program, events=(replace(program.events[0], native_buffs=(child,)),), parameters=(("bb.value", 7),))
        self.world.start(program, action_id="first")
        self.world.advance(1)
        self.world.start(replace(program, parameters=(("bb.value", 11),)), action_id="second")
        self.assertEqual({v.parent_scope for v in self.world.native_buff_instances.values()}, {"ability:1:base"})
        self.assertEqual(len(self.world.native_abilities), 1)
        disable_ability(self.world, "1", "base")
        self.assertEqual(self.world.characters["2"].energy, 18)

    def test_slot_change_and_reversion_enable_target_without_finishing_either_root(self):
        base = self.producer()
        variant = replace(base, key="variant", native_requires_override=True)
        for p in (base, variant):
            self.world.register_native_program(p, default=p == base)
        self.world.start(base, action_id="base")
        self.world.advance(1)
        change = NativeSkillChange(0, "variant", NativeTarget("source"), 0, literal(2))
        switching = ActionProgram("switch", "1", "normal", 0, 0, 0,
                                  (CombatEvent(0, "switch", skill_changes=(change,)),))
        self.world.start(switching, action_id="switch")
        self.assertTrue(self.world.native_abilities["1", "base"].enabled)
        self.assertTrue(self.world.native_abilities["1", "variant"].enabled)
        self.world.start(variant, action_id="variant")
        self.world.advance(3)
        self.assertEqual(self.world.selected_native_skill("1", 0), "base")
        self.assertEqual(len(self.world.native_buff_instances), 2)
        self.assertTrue(self.world.native_abilities["1", "variant"].enabled)
        disable_ability(self.world, "1", "variant")
        self.assertEqual({v.parent_scope for v in self.world.native_buff_instances.values()}, {"ability:1:base"})
        self.assertEqual(self.world.characters["2"].energy, 5)

    def test_natural_expiry_does_not_disable_parent_and_old_queue_does_not_finish_twice(self):
        self.world.start(self.producer(duration=2), action_id="first")
        self.world.advance(2)
        self.assertFalse(self.world.native_buff_instances)
        self.assertTrue(self.world.native_abilities["1", "base"].enabled)
        disable_ability(self.world, "1", "base")
        self.world.advance(20)
        self.assertEqual(self.world.characters["2"].energy, 5)

    def test_disable_cleans_descendants_and_prediction_preserves_original(self):
        grandchild = self.fixture.child(key="grandchild", duration=None,
                                         callbacks=((2, self.fixture.callback(amount=3)),))
        self.world.start(self.producer(callbacks=((0, self.fixture.callback(changes=(grandchild,))),)), action_id="first")
        predicted = self.world.fork()
        disable_ability(predicted, "1", "base")
        self.assertFalse(predicted.native_buff_instances)
        self.assertEqual(predicted.characters["2"].energy, 3)
        self.assertEqual(len(self.world.native_buff_instances), 2)
        self.assertTrue(self.world.native_abilities["1", "base"].enabled)

    def test_same_skill_on_different_actor_and_unique_reapplication_keep_actual_parent(self):
        first = self.producer(unique=True)
        second = self.producer(actor="2", unique=True)
        self.world.start(first, action_id="first")
        self.world.start(second, action_id="second")
        disable_ability(self.world, "2", "base")
        self.assertEqual(len(self.world.native_buff_instances), 1)
        disable_ability(self.world, "1", "base")
        self.assertEqual(self.world.characters["2"].energy, 5)

    def test_disabled_root_rejects_cast_until_explicit_enable_without_reusing_old_children(self):
        program = self.producer(duration=10)
        self.world.start(program, action_id="first")
        self.world.advance(1)
        uid = self.world.native_abilities["1", "base"].uid
        disable_ability(self.world, "1", "base")
        self.assertFalse(self.world.start(program, action_id="rejected"))
        enable_ability(self.world, "1", "base")
        self.world.start(program, action_id="second")
        self.assertEqual(self.world.native_abilities["1", "base"].uid, uid)
        self.world.advance(10)
        self.assertEqual(len(self.world.native_buff_instances), 1)
        self.world.advance(11)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.world.characters["2"].energy, 10)

    def test_orphan_event_is_unresolved_instead_of_binding_to_callback_program(self):
        program = self.producer()
        self.world._execute_event("absent", 1, program, program.events[0])
        self.assertIn("Child buff lacks its enabled Ability root", self.world.unresolved)
        self.assertFalse(self.world.native_buff_instances)

    def test_attached_passive_skill_has_persistent_ability_scope(self):
        program = replace(self.producer(key="chr_test_talent"), native_slot=None)
        passive = NativePassiveProgram("talent", "talent", program)
        self.assertTrue(activate_passive(self.world, passive))
        self.assertFalse(activate_passive(self.world, passive))
        child = next(iter(self.world.native_buff_instances.values()))
        self.assertEqual(child.parent_scope, "ability:1:chr_test_talent")
        disable_ability(self.world, "1", program.key)
        self.assertFalse(self.world.native_buff_instances)

    def test_disable_cancels_pending_root_events_before_reenable_without_canceling_teammate(self):
        payout = self.fixture.callback(amount=7).events[0]
        producer = replace(self.producer(), events=(replace(payout, at=3),))
        self.world.start(producer, action_id="old")
        teammate = replace(producer, actor="2", key="other", native_slot=None)
        self.world.start(teammate, action_id="other")
        disable_ability(self.world, "1", "base")
        enable_ability(self.world, "1", "base")
        self.world.advance(3)
        self.assertEqual(self.world.characters["1"].energy, 0)
        self.assertEqual(self.world.characters["2"].energy, 7)

    def test_disabled_passive_subscription_does_not_execute_until_reenabled(self):
        callback = self.fixture.callback(amount=4)
        program = replace(callback, key="chr_test_subscription")
        passive = NativePassiveProgram("talent", "talent", replace(program, events=()), (("test", callback),))
        activate_passive(self.world, passive)
        dispatch_passive_event(self.world, "test", "1", {})
        self.assertEqual(self.world.characters["1"].energy, 4)
        disable_ability(self.world, "1", program.key)
        dispatch_passive_event(self.world, "test", "1", {})
        self.assertEqual(self.world.characters["1"].energy, 4)
        enable_ability(self.world, "1", program.key)
        dispatch_passive_event(self.world, "test", "1", {})
        self.assertEqual(self.world.characters["1"].energy, 8)

    def test_validated_camille_root_compiles_with_other_mechanics_gaps_retained(self):
        store = SkillTimingStore()
        profile = store.profile("chr_0033_camille_ultimate_skill")
        program = compile_native_action(store, get_character("camille"), profile, "1", "ult")
        changes = [v for event in walk_combat_events(program.events) for v in event.native_buffs
                   if v.key == "buff_chr_0033_camille_ult_hit"]
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0].child_of_ability)
        self.assertFalse(changes[0].child_of_buff)
        self.assertTrue(any(e.unresolved for e in walk_combat_events(program.events)))

    def test_real_attached_passive_buff_cleanup_remains_explicitly_unresolved(self):
        catalog = build_combat_catalog(("余烬",), SkillTimingStore())
        passive = next(p for p in catalog.world.native_passives.values()
                       if any(e.name == "native_passive_skill_buffs" for e in p.program.events))
        self.assertTrue(disable_ability(catalog.world, passive.program.actor, passive.program.key))
        self.assertIn(f"Native Ability passive buff cleanup not yet bound: {passive.program.key}",
                      catalog.world.unresolved)


if __name__ == "__main__":
    unittest.main()
