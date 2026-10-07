"""Confirmed CastEnd transfers exact instances; handoff does not supply context."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import ActionProgram, CombatEvent, NativeBuffChange, NativeTarget, walk_combat_events
from src.data.native_ability_runtime import disable_ability
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.native_skill_runtime import attach_skill_buffs, bind_skill_object, finish_skill_cast
from src.data.skill_timing import SkillTimingStore
from tests import TestParentBoundBuffLifetime as parent_tests

literal = parent_tests.literal


class TestSkillAttachedBuffLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = parent_tests.TestParentBoundBuffLifetime()
        self.fixture.setUp()
        self.world = self.fixture.world

    def cast(self, key="first", scope="first", *, inherit=("next",), transfer=True, unique=False,
             duration=None, child=False):
        definition = replace(self.fixture.definition(duration=duration, stacking=7 if unique else 2,
                             callbacks=((2, self.fixture.callback(amount=5)),)), inherited=())
        change = NativeBuffChange("retained", literal(1), selector=NativeTarget("owner"), definition=definition,
                                  action_finish_after=None if child else 8,
                                  child_of_ability=child, inherit_skill_ids=() if child else inherit,
                                  finish_with_next_skill=transfer)
        program = ActionProgram(key, "1", "normal", 0, .25, 0,
                                (CombatEvent(0, "add", native_buffs=(change,)),))
        self.assertTrue(self.world.start(program, action_id=scope))
        return next(reversed(self.world.native_buff_instances))

    def end(self, scope="first", reason=7, next_skill="next"):
        return finish_skill_cast(self.world, scope, reason=reason, next_skill=next_skill)

    def start_empty(self, key="next", scope="next"):
        self.assertTrue(self.world.start(ActionProgram(key, "1", "normal", 0, .25, 0, ()), action_id=scope))

    def test_transfer_keeps_uid_blackboard_and_natural_deadline_until_target_cast_end(self):
        bind_skill_object(self.world, "1", "next")
        uid = self.cast(duration=20)
        before = dict(self.world._action_inputs[uid])
        self.assertTrue(self.end())
        instance = self.world.native_buff_instances[uid]
        self.assertEqual((instance.action_scope, instance.action_finish_at, instance.expires), (None, None, 20))
        self.assertEqual(self.world._action_inputs[uid], before)
        self.assertEqual(self.world.native_skill_buffs["1", "next"], [uid])
        self.world.advance(9)  # The old action deadline cannot finish a transferred instance.
        self.start_empty()
        self.world.advance(9.5)  # Handoff also cannot finish Skill.AttachBuff's list.
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertTrue(self.end("next", reason=0, next_skill=None))
        self.assertNotIn(uid, self.world.native_buff_instances)
        self.assertEqual(self.world.characters["1"].energy, 5)
        self.assertFalse(self.end("next", reason=0, next_skill=None))
        self.world.advance(21)
        self.assertEqual(self.world.characters["1"].energy, 5)

    def test_false_finish_with_next_preserves_without_attaching_or_enabling_target(self):
        uid = self.cast(transfer=False)
        self.end()
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs)
        self.assertNotIn(("1", "next"), self.world.native_abilities)
        self.world.advance(9)
        self.start_empty()
        self.end("next", reason=0, next_skill=None)
        self.assertIn(uid, self.world.native_buff_instances)

    def test_wrong_reason_or_nonmatching_target_finishes_without_transfer(self):
        for reason, target in ((0, "next"), (6, "next"), (7, "other"), (7, None)):
            with self.subTest(reason=reason, target=target):
                self.setUp()
                bind_skill_object(self.world, "1", "next")
                uid = self.cast()
                self.end(reason=reason, next_skill=target)
                self.assertNotIn(uid, self.world.native_buff_instances)
                self.assertFalse(self.world.native_skill_buffs)

    def test_missing_object_or_other_actors_object_cannot_receive_transfer(self):
        bind_skill_object(self.world, "2", "next")
        uid = self.cast()
        self.end()
        self.assertNotIn(uid, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs)
        self.assertNotIn(("1", "next"), self.world.native_abilities)

    def test_same_skill_end_cleans_old_batch_but_preserves_new_self_transfer(self):
        old = self.cast(inherit=())
        attach_skill_buffs(self.world, "1", "first", (old,))
        # A second instance belongs to this timeline but not to the old attached list.
        program = self.world.active_actions["1"][1]
        change = replace(program.events[0].native_buffs[0], inherit_skill_ids=("first",))
        self.world._execute_event("first", 10000, program, CombatEvent(0, "second", native_buffs=(change,)))
        new = next(reversed(self.world.native_buff_instances))
        self.end(next_skill="first")
        self.assertNotIn(old, self.world.native_buff_instances)
        self.assertIn(new, self.world.native_buff_instances)
        self.assertEqual(self.world.native_skill_buffs["1", "first"], [new])
        self.assertEqual(self.world.characters["1"].energy, 5)
        self.world.advance(.25)
        self.start_empty(key="first", scope="again")
        self.end("again", reason=0, next_skill=None)
        self.assertFalse(self.world.native_buff_instances)
        self.assertEqual(self.world.characters["1"].energy, 10)

    def test_stale_attached_uid_does_not_remove_later_same_name_buff(self):
        uid = self.cast(duration=.5)
        attach_skill_buffs(self.world, "1", "first", (uid,))
        self.world.advance(1)
        self.assertNotIn(uid, self.world.native_buff_instances)
        program = self.world.active_actions["1"][1]
        change = replace(program.events[0].native_buffs[0], action_finish_after=None, inherit_skill_ids=())
        self.world._execute_event("first", 10000, program, CombatEvent(0, "later", native_buffs=(change,)))
        later = next(iter(self.world.native_buff_instances))
        self.end(reason=0, next_skill=None)
        self.assertIn(later, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs["1", "first"])

    def test_unique_readd_does_not_transfer_an_instance_created_by_another_scope(self):
        uid = self.cast(unique=True, transfer=False)
        self.end()
        self.world.advance(.25)
        bind_skill_object(self.world, "1", "next")
        self.cast(key="second", scope="second", unique=True)
        self.end("second")
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs)

    def test_cast_end_does_not_disable_or_remove_ability_children(self):
        uid = self.cast(child=True)
        self.end(reason=0, next_skill=None)
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertTrue(self.world.native_abilities["1", "first"].enabled)
        disable_ability(self.world, "1", "first")
        self.assertNotIn(uid, self.world.native_buff_instances)

    def test_fork_transfer_does_not_mutate_original_lists_instances_or_roots(self):
        bind_skill_object(self.world, "1", "next")
        uid = self.cast()
        original = self.world
        self.world = original.fork()
        self.end()
        self.assertEqual(self.world.native_skill_buffs["1", "next"], [uid])
        self.assertFalse(original.native_skill_buffs)
        self.assertEqual(original.native_buff_instances[uid].action_scope, "first")
        self.assertFalse(original._native_confirmed_cast_ends)

    def test_unknown_scope_and_handoff_cannot_supply_native_end_context(self):
        bind_skill_object(self.world, "1", "next")
        uid = self.cast()
        self.world.advance(.25)
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertFalse(self.end("unknown"))
        self.assertTrue(any("CastEnd lacks" in value for value in self.world.unresolved))
        self.start_empty()
        self.assertNotIn(uid, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs)

    def test_delayed_end_for_old_cast_cannot_finish_current_same_skill_list(self):
        old = self.cast()
        self.world.advance(.25)
        self.start_empty(key="first", scope="again")
        self.assertNotIn(old, self.world.native_buff_instances)
        program = self.world.active_actions["1"][1]
        definition = replace(self.fixture.definition(duration=None), inherited=())
        change = NativeBuffChange("new", literal(1), selector=NativeTarget("owner"), definition=definition)
        self.world._execute_event("again", 10000, program, CombatEvent(0, "new", native_buffs=(change,)))
        uid = next(iter(self.world.native_buff_instances))
        attach_skill_buffs(self.world, "1", "first", (uid,))
        self.assertFalse(self.end())
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertTrue(any("superseded" in value for value in self.world.unresolved))
        self.assertTrue(self.end("again", reason=0, next_skill=None))
        self.assertNotIn(uid, self.world.native_buff_instances)

    def test_actual_mifu_inheritance_data_is_retained_but_not_accepted_without_end_producer(self):
        store = SkillTimingStore()
        profile = store.profiles("弭弗", "battle")[0]
        source = native_record(store, "chr_0031_mifu_normalskill_1")
        node = copy.deepcopy(next(n for n in _nodes(source) if n["$type"].endswith("CreateBuffAction+Data")
                                  and n["$value"].get("inheritSkillIdList")))
        record = copy.deepcopy(native_record(store, profile.skill_id))
        record["data"]["actionGroupData"]["passiveEventActions"] = []
        record["data"]["actionGroupData"]["timelineActions"] = [{"_startFrame": 0, "_endFrame": 240,
            "_sequenceActionData": {"actionData": [node]}}]
        def lookup(s, key):
            return record if key == profile.skill_id else native_record(s, key)
        with patch("src.data.native_action_program.native_record", side_effect=lookup):
            program = compile_native_action(store, get_character("mi_fu"), profile, "1", "battle")
        changes = [change for event in walk_combat_events(program.events) for change in event.native_buffs]
        self.assertTrue(changes)
        self.assertEqual(changes[0].inherit_skill_ids, tuple(node["$value"]["inheritSkillIdList"]))
        self.assertTrue(changes[0].finish_with_next_skill)
        self.assertTrue(any("CastEnd context not yet bound" in reason for event in walk_combat_events(program.events)
                            for reason in event.unresolved))
        # Only the creation event is exercised with separately confirmed end
        # context; the complete real program remains blocked by its diagnostic.
        change = replace(changes[0], selector=NativeTarget("owner"))
        cast = ActionProgram(profile.skill_id, "1", "normal", 0, .25, 0,
                             (CombatEvent(0, "create", native_buffs=(change,)),), parameters=program.parameters)
        self.assertTrue(self.world.start(cast, action_id="real"))
        target = change.inherit_skill_ids[0]
        bind_skill_object(self.world, "1", target)
        uid = next(iter(self.world.native_buff_instances))
        self.assertTrue(self.end("real", next_skill=target))
        self.assertEqual(self.world.native_skill_buffs["1", target], [uid])
