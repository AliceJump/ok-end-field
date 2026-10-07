"""Enable-owned buff wrappers are separate from children and same-name buffs."""

import unittest
from dataclasses import replace

from src.data.combat_expressions import combat_input
from src.data.combat_simulation import ActionProgram, CombatEvent, NativeBuffChange, NativeBuffQuery, NativeTarget
from src.data.native_ability_runtime import disable_ability, enable_ability
from src.data.native_passive_program import NativePassiveProgram
from src.data.native_passive_runtime import activate_passive
from tests import TestParentBoundBuffLifetime as parent_tests

literal = parent_tests.literal


class TestAbilityPassiveBuffLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = parent_tests.TestParentBoundBuffLifetime()
        self.fixture.setUp()
        self.world = self.fixture.world

    def owned(self, *, key="owned", duration=None, unique=False, callbacks=None):
        definition = self.fixture.definition(duration=duration, stacking=7 if unique else 2,
                     callbacks=((2, self.fixture.callback(amount=combat_input("bb.gain"))),) if callbacks is None else callbacks)
        return NativeBuffChange(key, literal(1), selector=NativeTarget("owner"), definition=definition,
                                 passive_of_ability=True)

    def producer(self, *, key="skill", actor="1", changes=None, value=7, events=(), subscriptions=(), identity=None):
        program = ActionProgram(key, actor, "normal", 0, 0, 0, events, parameters=(("bb.inherit", value),))
        return NativePassiveProgram(identity or key, "fixture", program, subscriptions, ability_skill=key,
                                     ability_buffs=(self.owned(),) if changes is None else changes)

    def activate(self, passive):
        self.assertTrue(activate_passive(self.world, passive))
        return self.world.native_abilities[passive.program.actor, passive.ability_skill]

    def test_enable_and_registration_are_idempotent_without_cast_cost_or_time(self):
        passive = self.producer()
        before = (self.world.sp, self.world.time, self.world._actions_started, dict(self.world.cooldowns))
        root = self.activate(passive)
        uid = root.passive_buffs[0]
        self.assertIsNone(self.world.native_buff_instances[uid].parent_scope)
        self.assertFalse(activate_passive(self.world, passive))
        enable_ability(self.world, "1", "skill")
        self.assertEqual(root.passive_buffs, [uid])
        self.assertEqual(before, (self.world.sp, self.world.time, self.world._actions_started, self.world.cooldowns))

    def test_same_name_other_roots_and_actor_buffs_are_not_removed(self):
        first = self.activate(self.producer(key="first"))
        second = self.activate(self.producer(key="second", value=11))
        other = self.activate(self.producer(actor="2", key="other", value=15))
        disable_ability(self.world, "1", "first")
        self.assertFalse(first.passive_buffs)
        self.assertEqual(set(self.world.native_buff_instances), {*second.passive_buffs, *other.passive_buffs})
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.assertEqual(self.world.characters["2"].energy, 0)

    def test_unique_reapplication_returns_no_wrapper_to_second_root(self):
        change = self.owned(unique=True)
        first = self.activate(self.producer(key="first", changes=(change,)))
        second = self.activate(self.producer(key="second", changes=(change,), value=11))
        self.assertEqual(len(first.passive_buffs), 1)
        self.assertFalse(second.passive_buffs)
        disable_ability(self.world, "1", "second")
        self.assertEqual(len(self.world.native_buff_instances), 1)
        disable_ability(self.world, "1", "first")
        self.assertEqual(self.world.characters["1"].energy, 7)

    def test_stale_expired_wrapper_cannot_remove_later_same_name_instance(self):
        root = self.activate(self.producer(changes=(self.owned(duration=1),)))
        stale_uid = root.passive_buffs[0]
        self.world.advance(1)
        second = self.activate(self.producer(key="later", value=11))
        self.assertNotIn(stale_uid, self.world.native_buff_instances)
        disable_ability(self.world, "1", "skill")
        self.assertEqual(set(self.world.native_buff_instances), set(second.passive_buffs))
        self.assertEqual(self.world.characters["1"].energy, 7)

    def test_reenable_uses_current_ability_blackboard_and_keeps_existing_buff_snapshot(self):
        passive = self.producer()
        root = self.activate(passive)
        first_uid = root.passive_buffs[0]
        self.world._action_inputs[root.passive_scope]["bb.inherit"] = 11
        disable_ability(self.world, "1", "skill")
        self.assertEqual(self.world.characters["1"].energy, 7)
        enable_ability(self.world, "1", "skill")
        self.assertNotEqual(root.passive_buffs[0], first_uid)
        self.assertEqual(self.world._action_inputs[root.passive_buffs[0]]["bb.gain"], 11)
        disable_ability(self.world, "1", "skill")
        self.assertEqual(self.world.characters["1"].energy, 18)
        self.assertFalse(disable_ability(self.world, "1", "skill"))
        self.assertFalse(self.world.unresolved)

    def test_passive_finish_precedes_ability_child_finish_and_cleans_buff_descendants(self):
        query = NativeBuffQuery("child_count", NativeTarget("owner"), ("child",))
        payout = self.fixture.callback(amount=combat_input("child_count"), query=query)
        grandchild = self.fixture.child(key="grandchild", duration=None,
                                        callbacks=((2, self.fixture.callback(amount=3)),))
        owned = self.owned(callbacks=((0, self.fixture.callback(changes=(grandchild,))), (2, payout)))
        child = replace(self.fixture.child(duration=None, callbacks=((2, self.fixture.callback(amount=5)),)),
                         child_of_buff=False, child_of_ability=True)
        initial = CombatEvent(0, "child", native_buffs=(child,))
        self.activate(self.producer(changes=(owned,), events=(initial,)))
        self.assertEqual(len(self.world.native_buff_instances), 3)
        disable_ability(self.world, "1", "skill")
        self.assertEqual(self.world.characters["1"].energy, 9)  # Child present (1), descendant (3), child finish (5).
        self.assertFalse(self.world.native_buff_instances)

    def test_start_callback_removal_does_not_store_invalid_wrapper(self):
        remove = NativeBuffChange("owned", literal(-1), selector=NativeTarget("owner"), remove_all=True)
        root = self.activate(self.producer(changes=(self.owned(callbacks=((0, self.fixture.callback(changes=(remove,))),)),)))
        self.assertFalse(root.passive_buffs)
        self.assertFalse(self.world.native_buff_instances)
        disable_ability(self.world, "1", "skill")
        self.assertFalse(self.world.unresolved)

    def test_prediction_preserves_separate_uid_lists_and_parameters(self):
        root = self.activate(self.producer())
        prediction = self.world.fork()
        predicted_root = prediction.native_abilities["1", "skill"]
        self.assertIsNot(root.passive_buffs, predicted_root.passive_buffs)
        prediction._action_inputs[predicted_root.passive_scope]["bb.inherit"] = 11
        disable_ability(prediction, "1", "skill")
        enable_ability(prediction, "1", "skill")
        self.assertEqual(self.world._action_inputs[root.passive_buffs[0]]["bb.gain"], 7)
        self.assertEqual(prediction._action_inputs[predicted_root.passive_buffs[0]]["bb.gain"], 11)

    def test_multiple_definitions_for_same_skill_stay_explicitly_unresolved(self):
        self.activate(self.producer(identity="first"))
        self.assertFalse(activate_passive(self.world, self.producer(identity="second", value=11)))
        self.assertEqual(len(self.world.native_passives), 1)
        self.assertEqual(len(self.world.native_buff_instances), 1)
        self.assertIn("Multiple native passive Skill producers need binding: skill", self.world.unresolved)

    def test_reenable_does_not_guess_when_initial_timeline_should_restart(self):
        passive = self.producer(events=(CombatEvent(1, "initial_timeline"),))
        self.activate(passive)
        disable_ability(self.world, "1", "skill")
        enable_ability(self.world, "1", "skill")
        self.assertIn("Native passive timeline restart needs binding: skill", self.world.unresolved)
        self.assertFalse(any(row[4].name == "initial_timeline" for row in self.world._queue))


if __name__ == "__main__":
    unittest.main()
