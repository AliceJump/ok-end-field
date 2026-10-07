"""Camille's authored buff-event queries use the event's loaded BuffData tags."""

import copy
import unittest

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_simulation import CombatWorldState, walk_combat_events
from src.data.native_action_program import compile_native_action
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

    def test_id_reference_and_string_output_arms_remain_unresolved(self):
        for changes in ({"checkType": 0}, {"blackboardKey": "matched_buff_id"}):
            condition = copy.deepcopy(self.conditions[0])
            condition["comboSkillCheckAction"]["actionData"][0]["$value"].update(changes)
            program = self.compile(condition)
            self.assertIn("Native advanced event buff identity/output needs binding",
                          [r for e in walk_combat_events(program.events) for r in e.unresolved])

    def test_representative_catalog_keeps_ember_and_action_execution_blockers(self):
        catalog = build_combat_catalog(("弭弗", "骏卫", "余烬", "卡缪"), self.store)
        self.assertEqual(catalog.diagnostics, (
            "Runtime condition: chr_0009_azrila_combo_skill/CheckDamageDecorateMask+Data",))
        self.assertTrue(any(event.unresolved for program in catalog.candidates(kind="battle")
                            for event in walk_combat_events(program.events)))
        triggers = [trigger for trigger, p in catalog.world.native_character_hooks if p.actor == "4"]
        self.assertEqual(triggers, ["OnAbsorbBuff", "OnConsumeBuff"])


if __name__ == "__main__":
    unittest.main()
