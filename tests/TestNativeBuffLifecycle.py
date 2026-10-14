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
from src.data.native_buff_program import POGRANI_MORALE, compile_buff_definition, reviewed_retained_layers
from src.data.native_buff_runtime import change_buff, finish_buff_instances
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


if __name__ == "__main__":
    unittest.main()
