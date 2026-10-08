"""Confirmed raw marker fragments publish events; a cast is not a marker."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import ActionProgram, CombatWorldState
from src.data.native_action_program import compile_native_action
from src.data.native_buff_runtime import finish_buff_instances
from src.data.native_gameplay import native_record
from src.data.native_passive_program import NativePassiveProgram, compile_passive_producers
from src.data.native_passive_runtime import activate_passive
from src.data.native_zhuangfy_evidence import MARKER, PASSIVE
from src.data.skill_timing import SkillTimingStore


class TestNativeMarkerNotifications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("zhuang_fangyi")
        cls.profile = cls.store.profile("chr_0030_zhuangfy_normal_skill")
        producers, errors = compile_passive_producers(cls.store, cls.character, "1", cls.profile,
                                                    attributes={}, panel=None)
        assert not errors, errors
        cls.talent = next(row for row in producers if row.key.startswith(PASSIVE + ":"))

    def world(self):
        world = CombatWorldState(("1", "2"), regen=0)
        activate_passive(world, self.talent)
        return world

    def fragment(self, *, stance=False, extra=False):
        # The caller has confirmed the gate/frame. Selecting this actual raw
        # block does not implement CheckSquadInFight, animation, tick or Jump.
        skill = "chr_0030_zhuangfy_normal_skill"
        if extra:
            skill += "_ult_abilityrange"
            raw = native_record(self.store, skill)["data"]["actionGroupData"]["timelineActions"][6]
            sequence = dict(raw["_sequenceActionData"])
            sequence["actionData"] = [node for node in sequence["actionData"]
                                      if node["$type"].endswith("CreateBuffAction+Data")]
            self.assertEqual(len(sequence["actionData"]), 1)
        else:
            if stance:
                skill += "_ult"
            raw = native_record(self.store, skill)["data"]["actionGroupData"]["timelineActions"][10 if stance else 16]
            sequence = raw["_sequenceActionData"]["actionData"][0]["$value"]["succeedActions"]
        return compile_native_action(self.store, self.character, self.store.profile(skill), "1", "battle",
                                     event_sequence=sequence)

    def base(self, world):
        return next(row for row in world.native_buff_instances.values() if row.key == MARKER + "_base")

    def test_actual_basic_and_stance_marker_reach_selected_listener_in_native_order(self):
        for stance in (False, True):
            with self.subTest(stance=stance):
                world = self.world()
                with patch.object(world, "dispatch_native", wraps=world.dispatch_native) as dispatch:
                    self.assertTrue(world.start(self.fragment(stance=stance), action_id="confirmed-frame"))
                calls = [call for call in dispatch.call_args_list if call.args[0] in {"OnAddedBuff", "OnOutputBuff"}]
                self.assertEqual([call.args[0] for call in calls], ["OnAddedBuff", "OnOutputBuff"])
                for call in calls:
                    self.assertEqual(call.args[1], "1")
                    self.assertEqual(call.kwargs["target"], "1")
                    self.assertEqual(call.args[2]["event.skill_type"], 2)
                    self.assertEqual(call.args[2]["event.buff_id." + MARKER], 1)
                base = self.base(world)
                self.assertEqual((base.owner, base.source, base.expires), ("1", "1", 5))
                self.assertEqual(world._action_inputs[base.uid]["bb.base_rate"], .18000000715255737)
                self.assertEqual(world._action_inputs[base.uid]["event.skill_type"], 2)
                world.advance(.101)
                self.assertNotIn(("1", MARKER), world.native_buffs)
                self.assertIn(base.uid, world.native_buff_instances)
                self.assertTrue(any("EnhancedAction" in value for value in world.unresolved))
                self.assertNotIn("zhuangfy_talent1_marks", world.effective_attributes("1"))

    def test_replacement_deadlines_removal_and_prediction_are_independent(self):
        world = self.world()
        program = self.fragment()
        self.assertTrue(world.start(program, action_id="first"))
        old = self.base(world)
        world.advance(world.ready_at(program))
        self.assertTrue(world.start(program, action_id="second"))
        fresh = self.base(world)
        self.assertNotEqual(old.uid, fresh.uid)
        self.assertNotIn(old.uid, world.native_buff_instances)
        predicted = world.fork()
        finish_buff_instances(predicted, (fresh.uid,))
        self.assertIn(fresh.uid, world.native_buff_instances)
        world.advance(old.expires)
        self.assertIn(fresh.uid, world.native_buff_instances)
        world.advance(fresh.expires)
        self.assertNotIn(fresh.uid, world.native_buff_instances)

    def test_area_extra_marker_does_not_recreate_or_refresh_base(self):
        world = self.world()
        basic = self.fragment()
        self.assertTrue(world.start(basic, action_id="basic"))
        original = self.base(world)
        deadline = original.expires
        world.advance(world.actor_ready["1"])
        with patch.object(world, "dispatch_native", wraps=world.dispatch_native) as dispatch:
            self.assertTrue(world.start(self.fragment(extra=True), action_id="confirmed-area-mark"))
        self.assertIs(self.base(world), original)
        self.assertEqual(original.expires, deadline)
        added = next(call for call in dispatch.call_args_list if call.args[0] == "OnAddedBuff")
        self.assertEqual(added.args[2]["event.buff_id." + MARKER + "_mark"], 1)
        self.assertNotIn("source.zhuangfy_talent1_marks", world._action_inputs[original.uid])

    def test_cast_without_marker_and_unreviewed_producer_do_not_publish(self):
        world = self.world()
        with patch.object(world, "dispatch_native", wraps=world.dispatch_native) as dispatch:
            self.assertTrue(world.start(ActionProgram("plain-cast", "1", "battle", 100, 0, 0, ()), action_id="plain"))
        self.assertFalse(dispatch.called)
        self.assertFalse(world.native_buff_instances)
        with patch.object(world, "dispatch_native", wraps=world.dispatch_native) as dispatch:
            self.assertTrue(world.start(replace(self.fragment(), key="unreviewed"), action_id="unreviewed"))
        self.assertFalse(dispatch.called)
        self.assertFalse(any(row.key == MARKER + "_base" for row in world.native_buff_instances.values()))
        self.assertTrue(any("reviewed plain self shape" in value for value in world.unresolved))

    def test_pre_add_listener_blocks_unproven_post_notification(self):
        for actor in ("1", "2"):
            with self.subTest(actor=actor):
                world = self.world()
                empty = ActionProgram("pre-add", actor, "normal", 0, 0, 0, ())
                passive = NativePassiveProgram("pre-add", "test", empty, (("OnBeforeAddedBuff", empty),))
                activate_passive(world, passive)
                self.assertTrue(world.start(self.fragment(), action_id="marker"))
                if actor == "1":
                    self.assertFalse(any(row.key == MARKER + "_base" for row in world.native_buff_instances.values()))
                    self.assertTrue(any("pre-add callback" in value for value in world.unresolved))
                else:
                    self.assertEqual(self.base(world).owner, "1")

    def test_failed_or_duplicate_release_has_no_add_notifications(self):
        program = self.fragment()
        for reason in ("dead", "sp", "duplicate"):
            with self.subTest(reason=reason):
                world = self.world()
                if reason == "dead":
                    world.characters["1"].alive = False
                elif reason == "sp":
                    world.sp = 0
                else:
                    self.assertTrue(world.start(program, action_id="once"))
                with patch.object(world, "dispatch_native", wraps=world.dispatch_native) as dispatch:
                    self.assertFalse(world.start(program, action_id="once"))
                self.assertFalse(dispatch.called)
