"""Persistent producers use selected progression and never become player casts."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.combat_expressions import CombatExpression
from src.data.combat_simulation import ActionProgram, CombatEvent, CombatWorldState, NativeResourceChange, NativeTarget
from src.data.native_combat_parameters import bind_native_parameters
from src.data.native_passive_program import NativePassiveProgram, compile_passive_producers
from src.data.native_passive_runtime import activate_passive
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import CombatResourceType


class TestNativePassivePrograms(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.catalog = build_combat_catalog(("弭弗", "骏卫", "余烬"), cls.store)

    def compile(self, key, potential=0):
        character = get_character(key, potential=potential)
        profile = next(p for p in self.store.profiles(character.name, "battle"))
        return compile_passive_producers(self.store, character, "1", profile, attributes={}, panel=None)

    def test_baseline_selects_producers_once_and_reports_the_conflicting_parameter(self):
        rows = json.loads(Path("assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        catalog = build_combat_catalog(tuple(r["character"] for r in rows), self.store)
        self.assertEqual(len(catalog.world.native_passives), 47)
        self.assertTrue(any("blackboard precedence" in d and "probability" in d for d in catalog.diagnostics))
        selected = {}
        for actor, row in enumerate(rows, 1):
            character = get_character(row["key"], potential=row["profile"]["potential"])
            selected[str(actor)] = {p.effect_id for p in (*character.progression.talents, *character.progression.active_potentials)}
        for passive in catalog.world.native_passives.values():
            self.assertIn(passive.key.rsplit(":", 1)[0], selected[passive.program.actor])
        self.assertFalse(any("toggle passive" in d for d in catalog.diagnostics))
        self.assertTrue(catalog.world.unresolved)  # Geometry/processors remain explicit.

    def test_empty_timeline_skill_attaches_real_buff_with_selected_talent_values(self):
        world = self.catalog.world
        instances = [b for b in world.native_buff_instances.values() if b.key == "buff_chr_0009_azrila_talent_2"]
        self.assertEqual(len(instances), 1)
        instance = instances[0]
        self.assertEqual((instance.source, instance.owner, instance.expires), ("3", "3", None))
        self.assertAlmostEqual(world._action_inputs[instance.uid]["bb.attack"], .09)
        self.assertEqual(world._action_targets[instance.uid]["owner"], ("3",))
        self.assertTrue(instance.definition.subscriptions)

    def test_startup_is_idempotent_without_sp_energy_locks_or_cooldowns(self):
        producers, errors = self.compile("ember")
        self.assertFalse(errors)
        world = CombatWorldState(("1",))
        world.sp = 75
        before = (world.time, world.sp, world.characters["1"].energy, dict(world.cooldowns), dict(world.active_actions))
        for passive in producers:
            self.assertTrue(activate_passive(world, passive))
        count = len(world.native_buff_instances)
        for passive in producers:
            self.assertFalse(activate_passive(world, passive))
        self.assertEqual(count, len(world.native_buff_instances))
        self.assertEqual(before, (world.time, world.sp, world.characters["1"].energy, world.cooldowns, world.active_actions))

    def test_potential_attachments_obey_selected_level(self):
        low, _ = self.compile("fluorite", 0)
        high, _ = self.compile("fluorite", 5)
        self.assertFalse(any("potential_5" in p.key for p in low))
        self.assertEqual(sum("potential_5" in p.key for p in high), 1)

    def test_parameter_composition_is_separate_from_unverified_native_precedence(self):
        key = "chr_0022_bounda_talent_2"
        p0 = bind_native_parameters(self.store, key, get_character("fluorite", potential=0).progression,
                                    initial_blackboard={"probability": .2})
        p5 = bind_native_parameters(self.store, key, get_character("fluorite", potential=5).progression,
                                    initial_blackboard={"probability": .2})
        self.assertAlmostEqual(p0.blackboard["probability"], .2)
        self.assertAlmostEqual(p5.blackboard["probability"], .3)
        producers, errors = self.compile("fluorite", 5)
        self.assertTrue(any("blackboard precedence" in d for d in errors))
        self.assertFalse(any(p.program.key == key for p in producers))

    def test_unsupported_attachment_condition_is_reported_without_attaching(self):
        character = get_character("ember", potential=0)
        talent = next(t for t in character.progression.talents if any(m.get("attachSkill") for m in t.modifiers))
        modifiers = copy.deepcopy(talent.modifiers)
        modifiers[0]["activeCondition"] = ["unknown_native_condition"]
        changed = replace(talent, modifiers=modifiers)
        progression = replace(character.progression, talent_ranks=(changed,))
        character = copy.copy(character)
        character.progression = progression
        programs, errors = compile_passive_producers(self.store, character, "1", self.store.profiles("余烬", "battle")[0],
                                                     attributes={}, panel=None)
        self.assertFalse(programs)
        self.assertIn("unknown_native_condition", errors[0])

    def test_subscription_uses_owner_and_actual_event_payload_only(self):
        literal = lambda n: CombatExpression("literal", (float(n),))
        change = NativeResourceChange(CombatResourceType.ULTIMATE_ENERGY, CombatExpression("input", ("event.amount",)),
                                      literal(1), NativeTarget("source"), NativeTarget("owner"))
        callback = ActionProgram("callback", "1", "normal", 0, 0, 0,
                                 (CombatEvent(0, "credit", native_resources=(change,)),))
        passive = NativePassiveProgram("test", "native fixture", ActionProgram("passive", "1", "normal", 0, 0, 0, ()),
                                       (("OnFixture", callback),))
        world = CombatWorldState(("1", "2"))
        activate_passive(world, passive)
        world.dispatch_native("OtherEvent", "1", {"event.amount": 9})
        world.dispatch_native("OnFixture", "2", {"event.amount": 9})
        self.assertEqual(world.characters["1"].energy, 0)
        world.dispatch_native("OnFixture", "1", {"event.amount": 9})
        self.assertEqual(world.characters["1"].energy, 9)
        self.assertEqual(world.characters["2"].energy, 0)
        world.characters["1"].alive = False
        world.dispatch_native("OnFixture", "1", {"event.amount": 9})
        self.assertEqual(world.characters["1"].energy, 9)

    def test_prediction_keeps_passive_scope_and_changes_are_isolated(self):
        world = self.catalog.world.fork()
        prediction = world.fork()
        uid = next(iter(prediction.native_passives))
        prediction._action_inputs[uid]["bb.fixture"] = 4
        self.assertNotIn("bb.fixture", world._action_inputs[uid])
        self.assertEqual(world.native_passives, prediction.native_passives)
        self.assertEqual(world.snapshot(), world.fork().snapshot())


if __name__ == "__main__":
    unittest.main()
