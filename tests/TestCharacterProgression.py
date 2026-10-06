"""Progression selection and native producer coverage regressions."""

import gzip
import hashlib
import importlib.util
import json
import unittest
from dataclasses import replace

from src.data.character_progression import SNAPSHOT, load_character_progression
from src.data.character_skills import load_all_characters
from src.data.effect_semantics import EFFECT_SEMANTICS, RefreshPolicy
from src.data.effects import EffectType
from src.data.skill_types import SkillResourceChange


def script(name):
    path = SNAPSHOT.parents[3] / "scripts/skill-data" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCharacterProgression(unittest.TestCase):
    def test_every_character_has_max_combat_talents_and_explicit_potential_baseline(self):
        characters = load_all_characters()
        self.assertEqual(len(characters), 32)
        self.assertEqual(sum(len(c.progression.talents) for c in characters.values()), 64)
        for key, character in characters.items():
            p = character.progression
            with self.subTest(character=key):
                self.assertIsNotNone(p)
                self.assertEqual(len(character.skills), 4)
                expected = 3 if key == "endministrator" else 0 if character.star == 6 else 5
                self.assertEqual(p.baseline.potential, expected)
                self.assertEqual(len(p.active_potentials), expected)
                for talent in p.talents:
                    self.assertEqual(talent.level, max(t.level for t in p.talent_ranks if t.slot == talent.slot))
        self.assertEqual(next(t for t in characters["typhoeus"].progression.talents if t.slot == 0).level, 3)

    def test_endministrator_uses_shared_current_talents_and_can_override_potential(self):
        p = load_character_progression("endministrator")
        self.assertEqual(p.native_id, "chr_9000_endmin")
        self.assertEqual(p.baseline.potential_basis, "user_endministrator_p3")
        self.assertEqual(p.baseline.potential, 3)
        self.assertTrue(all(t.effect_id.startswith("chr_9000_endmin") for t in p.talents))
        self.assertIn("+30%", p.talents[0].description)
        self.assertEqual(p.active_potentials[0].resource_changes[0].amount, 50)
        energy = p.active_potentials[2].resource_changes[0]
        self.assertEqual(energy.amount, 15)
        self.assertIs(energy.affected_by_energy_gain, False)
        self.assertEqual(energy.count_basis, "consumed")
        p0 = replace(p, baseline=replace(p.baseline, potential=0))
        self.assertEqual(p0.active_potentials, ())
        self.assertEqual(p0.talents, p.talents)
        unknown = replace(p, baseline=replace(p.baseline, potential=None))
        with self.assertRaisesRegex(ValueError, "unverified"):
            _ = unknown.active_potentials

    def test_snapshot_hashes_and_resource_paths_point_to_complete_native_trees(self):
        index = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf-8"))
        for name, field in (
            ("characters.json", "characters_sha256"),
            ("tables.json.gz", "tables_sha256"),
            ("supplement.json.gz", "supplement_sha256"),
            ("resource_actions.json.gz", "resource_audit_sha256"),
            ("raw.zip", "raw_sha256"),
            ("endmin_reward_scan.json", "endmin_reward_scan_sha256"),
        ):
            self.assertEqual(hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest(), index[field])
        records = json.loads(
            gzip.decompress((SNAPSHOT.parent.parent / "skill_timings/20261002/records.json.gz").read_bytes())
        )
        records.update(json.loads(gzip.decompress((SNAPSHOT / "supplement.json.gz").read_bytes())))
        audit = json.loads(gzip.decompress((SNAPSHOT / "resource_actions.json.gz").read_bytes()))
        for name, entries in audit.items():
            for entry in entries:
                node = records[name]["data"]
                for part in entry["path"].split("/")[1:]:
                    node = node[int(part)] if isinstance(node, list) else node[part]
                self.assertEqual(node, entry["action"])
                self.assertEqual(entry["execution_status"], "requires_action_interpreter")
        for character in load_all_characters().values():
            for passive in (*character.progression.talent_ranks, *character.progression.potentials):
                for modifier in passive.modifiers:
                    for arm, field in (("attachSkill", "skillId"), ("attachBuff", "buffId")):
                        if arm in modifier:
                            self.assertIn(modifier[arm][field], records)

    def test_private_action_counts_reset_and_crit_is_not_granted_on_cast(self):
        characters = load_all_characters()
        yvonne = characters["yvonne"].skills[3]
        self.assertEqual([e.effect_id for e in yvonne.effects], [EffectType.STATUS_YVONNE_ASSISTED])
        cap = yvonne.enhancements[-1]
        self.assertFalse(
            cap.is_trigger_satisfied({EffectType.STATUS_YVONNE_ASSISTED: 1, EffectType.STACK_YVONNE_CRIT: 9})
        )
        self.assertTrue(
            cap.is_trigger_satisfied({EffectType.STATUS_YVONNE_ASSISTED: 1, EffectType.STACK_YVONNE_CRIT: 10})
        )
        self.assertEqual(EFFECT_SEMANTICS[EffectType.STACK_AIR_ATTACKS].refresh, RefreshPolicy.REPLACE)
        for s in characters["typhoeus"].skills[1:]:
            reset = next(e for e in s.effects if e.effect_id == EffectType.STACK_AIR_ATTACKS)
            self.assertEqual(reset.count, 5)
        laevatain = characters["laevatain"].skills[1]
        self.assertFalse(laevatain.effects)
        self.assertEqual(laevatain.enhancement.evaluation_point, "before_action")

    def test_resource_snapshot_and_caps_survive_loading(self):
        c = SkillResourceChange.from_dict(
            {
                "resource": "skill_point",
                "target": "team",
                "kind": "dynamic",
                "max_amount": 100,
                "count_basis": "kills",
                "trigger": "on_kill",
            }
        )
        self.assertEqual(c.max_amount, 100)
        self.assertEqual(c.count_basis, "kills")
        self.assertEqual(c.trigger, "on_kill")
        p = load_character_progression("ember")
        self.assertEqual(
            (p.baseline.character_level, p.baseline.skill_rank, p.baseline.weapon_name), (80, 9, "终点之声")
        )

    def test_supplement_roundtrip_refuses_modified_decoded_data(self):
        importer = script("import_native_progression")
        records = json.loads(gzip.decompress((SNAPSHOT / "supplement.json.gz").read_bytes()))
        index = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf-8"))
        plans = json.loads(
            gzip.decompress((SNAPSHOT.parent.parent / "skill_timings/20261002/native_read_plans.json.gz").read_bytes())
        )
        importer.verify_supplement(records, index["supplement_verification"], SNAPSHOT / "raw.zip", plans)
        records[next(iter(records))]["data"]["id"] += "_corrupted"
        with self.assertRaisesRegex(ValueError, "roundtrip failed"):
            importer.verify_supplement(records, index["supplement_verification"], SNAPSHOT / "raw.zip", plans)

    def test_ember_comparison_uses_rank9_and_keeps_conditional_stagger_separate(self):
        result = script("compute_progression_baseline").comparison("ember")
        self.assertEqual(result["profile"]["potential"], 0)
        self.assertNotIn("cycle_expect", result)
        self.assertNotIn("cycle_expect_link4", result)
        self.assertFalse(any("循环期望" in line for line in result["trace"]))
        self.assertEqual(result["primary_stat"], "力量")
        self.assertEqual(result["secondary_stat"], "意志")
        battle = next(s for s in result["skills"] if s["type"] == "战技")
        self.assertEqual(battle["multiplier_pct"], 312)
        self.assertEqual(battle["stagger"], 10)
        self.assertTrue(any("受击" in row for row in battle["conditional_rows"]))
        self.assertEqual(result["skills"][0]["multiplier_pct"], 431)

    def test_described_cost_reductions_are_modifiers_not_energy_payouts(self):
        p = load_character_progression("arclight")
        reduction = next(x for x in p.active_potentials if x.level == 4)
        self.assertIn("-15%", reduction.description)
        self.assertNotIn("{", reduction.description)
        self.assertEqual(reduction.resource_changes, ())
        (modifier,) = reduction.resource_modifiers
        self.assertEqual(
            (modifier.scope, modifier.operation, modifier.resource), ("cost", "multiply", "ultimate_energy")
        )
        self.assertAlmostEqual(90 * modifier.value, 76.5, places=4)
        self.assertEqual(modifier.skill_ids, ("chr_0007_ikut_ultimate_skill",))

    def test_potential_resource_scaling_preserves_base_rule_and_consumed_units(self):
        c = load_character_progression("camille")
        self.assertEqual(c.active_potentials, ())  # Six-star P0 still retains override definitions.
        potential = next(x for x in c.potentials if x.level == 3)
        (modifier,) = potential.resource_modifiers
        self.assertEqual(modifier.scope, "recovery")
        self.assertEqual(potential.resource_changes, ())
        self.assertEqual((20 * modifier.value, 40 * modifier.value), (23, 46))
        self.assertEqual(len(modifier.skill_ids), 2)
        a = load_character_progression("akekuri").talents[0]
        self.assertEqual(a.resource_changes, ())
        (dynamic,) = a.resource_modifiers
        self.assertIsNone(dynamic.value)
        self.assertIn("actor.intellect", dynamic.formula)
        bb = a.parameters
        self.assertEqual((bb["rate"], bb["max_ratio"]), (10, 0.75))
        self.assertAlmostEqual(bb["sub_ratio"], 0.015)
        for intellect, expected in ((0, 1), (100, 1.15), (500, 1.75), (2000, 1.75)):
            self.assertAlmostEqual(1 + min(intellect / bb["rate"] * bb["sub_ratio"], bb["max_ratio"]), expected)
        t = next(x for x in load_character_progression("tangtang").potentials if x.level == 1)
        (extra,) = t.resource_changes
        self.assertEqual(extra.per_unit, 5)
        self.assertEqual(extra.count_basis, "consumed")
        self.assertEqual(extra.source_effect_id, EffectType.STACK_WHIRLPOOL)
        self.assertEqual(extra.max_units, 2)
        y = next(x for x in load_character_progression("yvonne").potentials if x.level == 1)
        self.assertEqual(y.resource_changes[0].amount, 15)  # Native usp_extra replaces 10 by 25.

    def test_described_resource_review_separates_observers_from_producers(self):
        index = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf-8"))
        raw = (SNAPSHOT / "description_resource_review.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), index["description_resource_review_sha256"])
        review = json.loads(raw)
        self.assertEqual(len(review), 62)
        self.assertFalse(any(r["status"] == "unresolved" for r in review.values()))
        self.assertEqual(review["chr_0019_karin_potential_1"]["status"], "resource_observer")
        self.assertEqual(review["chr_0029_pograni_talent_1_2"]["status"], "resource_observer")
        self.assertEqual(review["chr_0029_pograni_potential_5"]["status"], "mapped_modifier")
        for name, field in (
            ("semantic_rules.json", "semantic_rules_sha256"),
            ("resource_modifier_rules.json", "resource_modifier_rules_sha256"),
        ):
            raw = (SNAPSHOT.parent / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), index[field])

    def test_cached_snapshot_is_not_mutated_by_a_loaded_character(self):
        p = load_character_progression("ember")
        p.talents[0].parameters["shelterrate"] = 99
        p2 = load_character_progression("ember")
        self.assertAlmostEqual(p2.talents[0].parameters["shelterrate"], 0.5)


if __name__ == "__main__":
    unittest.main()
