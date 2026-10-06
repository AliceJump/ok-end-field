"""Buff callbacks retain their holder, parameter snapshot and native clocks."""

import copy
import unittest

from src.data.character_skills import get_character
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeResourceChange,
    NativeTarget,
)
from src.data.native_action_program import _nodes, compile_native_action
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


if __name__ == "__main__":
    unittest.main()
