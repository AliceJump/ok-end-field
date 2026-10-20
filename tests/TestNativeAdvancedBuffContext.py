"""Camille's authored buff-event queries use the event's loaded BuffData tags."""

import copy
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_simulation import CombatWorldState, walk_combat_events
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_character_events import _dictionaries
from src.data.native_gameplay import native_asset, native_record
from src.data.native_spell_runtime import attachment_policies
from src.data.native_tags import expand_tags
from src.data.skill_timing import SkillTimingStore


class TestNativeAdvancedBuffContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("camille", skill_rank=9, potential=0)
        cls.profile = cls.store.profiles("卡缪", "link")[0]
        data = next(row for row in _dictionaries(native_asset("data_chr_0033_camille"))
                    if "comboSkillConditions" in row)
        cls.conditions = data["comboSkillConditions"]
        # Use the actual common attachment ID, not a guessed suffix.
        cls.fire = attachment_policies()[0][0]
        cls.other = attachment_policies()[1][0]

    def compile(self, condition):
        return compile_native_action(self.store, self.character, self.profile, "2", "normal",
                                     event_sequence=condition["comboSkillCheckAction"])

    def sample(self, program, buff_id, *, loaded=True, trigger="OnConsumeBuff"):
        world = CombatWorldState(("1", "2"), regen=0)
        if loaded:
            world.native_buff_tags[buff_id] = expand_tags(native_record(self.store, buff_id)["data"]["applyTags"])
        world.register_character_hook(trigger, program)
        world.dispatch_character_event(trigger, "1", "target", buff_id)
        values = next(value for key, value in world._action_inputs.items() if key.startswith("character_event:"))
        results = [value for key, value in values.items() if key.startswith("check.")]
        return world, results

    def test_both_real_camille_conditions_compile_without_unknown(self):
        self.assertEqual([row["comboSkillEvent"] for row in self.conditions], [211, 208])
        for condition, trigger in zip(self.conditions, ("OnAbsorbBuff", "OnConsumeBuff"), strict=True):
            program = self.compile(condition)
            self.assertFalse([reason for event in walk_combat_events(program.events) for reason in event.unresolved])
            world, results = self.sample(program, self.fire, trigger=trigger)
            self.assertEqual(results, [1])
            self.assertFalse(world.unresolved)

    def test_event_fire_tags_match_after_the_pool_has_already_been_consumed(self):
        world, results = self.sample(self.compile(self.conditions[1]), self.fire)
        self.assertEqual(world.enemies["target"].infliction_stacks, 0)
        self.assertEqual(results, [1])
        self.assertEqual(world.main_control, "1")
        self.assertNotIn("EntityBB_noguard_count", world.characters["1"].blackboard)

    def test_different_attachment_and_known_empty_tags_do_not_match(self):
        for buff_id in (self.other, "buff_chr_0009_azrila_normal_skill_shelter"):
            with self.subTest(buff_id=buff_id):
                world, results = self.sample(self.compile(self.conditions[0]), buff_id)
                self.assertEqual(results, [0])
                self.assertFalse(world.unresolved)

    def test_missing_buff_metadata_stays_unknown_even_for_exclusion_query(self):
        condition = copy.deepcopy(self.conditions[0])
        condition["comboSkillCheckAction"]["actionData"][0]["$value"]["query"]["queryType"] = 2
        world, results = self.sample(self.compile(condition), self.fire, loaded=False)
        self.assertEqual(results, [])
        self.assertIn("Missing combat input: event.buff_data_available", world.unresolved)

    def test_no_buff_event_context_returns_false_without_reading_metadata(self):
        program = self.compile(self.conditions[0])
        world = CombatWorldState(("1", "2"), regen=0)
        self.assertTrue(world.start(program, action_id="outside_buff_event"))
        self.assertEqual([v for k, v in world._action_inputs["outside_buff_event"].items()
                          if k.startswith("check.")], [0])
        self.assertFalse(world.unresolved)

    def test_all_tag_modes_and_empty_query_follow_native_tag_contract(self):
        for mode, expected in ((0, 1), (1, 1), (2, 0), (3, 0)):
            condition = copy.deepcopy(self.conditions[0])
            condition["comboSkillCheckAction"]["actionData"][0]["$value"]["query"]["queryType"] = mode
            world, results = self.sample(self.compile(condition), self.fire)
            self.assertEqual(results, [expected])
            self.assertFalse(world.unresolved)
        for mode, expected in ((0, 0), (1, 1), (2, 1), (3, 0)):
            condition = copy.deepcopy(self.conditions[0])
            query = condition["comboSkillCheckAction"]["actionData"][0]["$value"]["query"]
            query.update(queryType=mode, tags=[])
            _, results = self.sample(self.compile(condition), self.fire)
            self.assertEqual(results, [expected])

    def test_dynamic_id_reference_and_string_output_arms_remain_unresolved(self):
        for changes, expected in (({"checkType": 0, "buffIdList": [
                {"useBlackboardKey": True, "blackboardKey": "dynamic_id", "value": ""}]},
                "Dynamic/unknown native event buff identity needs binding"),
                ({"blackboardKey": "matched_buff_id"}, "Native advanced event buff identity/output needs binding")):
            condition = copy.deepcopy(self.conditions[0])
            condition["comboSkillCheckAction"]["actionData"][0]["$value"].update(changes)
            program = self.compile(condition)
            self.assertIn(expected, [r for e in walk_combat_events(program.events) for r in e.unresolved])

    def test_representative_catalog_keeps_ember_and_action_execution_blockers(self):
        catalog = build_combat_catalog(("弭弗", "骏卫", "余烬", "卡缪"), self.store)
        self.assertEqual(catalog.diagnostics, (
            "Unreviewed native damage mask check mode: chr_0009_azrila_combo_skill/3",))
        self.assertTrue(any(event.unresolved for program in catalog.candidates(kind="battle")
                            for event in walk_combat_events(program.events)))
        triggers = [trigger for trigger, p in catalog.world.native_character_hooks if p.actor == "4"]
        self.assertEqual(triggers, ["OnAbsorbBuff", "OnConsumeBuff"])


class TestNativeLiteralBuffContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("laevatain")
        cls.profile = cls.store.profiles("莱万汀", "ult")[0]
        cls.root = native_record(cls.store, "buff_chr_0016_laevat_show_weapon")["data"]
        cls.marker = "buff_chr_0016_laevat_pause_ult"

    def compile(self, identities=None):
        block = copy.deepcopy(self.root["abilityEventAction"][0]["actions"][0])
        # Use the real check's assigned result while keeping the Pause leaf out
        # of these condition-only tests; the full callback is tested separately.
        block["actionData"] = [next(n for n in _nodes(block)
                                     if n["$type"].endswith("CheckBuffIdInContextAdvanced+Data"))]
        body = block["actionData"][0]["$value"]
        if identities is not None:
            body["buffIdList"] = [{"useBlackboardKey": False, "blackboardKey": "", "value": key}
                                  for key in identities]
        program = compile_native_action(self.store, self.character, self.profile, "1", "normal",
                                        event_sequence=block, isolated_blackboard=True)
        self.assertFalse([r for event in walk_combat_events(program.events) for r in event.unresolved])
        return replace(program, sp_cost=0, gate=0, energy_cost=0)

    def result(self, world, program, buff_id=None, payload=None):
        values = []
        world.register_character_hook("probe", program)
        original = world._execute_event

        def capture(*args):
            result = original(*args)
            values.extend(value for key, value in world._action_inputs.get(args[0], {}).items()
                          if key.startswith("check."))
            return result

        with patch.object(world, "_execute_event", side_effect=capture):
            if buff_id is None:
                program = replace(program, events=tuple(replace(e, inputs=(*e.inputs, *(payload or {}).items()))
                                                         for e in program.events))
                world.start(program, action_id="outside")
            else:
                world.dispatch_character_event("probe", "1", "target", buff_id)
        return values[-1]

    def test_literal_or_supports_one_two_three_and_empty_lists_without_metadata(self):
        for ids, event_id, expected in (([self.marker], self.marker, 1),
                (["other", self.marker], self.marker, 1), (["a", "b", self.marker], self.marker, 1),
                (["a", "b", self.marker], "missing", 0), ([], self.marker, 0)):
            with self.subTest(ids=ids, event_id=event_id):
                world = CombatWorldState(("1",), regen=0)
                self.assertEqual(self.result(world, self.compile(ids), event_id), expected)
                self.assertFalse(world.native_buff_tags)
                self.assertFalse(world.native_buffs)
                self.assertFalse(world.unresolved)

    def test_payload_blackboard_and_holder_cannot_forge_a_buff_event(self):
        program = self.compile()
        world = CombatWorldState(("1",), regen=0)
        forged = {"event.literal_buff_id."+self.marker: 1,
                  "event.buff_id."+self.marker: 1, "event.buff_context": 1}
        key = "event.literal_buff_id."+self.marker
        program = replace(program, parameters=(*program.parameters, (key, 1)))
        self.assertEqual(self.result(world, program, payload=forged), 0)
        self.assertFalse(world.unresolved)

    def test_reused_callback_replaces_previous_true_and_nested_events_restore_identity(self):
        world = CombatWorldState(("1",), regen=0)
        p = self.compile()
        key = "event.literal_buff_id."+self.marker
        seen = []

        def dispatch(current, trigger, actor, payload, target):
            seen.append((trigger, current._native_event_buff_id))
            if trigger == "outer":
                current.dispatch_native("nested", "1")
                seen.append(("restored", current._native_event_buff_id))
            # Use one persistent callback scope, as a real buff listener does.
            current._action_inputs.setdefault("callback", dict(p.parameters))
            for event in p.events:
                current._sequence += 1
                current._execute_event("callback", current._sequence, p, event)

        with patch("src.data.native_buff_runtime.dispatch_buff_event", side_effect=dispatch):
            world.dispatch_native("outer", "1", buff_event_id=self.marker)
            self.assertEqual([v for k, v in world._action_inputs["callback"].items() if k.startswith("check.")], [1])
            world._action_inputs["callback"][key] = 1  # A stale/private value is not an event context.
            world.dispatch_native("different", "1", {key: 1}, buff_event_id="other")
            self.assertEqual([v for k, v in world._action_inputs["callback"].items() if k.startswith("check.")], [0])
            world.dispatch_native("no_context", "1", {key: 1})
            self.assertEqual([v for k, v in world._action_inputs["callback"].items() if k.startswith("check.")], [0])
        self.assertEqual(seen[:3], [("outer", self.marker), ("nested", None), ("restored", self.marker)])
        self.assertIsNone(world._native_event_buff_id)

    def test_event_identity_restores_after_exception_and_invalid_input_is_rejected(self):
        world = CombatWorldState(("1",), regen=0)
        world._native_event_buff_id = "previous"
        with patch("src.data.native_buff_runtime.dispatch_buff_event", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                world.dispatch_native("OnAddedBuff", "1", buff_event_id=self.marker)
        self.assertEqual(world._native_event_buff_id, "previous")
        for invalid in ("", 1, [], False):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                world.dispatch_native("OnAddedBuff", "1", buff_event_id=invalid)
        self.assertEqual(world._native_event_buff_id, "previous")


if __name__ == "__main__":
    unittest.main()
