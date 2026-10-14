"""Native tag hierarchy, query modes and event recipient scopes."""

import copy
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression, combat_input
from src.data.combat_simulation import (
    ActionProgram,
    CombatEvent,
    CombatWorldState,
    NativeBuffChange,
    NativeBuffProgram,
    NativeBuffQuery,
    NativeTarget,
    UnresolvedMechanic,
)
from src.data.native_action_program import compile_native_action
from src.data.native_event_context import event_targets
from src.data.native_gameplay import native_asset, native_enums, native_record
from src.data.native_tags import expand_tags, matches_tags, native_tag_id, tag_hash, tag_names, tag_query
from src.data.skill_timing import SkillTimingStore


def literal(value):
    return CombatExpression("literal", (float(value),))


class TestNativeTags(unittest.TestCase):
    def setUp(self):
        self.world = CombatWorldState(("1", "2"), regen=0)
        self.shred_tag = 1075718177
        self.tags = expand_tags(({"raw": "21281e40"},))

    def sample(self, query, *, world=None, action_id="query"):
        world = world or self.world
        program = ActionProgram("query", "1", "normal", 0, 0, 0,
                                (CombatEvent(0, "query", assignments=(("bb.result", combat_input(query.key)),)),),
                                native_buff_queries=(query,))
        self.assertTrue(world.start(program, action_id=action_id))
        return world._action_inputs[action_id].get("bb.result")

    def test_native_asset_names_recover_known_ids_and_implicit_parents(self):
        self.assertEqual(tag_names()[self.shred_tag], "Skill/Character/Common/NoGuard")
        self.assertEqual(tag_hash("Skill/Character/Common/NoGuard"), self.shred_tag)
        self.assertEqual(tag_hash("Skill/Character/Common/PhysicalStatus/CrushStatus"), -168668661)
        self.assertEqual(tag_hash("Skill/Character/Common/PhysicalStatus/FractureStatus"), -430063731)
        self.assertEqual(len(tag_names()), 6962)
        explicit = {n for n in native_asset("GameplayTagConfig")["allTags"]["_keyData"]}
        self.assertTrue(explicit)
        for name in explicit:
            self.assertEqual(tag_names()[tag_hash(name)], name)
        enums = native_enums()["Beyond.Gameplay.Core.BuffStackNumType"]
        self.assertEqual({k: v["value"] for k, v in enums.items()}, {"BuffCount": 0, "BuffIdCount": 1})

    def test_both_serializations_match_ancestors_without_reverse_matching(self):
        self.assertEqual(native_tag_id({"raw": "21281e40"}), self.shred_tag)
        self.assertEqual(expand_tags(({"tagId": self.shred_tag},)), self.tags)
        ancestor = tag_hash("Skill/Character/Common")
        self.assertTrue(matches_tags(self.tags, "HasAny", (ancestor,)))
        parent_tags = expand_tags(({"tagId": ancestor},))
        self.assertFalse(matches_tags(parent_tags, "HasAny", (self.shred_tag,)))

    def test_all_four_modes_and_empty_or_invalid_queries(self):
        unrelated = -430063731
        for mode, expected in (("HasAny", True), ("HasAll", False), ("ExceptAny", False), ("ExceptAll", True)):
            with self.subTest(mode=mode):
                self.assertEqual(matches_tags(self.tags, mode, (self.shred_tag, unrelated)), expected)
        for mode, expected in (("HasAny", False), ("HasAll", True), ("ExceptAny", True), ("ExceptAll", False)):
            self.assertEqual(matches_tags(self.tags, mode, ()), expected)
        self.assertEqual(tag_query({"queryType": 0, "tags": [{"tagId": 0}]}), ("HasAny", (0,)))
        self.assertFalse(matches_tags(self.tags, "HasAny", (0,)))
        self.assertEqual(expand_tags(({"tagId": 0},)), ())
        for query in ({"queryType": 4, "tags": []}, {"queryType": 0, "tags": [{"tagId": 1}]}):
            with self.assertRaises(UnresolvedMechanic):
                tag_query(query)
        with self.assertRaises(UnresolvedMechanic):
            expand_tags(({"tagId": 1},))

    def test_counts_layers_or_distinct_ids_on_the_selected_holder(self):
        self.world.native_buff_tags.update(a=self.tags, b=self.tags)
        self.world.native_buffs.update({("target", "a"): (3, None), ("target", "b"): (2, None),
                                       ("1", "a"): (9, None)})
        query = NativeBuffQuery("query.count", NativeTarget("action_target"), (), "HasAny", (self.shred_tag,))
        self.assertEqual(self.sample(query), 5)
        self.assertEqual(self.sample(replace(query, count_type=1), action_id="ids"), 2)
        self.assertEqual(self.sample(replace(query, target=NativeTarget("source")), action_id="source"), 9)
        self.world.native_buff_tags["buff_physical_no_guard"] = self.tags
        self.world.add_shred("target", 4)
        self.assertEqual(self.sample(query, action_id="with_shred"), 9)
        self.assertEqual(self.sample(replace(query, count_type=1), action_id="with_shred_ids"), 3)

    def test_ambiguous_multi_target_tag_count_does_not_sum_the_squad(self):
        self.world.native_buff_tags["a"] = self.tags
        self.world.native_buffs.update({("1", "a"): (3, None), ("2", "a"): (2, None)})
        query = NativeBuffQuery("query.count", NativeTarget("squad"), (), "HasAny", (self.shred_tag,))
        self.assertIsNone(self.sample(query))
        self.assertTrue(self.world.unresolved)

    def test_simple_buff_tags_and_expiry_are_visible_without_callbacks(self):
        change = NativeBuffChange("simple", literal(2), literal(2), selector=NativeTarget("action_target"), tags=self.tags)
        program = ActionProgram("add", "1", "normal", 0, 0, 0, (CombatEvent(0, "add", native_buffs=(change,)),))
        self.assertTrue(self.world.start(program, action_id="add"))
        query = NativeBuffQuery("query.count", NativeTarget("action_target"), (), "HasAny", (self.shred_tag,))
        self.assertEqual(self.sample(query), 2)
        self.world.advance(2)
        self.assertEqual(self.sample(query, action_id="expired"), 0)

    def test_event_target_overrides_current_but_keeps_buff_source_and_owner(self):
        self.world.native_buff_tags["buff_physical_no_guard"] = self.tags
        self.world.add_shred("target", 3)
        query = NativeBuffQuery("query.count", NativeTarget("action_target"), (), "HasAny", (self.shred_tag,))
        callback = ActionProgram("callback", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "capture", assignments=(("bb.result", combat_input(query.key)),)),),
                                 native_buff_queries=(query,))
        definition = NativeBuffProgram((), (), None, literal(-1), literal(0), True, 0, literal(0), (),
                                       subscriptions=(("OnBeforeOutputPhysicalInfliction", callback),))
        change = NativeBuffChange("listener", literal(1), selector=NativeTarget("main"), definition=definition)
        self.world.main_control = "2"
        self.world.start(ActionProgram("add", "1", "normal", 0, 0, 0,
                                       (CombatEvent(0, "add", native_buffs=(change,)),)), action_id="add")
        uid = next(iter(self.world.native_buff_instances))
        before = dict(self.world._action_targets[uid])
        self.world.dispatch_native("OnBeforeOutputPhysicalInfliction", "2", target="target")
        self.assertEqual(self.world._action_inputs[uid]["bb.result"], 3)
        self.assertEqual(self.world._action_targets[uid], before)
        self.assertEqual(before["source"], ("1",))
        self.assertEqual(before["owner"], ("2",))

    def test_nested_event_context_restores_targets_even_when_a_callback_raises(self):
        self.world._action_targets["scope"] = {"current": ("1",), "source": ("2",)}
        with event_targets(self.world, "scope", "target"):
            with self.assertRaises(RuntimeError):
                with event_targets(self.world, "scope", "2"):
                    raise RuntimeError("callback failed")
            self.assertEqual(self.world._action_targets["scope"]["current"], ("target",))
        self.assertEqual(self.world._action_targets["scope"], {"current": ("1",), "source": ("2",)})

    def test_real_rocxi_character_tag_conditions_register_and_prediction_is_isolated(self):
        catalog = build_combat_catalog(("洛茜", "弭弗", "骏卫"), SkillTimingStore())
        self.assertFalse(catalog.diagnostics)
        queries = [q for _, p in catalog.world.native_character_hooks if p.actor == "1" for q in p.native_buff_queries]
        self.assertTrue(any(q.tag_mode == "HasAny" and q.tags == (-193971080,) for q in queries))
        predicted = catalog.world.fork()
        predicted.native_buff_tags["new"] = self.tags
        self.assertNotIn("new", catalog.world.native_buff_tags)

    def combo_snapshot(self, *, target_source=4, target_key="", setting=False):
        store = SkillTimingStore()
        data = native_record(store, "buff_common_affixes_combo_trigger")["data"]
        block = data["abilityEventAction"][0]["actions"][0]
        nodes = copy.deepcopy(block["actionData"][1:3] if setting else block["actionData"][1:2])
        nodes[0]["$value"]["checkTarget"].update(targetSource=target_source, targetGroupKey=target_key)
        program = compile_native_action(store, get_character("akekuri"), store.profiles("秋栗", "ult")[0], "1", "normal",
                                        event_sequence={"actionData": nodes}, event_blackboard={"count": 0, "imbue_scale": 0},
                                        isolated_blackboard=True)
        self.assertFalse(any(event.unresolved for event in program.events))
        self.assertTrue(program.native_buff_queries[0].first_target_only)
        # Replay the callback fragment; it does not pay the enclosing ultimate
        # energy or own that ultimate's cooldown/animation lock.
        return replace(program, key="basic_snapshot_probe", energy_cost=0, duration=2, cooldown=0,
                       actor_lock=0, next_action_windows=())

    def test_actual_basic_combo_snapshot_feeds_the_captured_setting_tier(self):
        key = "buff_common_affixes_combo_trigger"
        self.world.native_buffs["1", key] = (3, None)
        program = self.combo_snapshot(setting=True)
        self.assertTrue(self.world.start(program, action_id="actual_read"))
        self.assertEqual(self.world._action_inputs["actual_read"]["bb.count"], 3)
        # Native ReadSkillSettingData reads column-1; the authored row contains
        # incremental tiers, not an automatically accumulated/max-stack bonus.
        expected = next(row for row in native_asset("SkillSetting")["spellInflictionDataList"]
                        if row["key"] == "连击增伤")["values"][2]
        self.assertEqual(self.world._action_inputs["actual_read"]["bb.imbue_scale"], expected)
        self.assertFalse(self.world.unresolved)

    def test_basic_snapshot_reads_first_group_target_instead_of_summing_all_owners(self):
        program = self.combo_snapshot(target_source=2, target_key="recipients")
        key = "buff_common_affixes_combo_trigger"
        self.world.native_buffs.update({("1", key): (2, None), ("2", key): (4, None)})
        program = replace(program, events=tuple(replace(e, at=1) for e in program.events))
        self.assertTrue(self.world.start(program, action_id="first"))
        self.world._action_targets["first"]["recipients"] = ("2", "1")
        self.world.advance(1)
        self.assertEqual(self.world._action_inputs["first"]["bb.count"], 4)
        query = replace(program.native_buff_queries[0], first_target_only=False)
        legacy = replace(program, native_buff_queries=(query,), events=tuple(replace(e, at=0) for e in program.events))
        # The new first-target flag does not change existing multi-owner queries.
        self.world._action_targets["all"] = {"recipients": ("2", "1")}
        self.world._action_inputs["all"] = dict(legacy.parameters)
        for event in legacy.events:
            self.world._execute_event("all", 0, legacy, event)
        self.assertEqual(self.world._action_inputs["all"]["bb.count"], 6)

    def test_missing_selector_stays_unknown_while_confirmed_empty_group_writes_zero(self):
        for bound in (False, True):
            with self.subTest(bound=bound):
                world = CombatWorldState(("1", "2"), regen=0)
                program = self.combo_snapshot(target_source=2, target_key="recipients")
                program = replace(program, events=tuple(replace(e, at=1) for e in program.events))
                self.assertTrue(world.start(program, action_id="read"))
                world._action_inputs["read"]["bb.count"] = 4
                if bound:
                    world._action_targets["read"]["recipients"] = ()
                world.advance(1)
                if bound:
                    self.assertEqual(world._action_inputs["read"]["bb.count"], 0)
                    self.assertFalse(world.unresolved)
                else:
                    self.assertNotIn("bb.count", world._action_inputs["read"])
                    self.assertTrue(world.unresolved)

    def test_basic_snapshot_expiry_and_prediction_do_not_keep_old_or_maximum_count(self):
        key = "buff_common_affixes_combo_trigger"
        self.world.native_buffs["1", key] = (2, 1)
        program = self.combo_snapshot()
        self.assertTrue(self.world.start(program, action_id="before"))
        self.assertEqual(self.world._action_inputs["before"]["bb.count"], 2)
        predicted = self.world.fork()
        predicted.native_buffs["1", key] = (4, None)
        self.assertTrue(predicted.start(program, action_id="forecast"))
        self.assertEqual(predicted._action_inputs["forecast"]["bb.count"], 4)
        self.assertEqual(self.world.native_buffs["1", key][0], 2)
        self.world.advance(1)
        self.assertTrue(self.world.start(program, action_id="expired"))
        self.assertEqual(self.world._action_inputs["expired"]["bb.count"], 0)

    def test_confirmed_battle_callback_reads_holder_count_without_borrowing_source_count(self):
        key = "buff_common_affixes_combo_trigger"
        self.world.native_buffs.update({("1", key): (4, None), ("2", key): (2, None)})
        callback = self.combo_snapshot(setting=True)
        definition = NativeBuffProgram((), (), None, literal(-1), literal(0), True, 0, literal(0), (),
                                       subscriptions=(("OnBeforeCastSkill", callback),))
        self.world.main_control = "2"
        change = NativeBuffChange("snapshot_listener", literal(1), selector=NativeTarget("main"), definition=definition)
        producer = ActionProgram("listener", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "listener_added", native_buffs=(change,)),))
        self.assertTrue(self.world.start(producer, action_id="listener"))
        uid = next(iter(self.world.native_buff_instances))
        before = dict(self.world._action_targets[uid])
        self.world.dispatch_native("OnBeforeCastSkill", "2", {"event.skill_type": 2})
        self.assertEqual(self.world._action_inputs[uid]["bb.count"], 2)
        expected = next(row for row in native_asset("SkillSetting")["spellInflictionDataList"] if row["key"] == "连击增伤")
        self.assertEqual(self.world._action_inputs[uid]["bb.imbue_scale"], expected["values"][1])
        self.assertEqual(self.world._action_targets[uid], before)
        self.assertEqual(before["owner"], ("2",))
        self.assertEqual(before["source"], ("1",))


if __name__ == "__main__":
    unittest.main()
