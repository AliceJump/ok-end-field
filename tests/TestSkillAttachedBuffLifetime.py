"""Confirmed CastEnd transfers exact instances; handoff does not supply context."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeTarget,
    walk_combat_events,
)
from src.data.damage_resolution import FixedDamagePanel
from src.data.native_ability_runtime import disable_ability
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_buff_runtime import finish_buff_instances
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


class TestCreateBuffAttachingSkill(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.store = SkillTimingStore()
        self.profile = self.store.profile("chr_0027_tangtang_ultimate_skill_1")
        self.vfx = "buff_chr_0027_tangtang_ultskill_vfx"
        source = native_record(self.store, self.profile.skill_id)
        node = copy.deepcopy(next(n for n in _nodes(source) if n["$type"].endswith("CreateBuffAttachingSkill+Data")))
        record = copy.deepcopy(source)
        record["data"]["actionGroupData"]["passiveEventActions"] = []
        record["data"]["actionGroupData"]["timelineActions"] = [{"_startFrame": 0, "_endFrame": 123,
            "_sequenceActionData": {"actionData": [node]}}]
        def lookup(s, key):
            return record if key == self.profile.skill_id else native_record(s, key)
        with patch("src.data.native_action_program.native_record", side_effect=lookup):
            compiled = compile_native_action(self.store, get_character("tangtang"), self.profile, "1", "ult")
        self.change, = [c for event in walk_combat_events(compiled.events) for c in event.native_buffs]
        self.assertTrue(self.change.attach_to_event_skill)
        self.assertEqual(self.change.action_finish_after, 4.1)
        self.assertEqual(self.change.definition.duration.evaluate({}), 6)

    def install(self, change=None, *, extra=()):
        callback = ActionProgram("callback_is_not_the_cast_skill", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "create", native_buffs=(change or self.change,)),))
        definition = NativeBuffProgram((), (), None, literal(-1), literal(0), False, 0, literal(0), (),
                                       subscriptions=(("probe", callback), *extra))
        program = ActionProgram("holder", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "root", native_buffs=(NativeBuffChange(
                                     "listener", literal(1), selector=NativeTarget("owner"), definition=definition),)),))
        self.assertTrue(self.world.start(program, action_id="holder"))
        return next(iter(self.world.native_buff_instances))

    def cast(self, scope="caster", *, key="actual_skill", actor="2"):
        self.assertTrue(self.world.start(ActionProgram(key, actor, "normal", 0, 0, 0, ()), action_id=scope))

    def test_actual_creation_attaches_only_to_explicit_event_skill_and_cast_end_cleans(self):
        parent = self.install()
        self.cast()
        self.world.dispatch_native("probe", "1", cast_action="caster")
        uid, = [v.uid for v in self.world.native_buff_instances.values() if v.key == self.vfx]
        self.assertEqual(self.world.native_buff_instances[uid].owner, "1")
        self.assertEqual(self.world.native_skill_buffs, {("2", "actual_skill"): [uid]})
        self.assertIsNone(self.world._native_cast_context)
        self.assertTrue(finish_skill_cast(self.world, "caster", reason=0))
        self.assertNotIn(uid, self.world.native_buff_instances)
        self.assertIn(parent, self.world.native_buff_instances)
        self.assertFalse(self.world.unresolved)

    def test_action_end_removes_exact_instance_without_waiting_for_cast_end(self):
        self.install()
        self.cast()
        self.world.dispatch_native("probe", "1", cast_action="caster")
        uid, = self.world.native_skill_buffs["2", "actual_skill"]
        self.world.advance(4.1)
        self.assertNotIn(uid, self.world.native_buff_instances)
        self.assertTrue(finish_skill_cast(self.world, "caster", reason=0))
        self.assertFalse(self.world.native_skill_buffs["2", "actual_skill"])
        self.assertEqual(self.world.native_buff_count("1", "listener"), 1)

    def test_name_payload_or_holder_skill_does_not_supply_cast_context(self):
        self.install()
        self.cast()
        self.world.dispatch_native("probe", "1", {"event.skill_type": 7, "event.skill_id.actual_skill": 1})
        self.assertEqual(self.world.native_buff_count("1", self.vfx), 1)
        self.assertFalse(self.world.native_skill_buffs)
        self.assertIn(f"Native skill-attached buff lacks CastSkillContext: {self.vfx}", self.world.unresolved)
        self.assertIsNone(self.world._native_cast_context)

    def test_invalid_superseded_or_ended_cast_cannot_authorize_callback(self):
        self.install()
        self.cast("old")
        self.cast("new")
        for scope in ("unknown", "old"):
            self.assertFalse(self.world.dispatch_native("probe", "1", cast_action=scope))
            self.assertEqual(self.world.native_buff_count("1", self.vfx), 0)
        self.assertTrue(finish_skill_cast(self.world, "new", reason=0))
        self.assertFalse(self.world.dispatch_native("probe", "1", cast_action="new"))
        self.assertEqual(self.world.native_buff_count("1", self.vfx), 0)

    def test_unique_reapplication_does_not_attach_old_instance_to_another_cast(self):
        definition = NativeBuffProgram((), (), None, literal(-1), literal(0), False, 7, literal(0), ())
        change = NativeBuffChange("unique", literal(1), selector=NativeTarget("owner"), definition=definition,
                                  attach_to_event_skill=True)
        self.install(change)
        self.cast("first", key="first_skill")
        self.world.dispatch_native("probe", "1", cast_action="first")
        uid, = self.world.native_skill_buffs["2", "first_skill"]
        self.cast("second", key="second_skill")
        self.world.dispatch_native("probe", "1", cast_action="second")
        self.assertNotIn(("2", "second_skill"), self.world.native_skill_buffs)
        self.assertTrue(finish_skill_cast(self.world, "second", reason=0))
        self.assertIn(uid, self.world.native_buff_instances)
        self.assertTrue(finish_skill_cast(self.world, "first", reason=0))
        self.assertNotIn(uid, self.world.native_buff_instances)

    def test_fork_attachment_and_parent_cleanup_do_not_touch_original(self):
        parent = self.install(replace(self.change, child_of_buff=True, action_finish_after=None))
        self.cast()
        predicted = self.world.fork()
        predicted.dispatch_native("probe", "1", cast_action="caster")
        uid, = predicted.native_skill_buffs["2", "actual_skill"]
        self.assertEqual(predicted.native_buff_instances[uid].parent_scope, parent)
        finish_buff_instances(predicted, (parent,))
        self.assertNotIn(uid, predicted.native_buff_instances)
        self.assertTrue(finish_skill_cast(predicted, "caster", reason=0))
        self.assertIn(parent, self.world.native_buff_instances)
        self.assertFalse(self.world.native_skill_buffs)
        self.assertIsNone(self.world._native_cast_context)

    def test_unrelated_nested_event_cannot_borrow_outer_cast_context(self):
        from src.data.native_skill_runtime import attach_created_buff

        inner_definition = NativeBuffProgram((), (), None, literal(-1), literal(0), False, 0, literal(0), ())
        inner_change = NativeBuffChange("nested", literal(1), selector=NativeTarget("owner"),
                                        definition=inner_definition, attach_to_event_skill=True)
        inner = ActionProgram("nested_callback", "1", "normal", 0, 0, 0,
                              (CombatEvent(0, "nested", native_buffs=(inner_change,)),))
        self.install(extra=(("nested_probe", inner),))
        self.cast()
        def attach(world, instance):
            if instance.key == self.vfx:
                world.dispatch_native("nested_probe", "1")
            return attach_created_buff(world, instance)
        with patch("src.data.native_skill_runtime.attach_created_buff", side_effect=attach):
            self.world.dispatch_native("probe", "1", cast_action="caster")
        uids = self.world.native_skill_buffs["2", "actual_skill"]
        self.assertEqual([self.world.native_buff_instances[v].key for v in uids], [self.vfx])
        self.assertEqual(self.world.native_buff_count("1", "nested"), 1)
        self.assertIn("Native skill-attached buff lacks CastSkillContext: nested", self.world.unresolved)
        self.assertIsNone(self.world._native_cast_context)

    def test_yvonne_refresh_attacker_buff_still_keeps_explicit_diagnostic(self):
        source = native_record(self.store, "buff_chr_0017_yvonne_talent_1_valid")["data"]
        block, = source["abilityEventAction"][0]["actions"]
        program = compile_native_action(self.store, get_character("yvonne"), self.profile, "1", "normal",
                                        event_sequence=block, buff_path=(source["id"],))
        reasons = [r for e in walk_combat_events(program.events) for r in e.unresolved]
        self.assertTrue(any("skill-attached buff needs reviewed instance policy" in r for r in reasons))
        self.assertFalse([c for e in walk_combat_events(program.events) for c in e.native_buffs])

    def yvonne_listener(self, block=None):
        data = native_record(self.store, "buff_chr_0017_yvonne_talent_1_valid")["data"]
        callback = compile_native_action(self.store, get_character("yvonne"),
                                         self.store.profile("chr_0017_yvonne_attack5"), "1", "normal",
                                         event_sequence=block or data["abilityEventAction"][0]["actions"][0],
                                         buff_path=(data["id"],))
        definition = NativeBuffProgram((), (), None, literal(-1), literal(0), False, 0, literal(0), (),
                                       subscriptions=(("OnBeforeCastSkill", callback),))
        program = ActionProgram("ready_contract", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "root", native_buffs=(NativeBuffChange(
                                     "ready_contract", literal(1), selector=NativeTarget("owner"), definition=definition),)),))
        self.assertTrue(self.world.start(program, action_id="ready_contract"))
        return callback

    def test_actual_skill_id_gate_recomputes_from_current_context_and_clears_old_true(self):
        callback = self.yvonne_listener()
        self.assertEqual(callback.native_cast_skill_ids, ("chr_0017_yvonne_attack5",))
        self.cast("heavy", key="chr_0017_yvonne_attack5")
        self.world.dispatch_native("OnBeforeCastSkill", "1", cast_action="heavy")
        self.assertTrue(any("skill-attached buff needs reviewed instance policy" in r for r in self.world.unresolved))
        self.world.unresolved.clear()
        self.cast("light", key="chr_0017_yvonne_attack1")
        self.world.dispatch_native("OnBeforeCastSkill", "1", cast_action="light")
        self.assertFalse(self.world.unresolved)
        self.world.dispatch_native("OnBeforeCastSkill", "1", cast_action="heavy")
        self.assertTrue(self.world.unresolved)
        self.world.unresolved.clear()
        key = "event.cast_skill.chr_0017_yvonne_attack5"
        self.world.dispatch_native("OnBeforeCastSkill", "1", {key: 1})
        self.assertTrue(any("Missing combat input: " + key in r for r in self.world.unresolved))
        self.assertFalse(any("skill-attached buff needs reviewed instance policy" in r for r in self.world.unresolved))
        self.assertEqual(self.world.native_buff_count("1", "buff_chr_0017_yvonne_talent_1_valid_up"), 0)

    def test_literal_skill_list_is_any_match_and_dynamic_references_stay_unknown(self):
        data = native_record(self.store, "buff_chr_0017_yvonne_talent_1_valid")["data"]
        block = copy.deepcopy(data["abilityEventAction"][0]["actions"][0])
        identities = block["actionData"][0]["$value"]["skillIdList"]
        identities.extend([dict(identities[0], value="other"), dict(identities[0], value="third")])
        callback = self.yvonne_listener(block)
        self.assertEqual(len(callback.native_cast_skill_ids), 3)
        self.cast("third", key="third")
        self.world.dispatch_native("OnBeforeCastSkill", "1", cast_action="third")
        self.assertTrue(any("reviewed instance policy" in r for r in self.world.unresolved))
        block["actionData"][0]["$value"]["skillIdList"][0]["useBlackboardKey"] = True
        unknown = compile_native_action(self.store, get_character("yvonne"), self.profile, "1", "normal",
                                        event_sequence=block, buff_path=(data["id"],))
        self.assertTrue(any("Dynamic/unknown native event skill identity" in r
                            for e in walk_combat_events(unknown.events) for r in e.unresolved))
        self.assertFalse([c for e in walk_combat_events(unknown.events) for c in e.native_buffs])

    def test_actual_yvonne_same_frame_preserves_damage_then_ready_cleanup(self):
        profile = self.store.profile("chr_0017_yvonne_attack5")
        program = compile_native_action(self.store, get_character("yvonne"), profile, "1", "normal",
                                        panel=FixedDamagePanel(100, 0, 0, 1, 0, .5))
        events = [e for e in walk_combat_events(program.events) if e.hit or any(
            c.key == "buff_chr_0017_yvonne_talent_1_valid" for c in e.native_buffs)]
        self.assertEqual(len(events), 2)
        self.assertEqual([e.at for e in events], [.7, .7])
        self.assertIsNotNone(events[0].hit)
        self.assertEqual(events[1].native_buffs[0].key, "buff_chr_0017_yvonne_talent_1_valid")
        self.assertTrue(events[1].native_buffs[0].remove_all)
