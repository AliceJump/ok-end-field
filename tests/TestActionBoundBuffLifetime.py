"""Authored node deadlines and cancellation are independent of buff duration."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import ActionProgram, CombatEvent, NativeBuffChange, NativeTarget, walk_combat_events
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore
from tests import TestNativeBuffLifecycle as buff_tests

literal = buff_tests.literal


class TestActionBoundBuffLifetime(unittest.TestCase):
    def setUp(self):
        fixture = buff_tests.TestNativeBuffLifecycle()
        fixture.setUp()
        self.world = fixture.world
        self.definition = fixture.definition

    def add(self, *, action_id="cast", finish=1.5, duration=5, handoff=.25):
        change = NativeBuffChange("bound", literal(1), selector=NativeTarget("owner"),
                                  definition=self.definition(duration=duration), action_finish_after=finish)
        program = ActionProgram("bound_skill", "1", "normal", 0, handoff, 0,
                                (CombatEvent(0, "add", native_buffs=(change,)),), parameters=(("bb.inherit", 7.0),))
        self.assertTrue(self.world.start(program, action_id=action_id))

    def test_authored_end_is_independent_of_handoff_and_natural_duration(self):
        self.add()
        self.world.advance(.5)
        instance = next(iter(self.world.native_buff_instances.values()))
        self.assertEqual(instance.expires, 5)  # Buff timer is not clamped to action deadline.
        self.world.advance(1)
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.world.advance(1.5)
        self.assertFalse(self.world.native_buff_instances)
        self.world.advance(6)
        self.assertEqual(self.world.characters["1"].energy, 7)

    def test_actual_cancellation_removes_only_previous_action_instances(self):
        self.add(action_id="first")
        self.world.advance(.5)
        self.add(action_id="next", finish=3)
        self.assertEqual({v.action_scope for v in self.world.native_buff_instances.values()}, {"next"})
        self.world.advance(1.5)  # First instance's queued expiry cannot remove the next instance.
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.assertEqual({v.action_scope for v in self.world.native_buff_instances.values()}, {"next"})

    def test_earlier_natural_expiry_and_fork_leave_no_late_payout(self):
        self.add(finish=4, duration=.5)
        predicted = self.world.fork()
        predicted.advance(6)
        self.assertFalse(predicted.native_buff_instances)
        self.assertEqual(predicted.characters["1"].energy, 0)
        self.assertTrue(self.world.native_buff_instances)
        self.assertEqual(self.world.time, 0)

    def test_teammate_cast_does_not_end_another_actors_bound_buff(self):
        self.add()
        self.world.advance(.5)
        other = ActionProgram("teammate", "2", "normal", 0, .1, 0, ())
        self.assertTrue(self.world.start(other, action_id="other"))
        self.world.advance(1)
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.assertEqual({v.action_scope for v in self.world.native_buff_instances.values()}, {"cast"})

    def test_native_root_binding_requires_no_children_or_skill_inheritance(self):
        store = SkillTimingStore()
        profile = store.profiles("佩丽卡", "battle")[0]
        source = native_record(store, profile.skill_id)
        node = copy.deepcopy(next(n for n in _nodes(source) if n["$type"].endswith("CreateBuffAction+Data")))
        body = node["$value"]
        body.update(autoFinishByAction=True, asChildBuff=False, inheritSkillIdList=[])
        body["targetSettings"]["targetSource"] = 1
        body["count"] = {"blackboardKey": "", "useBlackboardKey": False, "value": 1}
        body["buffs"] = [{"buffId": "buff_chr_0034_typhoea_normal_attack5_atb_recovered",
                          "assignBlackboard": True, "assignItems": [{"directValueType": 0, "targetKey": "atb",
                          "inputValueKey": "", "numericValue": 17, "stringValue": "", "useDirectValue": True}]}]
        record = copy.deepcopy(source)
        record["data"]["actionGroupData"]["passiveEventActions"] = []
        record["data"]["actionGroupData"]["timelineActions"] = [{"_startFrame": 3, "_endFrame": 18,
            "_sequenceActionData": {"actionData": [node]}}]
        buff_override = None
        def lookup(s, name):
            if name == profile.skill_id:
                return record
            if buff_override is not None and name == body["buffs"][0]["buffId"]:
                return buff_override
            return native_record(s, name)
        with patch("src.data.native_action_program.native_record", side_effect=lookup):
            program = compile_native_action(store, get_character("perlica"), profile, "1", "normal")
            changes = [v for event in walk_combat_events(program.events) for v in event.native_buffs]
            self.assertEqual([v.action_finish_after for v in changes], [.5])
            self.world.sp = 200
            self.world.start(replace(program, sp_cost=0, duration=.25), action_id="real")
            self.world.advance(.1)
            self.assertEqual(self.world.sp, 217)
            self.assertTrue(self.world.native_buff_instances)
            self.world.advance(.6)
            self.assertFalse(self.world.native_buff_instances)
            self.assertFalse(self.world.unresolved)
            for field, value in [("asChildBuff", True), ("inheritSkillIdList", ["chr_0004_pelica_normal_skill"])]:
                with self.subTest(field=field):
                    body[field] = value
                    record["data"]["actionGroupData"]["timelineActions"][0]["_sequenceActionData"]["actionData"] = [node]
                    blocked = compile_native_action(store, get_character("perlica"), profile, "1", "normal")
                    self.assertTrue(any("child/action-bound" in r for event in walk_combat_events(blocked.events)
                                        for r in event.unresolved))
                    body[field] = False if field == "asChildBuff" else []
            buff_override = native_record(store, body["buffs"][0]["buffId"])
            buff_override["data"]["triggerInterval"]["value"] = 1
            periodic = compile_native_action(store, get_character("perlica"), profile, "1", "normal")
            self.assertTrue(any("tick/end ordering" in r for event in walk_combat_events(periodic.events)
                                for r in event.unresolved))
            buff_override = None
            record["data"]["actionGroupData"]["timelineActions"][0]["_endFrame"] = 3
            instant = compile_native_action(store, get_character("perlica"), profile, "1", "normal")
            self.assertTrue(any("child/action-bound" in r for event in walk_combat_events(instant.events)
                                for r in event.unresolved))
