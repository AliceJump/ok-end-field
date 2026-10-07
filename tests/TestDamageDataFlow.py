"""Per-skill evidence, numerical replay, tag fidelity and honest coverage."""

import importlib.util
import json
import unittest
from copy import deepcopy
from pathlib import Path

from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, MagnitudeTerm, ModifierMagnitude
from src.data.damage_quote_data import read_fixed_quote, verify_sources
from src.data.damage_resolution import TimedDamageState

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_damage_data_flow", ROOT / "scripts/skill-data/audit_damage_data_flow.py")
auditor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auditor)


class TestDamageDataFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        cls.by_key = {row["key"]: row for row in cls.rows}

    def quote(self, key="ember", kind="战技"):
        row = self.by_key[key]
        skill = next(skill for skill in row["skills"] if skill["type"] == kind)
        return read_fixed_quote(row, skill)

    def test_every_character_and_skill_has_replayable_evidence(self):
        self.assertEqual(len(self.rows), 32)
        total = 0
        for row in self.rows:
            verify_sources(row)
            for skill in row["skills"]:
                quote = read_fixed_quote(row, skill)
                self.assertEqual(quote.skill_id, skill["skill_id"])
                self.assertEqual(skill["quote_basis"]["skill_rank"], row["profile"]["skill_rank"])
                total += 1
        self.assertEqual(total, 128)

    def test_ember_physical_attack_does_not_inherit_characters_fire_element(self):
        self.assertEqual(self.by_key["ember"]["element"], "灼热")
        self.assertEqual(self.quote(kind="普通攻击").element, "物理")
        self.assertEqual(self.quote().panel.attack(), self.quote(kind="普通攻击").panel.attack())

    def test_runtime_skill_tag_filters_do_not_leak_to_other_skill_types(self):
        state = TimedDamageState(("carry",))
        spec = DamageModifierSpec("combo_only", DamageBucket.DAMAGE_BONUS, ("all",), "self",
                                  ModifierMagnitude(.5), "confirmed", damage_tags=("combo",), duration=5)
        state.apply(spec, source_actor="carry", now=0, event="confirmed")
        for kind in ("战技", "终结技", "普通攻击", "连携技"):
            quote = self.quote(kind=kind)
            result = quote.resolve(state, actor="carry", enemy="target", now=1)
            self.assertEqual(result.buckets["damage_bonus"], .5 if kind == "连携技" else 0)

    def test_fixed_panel_and_dynamic_groups_are_applied_once(self):
        quote = self.quote("avywenna")
        state = TimedDamageState(("support", "carry"))
        specs = (
            DamageModifierSpec("amp", DamageBucket.AMPLIFICATION, (quote.element,), "team", ModifierMagnitude(.2), "confirmed", duration=5),
            DamageModifierSpec("vuln", DamageBucket.VULNERABILITY, (quote.element,), "enemy", ModifierMagnitude(.1), "confirmed", duration=5),
        )
        for spec in specs:
            state.apply(spec, source_actor="support", enemy="target", now=0, event="confirmed")
        result = quote.resolve(state, actor="carry", enemy="target", now=1)
        fixed = quote.panel.amplification[quote.element]
        base = quote.resolve(TimedDamageState(("carry",)), actor="carry", enemy="target", now=1)
        self.assertAlmostEqual(result.expected / base.expected, (1 + fixed + .2) / (1 + fixed) * 1.1)
        after = quote.resolve(state, actor="carry", enemy="target", now=5)
        self.assertAlmostEqual(after.expected, base.expected)

    def test_quote_resolution_never_invents_missing_attribute_inputs(self):
        quote = self.quote()
        state = TimedDamageState(("carry",))
        spec = DamageModifierSpec("needs_will", DamageBucket.ATTACK, ("all",), "self",
                                  ModifierMagnitude(terms=(MagnitudeTerm("source.意志", .001),)),
                                  "confirmed", duration=5)
        state.apply(spec, source_actor="carry", now=0, event="confirmed")
        self.assertIsNone(quote.resolve(state, actor="carry", enemy="target", now=1).expected)
        self.assertEqual(len(state.modifiers), 1)

    def test_missing_basis_bad_rank_tags_and_nonnumeric_inputs_are_rejected(self):
        row = deepcopy(self.by_key["ember"])
        for field, value in (("skill_rank", 12), ("damage_tags", ["ultimate"]), ("multiplier", None)):
            skill = deepcopy(row["skills"][1])
            skill["quote_basis"][field] = value
            with self.assertRaises((KeyError, ValueError)):
                read_fixed_quote(row, skill)
        skill = deepcopy(row["skills"][1])
        del skill["quote_basis"]
        with self.assertRaises(KeyError):
            read_fixed_quote(row, skill)

    def test_changed_quote_or_evidence_is_not_silently_accepted(self):
        row = deepcopy(self.by_key["ember"])
        skill = deepcopy(row["skills"][1])
        skill["crit_expect"] += 1
        with self.assertRaisesRegex(ValueError, "replay mismatch"):
            read_fixed_quote(row, skill)
        first = next(iter(row["data_flow"]["sources"]))
        row["data_flow"]["sources"][first] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Stale fixed quote source"):
            verify_sources(row)
        del row["data_flow"]["sources"][first]
        with self.assertRaisesRegex(ValueError, "Incomplete fixed quote source ledger"):
            verify_sources(row)

    def test_audit_keeps_missing_and_duplicate_quotes_on_their_skill_rows(self):
        row = deepcopy(self.by_key["ember"])
        missing = row["skills"].pop(0)["skill_id"]
        duplicate = row["skills"][0]["skill_id"]
        row["skills"].append(deepcopy(row["skills"][0]))
        report = auditor.audit([row])
        skills = {skill["skill_id"]: skill for skill in report["characters"][0]["skills"]}
        for skill_id, count in ((missing, 0), (duplicate, 2)):
            self.assertEqual(skills[skill_id]["quote_status"], "incomplete")
            self.assertIn(f"Expected one quote, found {count}", skills[skill_id]["data_gaps"])
        self.assertEqual(report["summary"]["skills_with_structural_data_gaps"], 2)

    def test_audit_retains_branch_guards_and_does_not_claim_semantic_completion(self):
        report = auditor.audit(self.rows)
        self.assertEqual(report["summary"]["replay_verified_quotes"], 128)
        self.assertIsNone(report["summary"]["complete_characters"])
        self.assertEqual(report["summary"]["skills_pending_semantic_review"], 128)
        self.assertFalse(report["missing_characters"])
        mifu = next(row for row in report["characters"] if row["key"] == "mi_fu")
        battle = next(skill for skill in mifu["skills"] if skill["type"] == "战技")
        self.assertTrue(any(branch["replaces_base_action"] for branch in battle["branches"]))
        self.assertTrue(any(branch["trigger_effect_groups"] for branch in battle["branches"]))
        self.assertTrue(all(not passive["complete_semantic_review"] for row in report["characters"] for passive in row["passives"]))

    def test_passive_ownership_is_separate_from_parameter_dependencies(self):
        report = auditor.audit(self.rows)
        self.assertEqual(report["summary"]["selected_passive_damage_rules"], 15)
        endmin = next(row for row in report["characters"] if row["key"] == "endministrator")
        passives = {row["effect_id"]: row for row in endmin["passives"]}
        talent = passives["chr_9000_endmin_talent_1_2"]
        potential = passives["chr_9000_endmin_potential_2"]
        self.assertEqual([rule["recipient"] for rule in talent["damage_rules"]], ["self"])
        self.assertEqual([rule["recipient"] for rule in potential["damage_rules"]], ["other_allies"])
        self.assertEqual(talent["runtime_rule_parameter_references"],
                         ["chr_9000_endmin_potential_2:attack"])
        self.assertEqual(potential["placement"], "runtime_rules")
        xaihi = next(row for row in report["characters"] if row["key"] == "xaihi")
        adjustment = next(row for row in xaihi["passives"] if row["effect_id"] == "chr_0011_seraph_potential_5")
        self.assertEqual(adjustment["placement"], "skill_rule_parameters")
        self.assertEqual(len(adjustment["skill_rule_references"]), 2)

    def test_first_team_base_quotes_exclude_replacement_and_conditional_damage(self):
        cases = (("mi_fu", "战技", 1.5), ("camille", "战技", 2.0), ("camille", "连携技", 3.0),
                 ("pogranichnik", "连携技", .95), ("pogranichnik", "终结技", 3.0))
        for key, kind, multiplier in cases:
            with self.subTest(character=key, skill=kind):
                self.assertEqual(self.quote(key, kind).multiplier, multiplier)
                skill = next(skill for skill in self.by_key[key]["skills"] if skill["type"] == kind)
                self.assertEqual(skill["quote_basis"]["scope"], "reviewed_base_rows")
                self.assertTrue(skill["row_semantics"]["components"])
        mifu = next(skill for skill in self.by_key["mi_fu"]["skills"] if skill["type"] == "战技")
        kaitian = next(c for c in mifu["row_semantics"]["components"] if c["key"] == "开天")
        self.assertEqual(kaitian["damage_class"], "physical_infliction")

    def test_first_team_all_skills_have_reviewed_membership_and_honest_aggregation_scopes(self):
        for key in ("mi_fu", "pogranichnik", "ember", "camille"):
            for skill in self.by_key[key]["skills"]:
                self.assertEqual(skill["quote_basis"]["scope"], "reviewed_base_rows")
                if skill["type"] == "普通攻击":
                    self.assertEqual(self.quote(key, skill["type"]).aggregation_scope, "full_normal_sequence_not_one_hit")
        self.assertIn("without_physical_infliction", self.quote("pogranichnik").aggregation_scope)

    def test_self_consistent_but_wrong_reviewed_quote_is_caught_against_rank_source(self):
        row = deepcopy(self.by_key["mi_fu"])
        skill = next(skill for skill in row["skills"] if skill["type"] == "战技")
        skill["quote_basis"]["multiplier"] *= 2
        quote = self.quote("mi_fu")
        result = quote.resolve(TimedDamageState(("actor",)), actor="actor", enemy="target", now=0)
        skill["non_crit"] = round(result.non_crit * 2, 1)
        skill["crit_expect"] = round(result.expected * 2, 1)
        # It still replays arithmetically; rank evidence must catch the wrong amount.
        read_fixed_quote(row, skill)
        report = auditor.audit([row])
        audited = next(s for s in report["characters"][0]["skills"] if s["type"] == "战技")
        self.assertEqual(audited["quote_status"], "incomplete")
        self.assertIn("Reviewed quote multiplier differs from selected rank rows", audited["data_gaps"])


if __name__ == "__main__":
    unittest.main()
