"""The team's actual authored MainCharacterValidator filters living members."""

import copy
import json
import unittest
from dataclasses import replace

from src.data.character_skills import get_character
from src.data.combat_catalog import BASELINE, build_combat_catalog
from src.data.combat_simulation import CombatWorldState, NativeTarget, walk_combat_events
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore


class TestNativeMainCharacterSelector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        names = ("弭弗", "骏卫", "余烬", "卡缪")
        builds = {row["character"]: row for row in json.loads(BASELINE.read_text(encoding="utf-8"))}
        cls.members = tuple((name, builds[name]["key"]) for name in names)
        cls.samples = []
        for name, character_id in cls.members:
            profile = (cls.store.battle_phase_profiles(name) or cls.store.profiles(name, "battle"))[0]
            node = next(n for n in _nodes(native_record(cls.store, profile.skill_id)["data"])
                        if n["$type"].endswith("FindTargetAction+FindTargetActionData")
                        and any(v["$type"].endswith("MainCharacterValidator+Data")
                                for v in n["$value"]["selectorData"]["validatorData"]))
            cls.samples.append((get_character(character_id), profile, node))

    def compile(self, node, index=0):
        character, profile, _ = self.samples[index]
        program = compile_native_action(self.store, character, profile, "1", "normal",
                                         event_sequence={"actionData": [node]})
        return replace(program, sp_cost=0, gate=None, energy_cost=0, cooldown=0, actor_lock=0, duration=.1,
                       events=tuple(replace(event, at=.1) for event in program.events))

    def execute(self, node=None, *, main="2", dead=(), index=0):
        node = node or self.samples[index][2]
        world = CombatWorldState(("1", "2", "3", "4"), regen=0)
        world.main_control = main
        for actor in dead:
            world.characters[actor].alive = False
        program = self.compile(node, index)
        self.assertTrue(world.start(program, action_id="find"))
        world.advance(.1)
        key = node["$value"]["targetGroupKey"]
        return world, world._action_targets["find"].get(key)

    def test_actual_four_base_skills_select_main_not_the_casting_character(self):
        for index, member in enumerate(self.members):
            with self.subTest(member=member):
                program = self.compile(self.samples[index][2], index)
                self.assertEqual(program.events[0].target_bindings[0].selectors, (NativeTarget("squad_main"),))
                world, targets = self.execute(index=index)
                self.assertEqual(targets, ("2",))
                self.assertEqual(world._action_inputs["find"][f"target.{self.samples[index][2]['$value']['targetGroupKey']}.count"], 1)
                self.assertFalse(world.unresolved)

    def test_selection_samples_main_at_event_time(self):
        world = CombatWorldState(("1", "2", "3", "4"), regen=0)
        program = self.compile(self.samples[0][2])
        self.assertTrue(world.start(program, action_id="find"))
        world.main_control = "3"
        world.advance(.1)
        self.assertEqual(world._action_targets["find"]["MainChar"], ("3",))

    def test_dead_or_absent_main_does_not_fall_back_to_caster(self):
        for main, dead in (("2", ("2",)), (None, ()), ("not_a_member", ())):
            with self.subTest(main=main):
                world, targets = self.execute(main=main, dead=dead)
                self.assertEqual(targets, ())
                self.assertEqual(world._action_inputs["find"]["target.MainChar.count"], 0)
                self.assertFalse(world.unresolved)

    def test_plain_team_finder_excludes_dead_but_is_not_restricted_to_main(self):
        node = copy.deepcopy(self.samples[0][2])
        node["$value"]["selectorData"]["validatorData"] = []
        world, targets = self.execute(node, dead=("3",))
        self.assertEqual(set(targets), {"1", "2", "4"})
        self.assertFalse(world.unresolved)

    def test_other_filters_postprocessors_and_unverified_validator_fields_stay_unknown(self):
        for change in ("validator_fields", "postprocessor", "other_finder", "second_filter"):
            node = copy.deepcopy(self.samples[0][2])
            selector = node["$value"]["selectorData"]
            if change == "validator_fields":
                selector["validatorData"][0]["$value"]["negate"] = True
            elif change == "postprocessor":
                selector["postProcessorData"] = [selector["validatorData"][0]]
            elif change == "other_finder":
                selector["finderData"]["$type"] = "Beyond.Gameplay.Core.Selector+SourceFinder+Data"
            else:
                selector["validatorData"].append(copy.deepcopy(selector["validatorData"][0]))
            program = self.compile(node)
            self.assertTrue(any(e.unresolved for e in walk_combat_events(program.events)))

    def test_predicted_target_binding_does_not_mutate_source_world(self):
        world = CombatWorldState(("1", "2", "3", "4"), regen=0)
        world.main_control = "2"
        fork = world.fork()
        program = self.compile(self.samples[0][2])
        fork.start(program, action_id="predicted")
        fork.advance(.1)
        self.assertEqual(fork._action_targets["predicted"]["MainChar"], ("2",))
        self.assertNotIn("predicted", world._action_targets)

    def test_representative_team_keeps_geometry_and_gameplay_unknown(self):
        catalog = build_combat_catalog(tuple(name for name, _ in self.members), self.store)
        for program in catalog.candidates(kind="battle"):
            unknown = {r for e in walk_combat_events(program.events) for r in e.unresolved}
            self.assertFalse(any(r.endswith(("/MainChar", "/mainchar")) for r in unknown))
        self.assertTrue(any(e.unresolved for p in catalog.candidates(kind="battle") for e in walk_combat_events(p.events)))
        self.assertTrue(any("/pos" in r or "/TelePosition" in r for p in catalog.candidates(kind="battle")
                            for e in walk_combat_events(p.events) for r in e.unresolved))


if __name__ == "__main__":
    unittest.main()
