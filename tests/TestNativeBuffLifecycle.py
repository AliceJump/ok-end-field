"""Buff callbacks retain their holder, parameter snapshot and native clocks."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, MissingCombatInput, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeResourceChange,
    NativeTarget,
    UnresolvedMechanic,
)
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_buff_program import (
    LAEVAT_STANCE,
    LAEVAT_ULT_END,
    POGRANI_MORALE,
    compile_buff_definition,
    reviewed_duration_extend,
    reviewed_retained_layers,
)
from src.data.native_buff_runtime import change_buff, finish_buff_instances, pause_buff_time
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import CombatResourceType


def literal(value):
    return CombatExpression("literal", (float(value),))


class TestNativeBuffLifecycle(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), sp=100, regen=0)

    def definition(self, *, duration=3, period=1, limit=-1, wait=True, stacking=2, maximum=3, callbacks=None):
        payout = NativeResourceChange(CombatResourceType.ULTIMATE_ENERGY, combat_input("bb.gain"), literal(1),
                                      NativeTarget("source"), NativeTarget("owner"), ignore_energy_gain=True)
        tick = ActionProgram("buff_tick", "1", "normal", 0, 0, 0, (CombatEvent(0, "tick", native_resources=(payout,)),))
        return NativeBuffProgram((("bb.gain", 1.0),), (("bb.gain", combat_input("bb.inherit")),),
                                 None if duration is None else literal(duration), literal(period), literal(limit), wait,
                                 stacking, literal(maximum), callbacks if callbacks is not None else ((1, tick),))

    def add(self, definition, *, action_id="add", amount=7, target="main"):
        self.world.main_control = "2"
        change = NativeBuffChange("test_buff", literal(1), selector=NativeTarget(target), definition=definition)
        program = ActionProgram("producer", "1", "normal", 0, 0, 0,
                                (CombatEvent(0, "buff", native_buffs=(change,)),), parameters=(("bb.inherit", amount),))
        self.assertTrue(self.world.start(program, action_id=action_id))

    def test_tick_uses_holder_and_captured_values_including_deadline_once(self):
        self.add(self.definition())
        self.assertEqual(self.world.characters["2"].energy, 0)
        self.world._action_inputs["add"]["bb.inherit"] = 100
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 21)
        self.assertEqual(self.world.characters["1"].energy, 0)
        self.assertFalse(self.world.native_buff_instances)
        self.assertFalse(self.world.native_buffs)
        self.world.advance(10)
        self.assertEqual(self.world.characters["2"].energy, 21)
        self.assertFalse(self.world.unresolved)

    def test_wait_false_triggers_now_and_finite_limit_does_not_expire_buff_early(self):
        self.add(self.definition(duration=5, limit=2, wait=False))
        self.assertEqual(self.world.characters["2"].energy, 7)
        self.world.advance(2)
        self.assertEqual(self.world.characters["2"].energy, 14)
        self.assertIn(("2", "test_buff"), self.world.native_buffs)
        self.world.advance(5)
        self.assertNotIn(("2", "test_buff"), self.world.native_buffs)

    def test_zero_limit_disables_periodic_trigger_and_removal_cancels_pending_ticks(self):
        self.add(self.definition(limit=0))
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 0)
        self.add(self.definition(), action_id="later")
        remove = NativeBuffChange("test_buff", literal(-1), remove_all=True, selector=NativeTarget("main"))
        self.world.start(ActionProgram("remove", "1", "normal", 0, 0, 0,
                                       (CombatEvent(0, "remove", native_buffs=(remove,)),)), action_id="remove")
        self.world.advance(10)
        self.assertEqual(self.world.characters["2"].energy, 0)
        self.assertFalse(self.world.native_buff_instances)

    def test_independent_layers_expire_separately_and_stack_overflow_replaces_oldest(self):
        definition = self.definition(duration=3, limit=0, maximum=2)
        self.add(definition)
        first_uid = next(iter(self.world.native_buff_instances))
        self.world.advance(1)
        self.add(definition, action_id="second")
        self.assertEqual(self.world.native_buffs["2", "test_buff"][0], 2)
        self.world.advance(2)
        self.add(definition, action_id="overflow")
        self.assertNotIn(first_uid, self.world.native_buff_instances)
        self.world.advance(3)
        self.assertEqual(self.world.native_buffs["2", "test_buff"][0], 2)
        self.world.advance(4)
        self.assertEqual(self.world.native_buffs["2", "test_buff"][0], 1)
        self.world.advance(5)
        self.assertFalse(self.world.native_buffs)

    def test_unique_reapplication_preserves_first_parameters_and_deadline(self):
        definition = self.definition(stacking=7)
        self.add(definition)
        self.world.advance(1)
        self.add(definition, action_id="second", amount=100)
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 21)
        self.assertFalse(self.world.native_buffs)

    def test_finish_callback_fires_once_and_listener_keeps_its_holder(self):
        payout = NativeResourceChange(CombatResourceType.ULTIMATE_ENERGY, literal(5), literal(1),
                                      NativeTarget("source"), NativeTarget("owner"), ignore_energy_gain=True)
        callback = ActionProgram("callback", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "callback", native_resources=(payout,)),))
        from dataclasses import replace

        definition = replace(self.definition(limit=0, callbacks=((2, callback),)),
                             subscriptions=(("OnBeforeOutputPhysicalInfliction", callback),))
        self.add(definition)
        self.world.dispatch_native("OnBeforeOutputPhysicalInfliction", "2")
        self.assertEqual(self.world.characters["2"].energy, 5)
        self.world.advance(3)
        self.assertEqual(self.world.characters["2"].energy, 10)
        self.world.dispatch_native("OnBeforeOutputPhysicalInfliction", "2")
        self.world.advance(10)
        self.assertEqual(self.world.characters["2"].energy, 10)

    def test_counterfactual_buff_scope_and_pending_ticks_do_not_touch_original(self):
        predicted = self.world.fork()
        actual = self.world
        self.world = predicted
        self.add(self.definition(wait=False))
        self.world.advance(2)
        self.assertEqual(predicted.characters["2"].energy, 21)
        self.assertEqual(actual.characters["2"].energy, 0)
        self.assertFalse(actual.native_buff_instances)
        self.assertFalse(actual._queue)

    def test_real_typhoea_buff_executes_start_resource_with_inherited_blackboard(self):
        store = SkillTimingStore()
        profile = store.battle_phase_profiles("弭弗")[0]
        source = copy.deepcopy(store.record(profile.skill_id)["data"]["actionGroupData"]["timelineActions"][0]["_sequenceActionData"])
        node = copy.deepcopy(next(n for n in _nodes(store.record(profile.skill_id)["data"])
                                  if n["$type"].endswith("CreateBuffAction+Data")))
        node["$value"]["count"] = {"blackboardKey": "", "useBlackboardKey": False, "value": 1}
        node["$value"]["targetSettings"]["targetSource"] = 1
        node["$value"]["buffs"] = [{"buffId": "buff_chr_0034_typhoea_normal_attack5_atb_recovered",
                                   "assignBlackboard": True, "assignItems": [{"directValueType": 0,
                                   "inputValueKey": "inherit", "numericValue": 0, "stringValue": "",
                                   "targetKey": "atb", "useDirectValue": False}]}]
        source["actionData"] = [node]
        program = compile_native_action(store, get_character("mi_fu"), profile, "1", "battle",
                                        event_sequence=source, event_blackboard={"inherit": 17})
        self.world.sp = 200
        self.world.start(program, action_id="real_buff")
        self.assertEqual(self.world.sp, 117)
        self.assertEqual(self.world.returned_sp, 0)
        # The copied CreateBuff action requests action-bound removal. The
        # resource producer is known; that extra lifetime remains incomplete.
        self.assertEqual(self.world.unresolved, {"Native child/action-bound buff lifetime not yet bound"})
        self.assertIn(("1", "buff_chr_0034_typhoea_normal_attack5_atb_recovered"), self.world.native_buffs)
        self.world.advance(.3)
        self.assertFalse(self.world.native_buff_instances)


class TestRetainedMoraleLayers(unittest.TestCase):
    """Explicitly supplied morale instances; the 80-SP producer is not assumed."""

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.store = SkillTimingStore()
        self.data = native_record(self.store, POGRANI_MORALE)["data"]
        self.character = get_character("pogranichnik")
        self.profile = self.store.profiles("骏卫", "battle")[0]
        reference = {"assignBlackboard": True, "assignItems": [
            {"directValueType": 0, "useDirectValue": False, "targetKey": key, "inputValueKey": key}
            for key in ("duration", "atk_up", "physpell_up", "max_stack")]}
        self.definition = compile_buff_definition(self.store, self.character, self.profile, "1", POGRANI_MORALE,
                                                 self.data, reference, attributes=None, panel=None, path=())
        self.program = ActionProgram("explicit_morale_instance", "1", "normal", 0, 0, 0, ())
        self.assertTrue(self.definition.retained_priority_layers)

    def add(self, duration=20, *, owner="1", definition=None, maximum=3):
        change = NativeBuffChange(POGRANI_MORALE, literal(1), definition=definition or self.definition)
        values = {"bb.duration": duration, "bb.atk_up": .08, "bb.physpell_up": 8, "bb.max_stack": maximum}
        change_buff(self.world, owner, change, values, "explicit", self.program, 1)
        return next(reversed(self.world.native_buff_instances))

    def test_overflow_is_retained_and_counted_but_only_three_candidates_are_enabled(self):
        uids = [self.add() for _ in range(4)]
        self.assertEqual(self.world.native_buff_count("1", POGRANI_MORALE), 4)
        self.assertEqual([v.enabled for v in self.world.native_buff_instances.values()], [True, True, True, False])
        self.assertEqual(self.world._action_inputs[uids[-1]]["bb.atk_up"], .08)
        self.assertEqual(self.world._action_inputs[uids[-1]]["bb.physpell_up"], 8)
        self.assertIn(f"Native buff modifier needs binding: {POGRANI_MORALE}/attributeModifier", self.world.unresolved)
        self.assertFalse(self.definition.attack_additions)

    def test_longer_layer_disables_old_candidate_without_restarting_its_clock(self):
        old = [self.add(10) for _ in range(3)]
        self.world.advance(1)
        newer = self.add(20)
        self.assertTrue(self.world.native_buff_instances[newer].enabled)
        self.assertFalse(self.world.native_buff_instances[old[-1]].enabled)
        self.assertEqual(self.world.native_buff_instances[old[-1]].expires, 10)
        self.world.advance(10)
        self.assertEqual(set(self.world.native_buff_instances), {newer})
        self.assertEqual(self.world.native_buff_count("1", POGRANI_MORALE), 1)
        self.world.advance(21)
        self.assertFalse(self.world.native_buff_instances)

    def test_finish_reenables_survivor_and_does_not_refresh_or_merge_owners(self):
        active = [self.add(20) for _ in range(3)]
        self.world.advance(1)
        inactive = self.add(10)
        other_owner = self.add(10, owner="2")
        self.assertFalse(self.world.native_buff_instances[inactive].enabled)
        finish_buff_instances(self.world, (active[0],))
        self.assertTrue(self.world.native_buff_instances[inactive].enabled)
        self.assertEqual(self.world.native_buff_instances[inactive].expires, 11)
        self.assertTrue(self.world.native_buff_instances[other_owner].enabled)
        self.assertEqual(self.world.native_buff_count("1", POGRANI_MORALE), 3)
        self.world.advance(11)
        self.assertNotIn(inactive, self.world.native_buff_instances)
        self.assertNotIn(other_owner, self.world.native_buff_instances)
        self.assertEqual(self.world.native_buff_count("1", POGRANI_MORALE), 2)

    def test_float32_epsilon_tie_keeps_earlier_uid_and_fork_selection_is_isolated(self):
        old = [self.add(20) for _ in range(3)]
        near = self.add(20.000005)
        self.assertFalse(self.world.native_buff_instances[near].enabled)
        predicted = self.world.fork()
        finish_buff_instances(predicted, (old[0],))
        self.assertTrue(predicted.native_buff_instances[near].enabled)
        self.assertFalse(self.world.native_buff_instances[near].enabled)
        self.world.advance(20)
        self.assertEqual(set(self.world.native_buff_instances), {near})
        self.assertTrue(self.world.native_buff_instances[near].enabled)

    def test_unreviewed_priority_period_callbacks_or_group_maximum_stay_unknown(self):
        for field, value in (("usePriorityKey", True), ("priority", 1), ("negatePriority", True)):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data["stackingSettings"][field] = value
                self.assertFalse(reviewed_retained_layers(POGRANI_MORALE, data))
        for definition in (replace(self.definition, retained_priority_layers=False),
                           replace(self.definition, period=literal(1)),
                           replace(self.definition, callbacks=((0, self.program),))):
            with self.subTest(definition=definition):
                with self.assertRaises(UnresolvedMechanic):
                    self.add(definition=definition)
                self.assertFalse(self.world.native_buff_instances)
        self.add()
        with self.assertRaisesRegex(UnresolvedMechanic, "group maximum change"):
            self.add(maximum=2)

    def test_inactive_unknown_attack_input_does_not_contaminate_enabled_modifiers(self):
        from src.data.native_attribute_modifiers import attack_addition

        # Exercise the existing modifier consumer independently; actual morale
        # ATK+arts-strength compilation remains unresolved above.
        definition = replace(self.definition, attack_additions=(combat_input("bb.atk_up"),))
        for _ in range(3):
            self.add(definition=definition)
        inactive = self.add(definition=definition)
        self.world.native_buff_instances[inactive].attack_addition = None
        self.assertAlmostEqual(attack_addition(self.world, "1"), .24)
        first = next(iter(self.world.native_buff_instances))
        finish_buff_instances(self.world, (first,))
        with self.assertRaisesRegex(MissingCombatInput, "Native attack modifier input"):
            attack_addition(self.world, "1")

    def test_original_create_node_keeps_parent_and_reselects_other_roots_on_cleanup(self):
        source = native_record(self.store, "chr_0029_pograni_talent1")["data"]
        node = next(n for n in _nodes(source) if n["$type"].endswith("CreateBuffAction+Data"))
        self.assertTrue(node["$value"]["asChildBuff"])
        # Isolate the original creation node after an explicitly supplied trigger.
        # This does not bypass the unresolved SP producer in a whole talent replay.
        creator = compile_native_action(self.store, self.character, self.profile, "1", "battle",
                                        event_sequence={"actionData": [node]}, buff_path=("explicit_parent",),
                                        event_blackboard={"duration": 20, "atk_up": .08,
                                                          "physpell_up": 8, "max_stack_owner": 3})
        changes = [change for event in creator.events for change in event.native_buffs]
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0].definition.retained_priority_layers)
        self.assertTrue(changes[0].child_of_buff)
        parent_definition = NativeBuffProgram((), (), literal(30), literal(-1), literal(0), False, 0, literal(0),
                                              ((0, creator),))
        for _ in range(4):
            change_buff(self.world, "1", NativeBuffChange("explicit_parent", literal(1), definition=parent_definition),
                        {}, "parent_trigger", self.program, 1)
        children = [v for v in self.world.native_buff_instances.values() if v.key == POGRANI_MORALE]
        self.assertEqual([v.enabled for v in children], [True, True, True, False])
        self.assertEqual(len({v.parent_scope for v in children}), 4)
        self.assertEqual(self.world._action_inputs[children[0].uid]["bb.atk_up"], .08)
        finish_buff_instances(self.world, (children[0].parent_scope,))
        self.assertNotIn(children[0].uid, self.world.native_buff_instances)
        self.assertTrue(self.world.native_buff_instances[children[-1].uid].enabled)
        self.world.advance(20)
        self.assertEqual(self.world.native_buff_count("1", POGRANI_MORALE), 0)
        self.assertEqual(self.world.native_buff_count("1", "explicit_parent"), 3)
        self.world.advance(30)
        self.assertFalse(self.world.native_buff_instances)


class TestNativePausedBuffClock(unittest.TestCase):
    """Authored clock scenarios; no assumed Laevatain stance producer or ticks."""

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.definition = TestNativeBuffLifecycle().definition

    def add(self, *, key="stance", owner="1", definition=None, end=None):
        definition = definition or self.definition(period=-1, callbacks=())
        change = NativeBuffChange(key, literal(1), target="self", definition=definition, action_finish_after=end)
        program = ActionProgram("confirmed_"+key, owner, "normal", 0, 0, 0,
                                (CombatEvent(0, "create", native_buffs=(change,)),), parameters=(("bb.inherit", 7),))
        self.assertTrue(self.world.start(program, action_id="add_"+key))
        return next(v for v in self.world.native_buff_instances.values() if v.key == key)

    def pause(self, instance, paused, *, world=None):
        world = world or self.world
        previous = world._native_buff_context
        world._native_buff_context = instance
        try:
            pause_buff_time(world, instance.uid, paused)
        finally:
            world._native_buff_context = previous

    def test_pause_preserves_remainder_and_does_not_pause_weapon_or_another_actor(self):
        stance = self.add()
        self.add(key="weapon", owner="1")
        self.add(key="other", owner="2")
        self.world.advance(.5)
        self.pause(stance, True)
        self.world.advance(10)
        self.assertEqual(set(self.world.native_buff_instances), {stance.uid})
        self.assertEqual(stance.paused_lifetime, 2.5)
        self.assertEqual(self.world.native_buff_count("1", "stance"), 1)
        self.pause(stance, False)
        self.assertEqual(stance.expires, 12.5)
        self.world.advance(12.49)
        self.assertIn(stance.uid, self.world.native_buff_instances)
        self.world.advance(12.5)
        self.assertFalse(self.world.native_buff_instances)

    def test_periodic_timer_also_pauses_and_deadline_trigger_is_paid_once(self):
        stance = self.add(definition=self.definition())
        self.world.advance(1.25)
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.pause(stance, True)
        self.world.advance(20)
        self.assertEqual(self.world.characters["1"].energy, 7)
        self.pause(stance, False)
        self.assertEqual(stance.next_trigger, 20.75)
        self.assertEqual(stance.expires, 21.75)
        self.world.advance(21.75)
        self.assertEqual(self.world.characters["1"].energy, 21)
        self.assertFalse(self.world.native_buff_instances)

    def test_same_time_toggles_and_duplicate_notifications_do_not_duplicate_ticks(self):
        stance = self.add(definition=self.definition())
        self.world.advance(.25)
        for _ in range(3):
            self.pause(stance, True)
            self.pause(stance, True)
            self.pause(stance, False)
            self.pause(stance, False)
        self.world.advance(3)
        self.assertEqual(self.world.characters["1"].energy, 21)
        self.assertFalse(self.world.native_buff_instances)

    def test_action_end_and_explicit_removal_still_finish_paused_instances(self):
        stance = self.add(end=1)
        self.world.advance(.25)
        self.pause(stance, True)
        self.world.advance(1)
        self.assertFalse(self.world.native_buff_instances)
        with self.assertRaisesRegex(UnresolvedMechanic, "owner"):
            self.pause(stance, False)
        stance = self.add(key="second")
        self.pause(stance, True)
        finish_buff_instances(self.world, (stance.uid,))
        self.assertFalse(self.world.native_buff_instances)

    def test_prediction_fork_can_resume_without_changing_the_observed_world(self):
        stance = self.add()
        self.pause(stance, True)
        forecast = self.world.fork()
        forecast.advance(5)
        self.pause(forecast.native_buff_instances[stance.uid], False, world=forecast)
        forecast.advance(8)
        self.assertFalse(forecast.native_buff_instances)
        self.assertTrue(self.world.native_buff_instances[stance.uid].time_paused)
        self.assertEqual(self.world.time, 0)

    def test_infinite_lifetime_survives_pause_resume_without_a_fake_expiry(self):
        stance = self.add(definition=self.definition(duration=None, period=-1, callbacks=()))
        self.pause(stance, True)
        self.world.advance(100)
        self.pause(stance, False)
        self.assertIsNone(stance.expires)
        self.world.advance(1000)
        self.assertEqual(self.world.native_buff_count("1", "stance"), 1)

    def test_pause_without_buff_owner_is_unknown_not_a_team_clock_switch(self):
        stance = self.add()
        action = ActionProgram("not_a_buff", "1", "normal", 0, 0, 0,
                               (CombatEvent(0, "wrong_root", buff_pause=True),))
        self.world.start(action, action_id="wrong")
        self.assertFalse(stance.time_paused)
        self.assertTrue(any("executing Buff owner" in error for error in self.world.unresolved))
        self.assertEqual(stance.expires, 3)

    def test_periodic_initialization_pause_keeps_zero_dt_trigger_order_unknown(self):
        start = ActionProgram("pause_on_start", "1", "normal", 0, 0, 0,
                              (CombatEvent(0, "pause", buff_pause=True),))
        definition = self.definition(wait=False)
        definition = replace(definition, callbacks=((0, start), *definition.callbacks))
        stance = self.add(definition=definition)
        self.assertFalse(stance.time_paused)
        self.assertTrue(any("initialization/trigger boundary" in error for error in self.world.unresolved))
        self.world.advance(3)
        # Rejected pause leaves the existing explicit immediate-trigger rule.
        # There are four opportunities (start plus 1/2/3), not a guessed skip.
        self.assertEqual(self.world.characters["1"].energy, 28)
        self.assertFalse(self.world.native_buff_instances)

    def test_refresh_of_paused_finite_buff_does_not_turn_it_into_infinite_life(self):
        from src.data.native_attribute_modifiers import ALESH_TEAM_ATTACK

        definition = self.definition(stacking=4, period=-1, callbacks=())
        stance = self.add(key=ALESH_TEAM_ATTACK, definition=definition)
        self.world.advance(.5)
        self.pause(stance, True)
        self.world.advance(10)
        refreshed = replace(definition, duration=literal(7))
        change_buff(self.world, "1", NativeBuffChange(ALESH_TEAM_ATTACK, literal(1), definition=refreshed),
                    {"bb.inherit": 7}, "later_source", stance.program, 1)
        self.assertEqual(stance.paused_lifetime, 7)
        self.assertEqual(len(self.world.native_buff_instances), 1)
        self.pause(stance, False)
        self.assertEqual(stance.expires, 17)
        self.world.advance(17)
        self.assertFalse(self.world.native_buff_instances)

    def test_unreviewed_child_clock_propagation_is_rejected_without_mutation(self):
        stance = self.add()
        child = self.add(key="child")
        child.parent_scope = stance.uid  # Explicitly supplied parent relationship.
        with self.assertRaisesRegex(UnresolvedMechanic, "child clocks"):
            self.pause(stance, True)
        self.assertFalse(stance.time_paused)
        self.assertEqual(stance.expires, 3)
        self.assertIsNone(stance.paused_lifetime)

    def test_real_laevatain_full_pause_callbacks_use_explicit_event_id(self):
        store = SkillTimingStore()
        character = get_character("laevatain")
        source = native_record(store, "buff_chr_0016_laevat_show_weapon")["data"]
        subscriptions = []
        flags = []
        for event in source["abilityEventAction"]:
            for block in event["actions"]:
                callback = compile_native_action(store, character, store.profiles("莱万汀", "ult")[0], "1", "normal",
                                                 event_sequence=block, isolated_blackboard=True)
                self.assertFalse([error for e in callback.events for error in e.unresolved])
                flags += [e.buff_pause for e in callback.events if e.buff_pause is not None]
                subscriptions.append(({9: "OnAddedBuff", 203: "OnFinishedBuff"}[event["abilityEvent"]], callback))
        self.assertEqual(flags, [True, False])
        # The full actual condition/Pause callbacks execute on a confirmed plain
        # holder. The type-5 container and automatic event producer are separate.
        definition = replace(self.definition(period=-1, callbacks=()), subscriptions=tuple(subscriptions))
        stance = self.add(definition=definition)
        self.world.advance(.5)
        key = "buff_chr_0016_laevat_pause_ult"
        self.world.dispatch_native("OnAddedBuff", "1", {"event.literal_buff_id."+key: 1})
        self.assertFalse(stance.time_paused)
        self.world.dispatch_native("OnAddedBuff", "1", buff_event_id="other_buff")
        self.assertFalse(stance.time_paused)
        self.world.dispatch_native("OnAddedBuff", "1", buff_event_id=key)
        self.assertTrue(stance.time_paused)
        self.world.advance(10)
        self.world.dispatch_native("OnFinishedBuff", "1")
        self.assertTrue(stance.time_paused)
        self.world.dispatch_native("OnFinishedBuff", "1", buff_event_id=key)
        self.assertFalse(stance.time_paused)
        self.assertEqual(stance.expires, 12.5)


class TestLaevatainDurationExtension(unittest.TestCase):
    """The reviewed type5 container, independent of unresolved stance creation."""

    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.definition = replace(TestNativeBuffLifecycle().definition(period=-1, callbacks=()),
                                  stacking=5, reviewed_duration_extend=True)

    def add(self, duration=3, *, source="1", definition=None, end=None, delta=1):
        definition = replace(definition or self.definition, duration=None if duration is None else literal(duration))
        change = NativeBuffChange(LAEVAT_STANCE, literal(delta), selector=NativeTarget("main"),
                                  definition=definition, action_finish_after=end)
        program = ActionProgram("explicit_stance", source, "normal", 0, 0, 0,
                                (CombatEvent(0, "add", native_buffs=(change,)),), parameters=(("bb.inherit", 7),))
        action_id = f"add:{self.world._sequence}:{source}"
        self.assertTrue(self.world.start(program, action_id=action_id))
        return next((v for v in self.world.native_buff_instances.values() if v.key == LAEVAT_STANCE), None)

    def pause(self, instance, paused):
        self.world._native_buff_context = instance
        try:
            pause_buff_time(self.world, instance.uid, paused)
        finally:
            self.world._native_buff_context = None

    def test_existing_instance_adds_life_instead_of_restarting_or_taking_maximum(self):
        instance = self.add()
        self.world.advance(2)
        same = self.add(2, source="2")
        self.assertIs(same, instance)
        self.assertEqual(instance.expires, 5)  # now 2 + remaining 1 + incoming 2.
        self.assertEqual(instance.source, "1")
        self.assertEqual(self.world.native_buff_count("1", LAEVAT_STANCE), 1)
        self.world.advance(3)
        self.assertIn(instance.uid, self.world.native_buff_instances)
        self.world.advance(5)
        self.assertFalse(self.world.native_buff_instances)

    def test_extension_while_paused_keeps_saved_remainder(self):
        instance = self.add()
        self.world.advance(1)
        self.pause(instance, True)
        self.world.advance(10)
        self.assertIs(self.add(1), instance)
        self.assertTrue(instance.time_paused)
        self.assertEqual(instance.paused_lifetime, 3)
        self.assertIsNone(instance.expires)
        self.pause(instance, False)
        self.assertEqual(instance.expires, 13)
        self.world.advance(13)
        self.assertFalse(self.world.native_buff_instances)

    def test_extension_retains_original_bb_source_parent_and_action_end(self):
        instance = self.add(15, end=5)
        instance.parent_scope = "explicit_original_parent"
        before = dict(self.world._action_inputs[instance.uid])
        self.world.advance(1)
        updated = replace(self.definition, parameters=(("bb.gain", 999),))
        self.assertIs(self.add(15, source="2", definition=updated, end=20), instance)
        self.assertEqual(instance.source, "1")
        self.assertEqual(instance.parent_scope, "explicit_original_parent")
        self.assertEqual(self.world._action_inputs[instance.uid], before)
        self.assertEqual(instance.action_finish_at, 5)
        self.world.advance(5)
        self.assertFalse(self.world.native_buff_instances)

    def test_periodic_timer_and_counter_are_not_restarted_on_extension(self):
        definition = replace(TestNativeBuffLifecycle().definition(), stacking=5, reviewed_duration_extend=True)
        instance = self.add(definition=definition)
        self.world.advance(.5)
        self.assertIs(self.add(3, definition=definition), instance)
        self.assertEqual(instance.next_trigger, 1)
        self.assertEqual(self.world.characters["1"].energy, 0)
        self.world.advance(6)
        self.assertEqual(self.world.characters["1"].energy, 42)
        self.assertFalse(self.world.native_buff_instances)

    def test_infinite_input_and_prediction_fork_do_not_corrupt_real_deadline(self):
        instance = self.add()
        fork = self.world.fork()
        definition = replace(self.definition, duration=None)
        change = NativeBuffChange(LAEVAT_STANCE, literal(1), definition=definition)
        change_buff(fork, "1", change, {"bb.inherit": 7}, "fork_add", instance.program, 1)
        self.assertIsNone(fork.native_buff_instances[instance.uid].expires)
        fork.advance(100)
        self.assertEqual(fork.native_buff_count("1", LAEVAT_STANCE), 1)
        self.assertEqual(instance.expires, 3)
        self.world.advance(3)
        self.assertFalse(self.world.native_buff_instances)

    def test_unreviewed_type5_and_multi_count_stay_unknown(self):
        for flag, count in ((False, 1), (True, 2)):
            with self.subTest(flag=flag, count=count):
                self.world = CombatWorldState(("1", "2"), regen=0)
                self.assertIsNone(self.add(definition=replace(self.definition, reviewed_duration_extend=flag), delta=count))
                self.assertTrue(any("reviewed single-layer" in reason for reason in self.world.unresolved))

    def test_real_container_shape_is_approved_without_accepting_other_policies(self):
        data = native_record(SkillTimingStore(), LAEVAT_STANCE)["data"]
        self.assertTrue(reviewed_duration_extend(LAEVAT_STANCE, data))
        self.assertFalse(reviewed_duration_extend("unreviewed", data))
        for key, value in (("stackingType", 4), ("identifierType", 1), ("priority", 1)):
            changed = copy.deepcopy(data)
            changed["stackingSettings"][key] = value
            self.assertFalse(reviewed_duration_extend(LAEVAT_STANCE, changed))

    def test_real_container_compiler_keeps_ranked_duration_bridge_unknown(self):
        store = SkillTimingStore()
        data = native_record(store, LAEVAT_STANCE)["data"]
        arguments = (store, get_character("laevatain"), store.profiles("莱万汀", "ult")[0], "1", LAEVAT_STANCE, data)
        unbound = compile_buff_definition(*arguments, {"assignBlackboard": False}, attributes={}, panel=None, path=())
        self.assertTrue(unbound.reviewed_duration_extend)
        self.assertIn("Native stance duration blackboard transfer needs binding: "+LAEVAT_STANCE, unbound.unresolved)
        reference = {"assignBlackboard": True, "assignItems": [{"directValueType": 0, "useDirectValue": True,
                     "numericValue": .1, "inputValueKey": "", "targetKey": "duration"}]}
        bound = compile_buff_definition(*arguments, reference, attributes={}, panel=None, path=())
        self.assertTrue(bound.reviewed_duration_extend)
        self.assertFalse(bound.unresolved)
        self.assertEqual(dict(bound.inherited)["bb.duration"].evaluate({}), .1)

    def test_real_ultimate_producer_uses_instance_policy_without_silencing_other_gaps(self):
        store = SkillTimingStore()
        program = compile_native_action(store, get_character("laevatain"), store.profiles("莱万汀", "ult")[0], "1", "ult")
        changes = [change for event in program.events for change in event.native_buffs
                   if change.key == LAEVAT_STANCE and not change.remove_all]
        self.assertEqual(len(changes), 1)
        self.assertIsNotNone(changes[0].definition)
        self.assertTrue(changes[0].definition.reviewed_duration_extend)
        self.assertIn("Native stance duration blackboard transfer needs binding: "+LAEVAT_STANCE,
                      changes[0].definition.unresolved)

    def test_link_exit_marker_has_its_own_reviewed_duration_not_a_shared_deadline(self):
        store = SkillTimingStore()
        data = native_record(store, LAEVAT_ULT_END)["data"]
        self.assertTrue(reviewed_duration_extend(LAEVAT_ULT_END, data))
        reference = {"assignBlackboard": True, "assignItems": [{"directValueType": 0, "useDirectValue": True,
                     "numericValue": .1, "inputValueKey": "", "targetKey": "duration"}]}
        compiled = compile_buff_definition(store, get_character("laevatain"), store.profiles("莱万汀", "ult")[0],
                                           "1", LAEVAT_ULT_END, data, reference, attributes={}, panel=None, path=())
        self.assertTrue(compiled.reviewed_duration_extend)
        own = compile_buff_definition(store, get_character("laevatain"), store.profiles("莱万汀", "ult")[0],
                                      "1", LAEVAT_ULT_END, data, {"assignBlackboard": False},
                                      attributes={}, panel=None, path=())
        self.assertNotIn("Native stance duration blackboard transfer needs binding: "+LAEVAT_ULT_END, own.unresolved)
        self.assertEqual(own.duration.evaluate(dict(own.parameters)), 15)
        stance = self.add()
        # Explicit holder omits the independent exit callbacks under audit.
        change = NativeBuffChange(LAEVAT_ULT_END, literal(1), definition=replace(self.definition, duration=literal(5)))
        change_buff(self.world, "1", change, {"bb.inherit": 7}, "exit_first", stance.program, 1)
        exit_marker = next(v for v in self.world.native_buff_instances.values() if v.key == LAEVAT_ULT_END)
        self.world.advance(1)
        change_buff(self.world, "1", change, {"bb.inherit": 7}, "exit_extend", stance.program, 1)
        self.assertEqual(exit_marker.expires, 10)
        self.assertEqual(stance.expires, 3)
        self.assertEqual(self.world.native_buff_count("1", LAEVAT_ULT_END), 1)


if __name__ == "__main__":
    unittest.main()
