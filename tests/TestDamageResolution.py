"""Damage type, timing, recipients, potential selection and fixed-panel regressions."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.character_progression import SNAPSHOT, load_character_progression
from src.data.character_skills import _load_character_from_json, load_all_characters
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude, bind_damage_modifier
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState
from src.data.skill_timing import SkillTimingStore, load_skill_timings

ROOT = Path(__file__).resolve().parents[1]


def modifier(key, bucket, amount, *, recipient="self", elements=("all",), duration=10, **kwargs):
    return DamageModifierSpec(
        key, bucket, elements, recipient, ModifierMagnitude(amount), "on_hit", duration=duration, **kwargs
    )


def skill_modifier(characters, key, kind, effect_id, branch=None):
    skill = next(s for s in characters[key].skills if s.skill_type.value == kind)
    effects = skill.effects if branch is None else next(e for e in skill.enhancements if e.name == branch).effects
    return next(e.damage_modifier for e in effects if e.effect_id.value == effect_id)


class TestDamageResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.characters = load_all_characters(skill_rank=9)

    def setUp(self):
        self.panel = FixedDamagePanel(100, 0.2, 20, 2, 0.1, 0.5)
        self.state = TimedDamageState(("support", "carry"))

    def apply(self, spec, *, source="support", now=0, event=None, **kwargs):
        return self.state.apply(
            spec, source_actor=source, now=now, event=event or spec.trigger, enemy="target", **kwargs
        )

    def hit(self, element="寒冷", actor="carry", now=1, **kwargs):
        return self.state.resolve_hit(self.panel, DamageHit(actor, "target", element, 2), now=now, **kwargs)

    def test_attack_bonus_does_not_multiply_fixed_attack_or_reapply_attributes(self):
        self.apply(modifier("team_attack", DamageBucket.ATTACK, 0.15, recipient="team"))
        result = self.hit()
        self.assertAlmostEqual(result.non_crit, (100 * 1.35 + 20) * 2 * 2)
        self.assertAlmostEqual(self.panel.attack(), 280)

    def test_amplification_vulnerability_and_taken_use_separate_groups(self):
        self.apply(modifier("amp", DamageBucket.AMPLIFICATION, 0.2, recipient="team", elements=("寒冷",)))
        self.apply(
            modifier(
                "vuln", DamageBucket.VULNERABILITY, 0.1, recipient="enemy", elements=("寒冷", "灼热", "电磁", "自然")
            )
        )
        self.apply(modifier("taken", DamageBucket.DAMAGE_TAKEN, 0.15, recipient="enemy"))
        self.assertAlmostEqual(self.hit().non_crit, 280 * 2 * 1.2 * 1.1 * 1.15)
        self.assertAlmostEqual(self.hit("物理").non_crit, 280 * 2 * 1.15)

    def test_static_bonus_and_static_amplification_are_not_counted_twice(self):
        panel = replace(self.panel, amplification={"寒冷": 0.1})
        self.apply(modifier("amp", DamageBucket.AMPLIFICATION, 0.2, recipient="team"))
        hit = DamageHit("carry", "target", "寒冷", 2, damage_bonus=0.4)
        self.assertAlmostEqual(self.state.resolve_hit(panel, hit, now=1).non_crit, 280 * 2 * 1.4 * 1.3)

    def test_main_control_recipient_and_required_outcome(self):
        spec = skill_modifier(self.characters, "xaihi", "战技", "BUFF_SPELL_UP", "满血转法术增幅")
        self.assertFalse(self.apply(spec, event="on_cast", main_control="carry"))
        self.assertFalse(self.apply(spec))
        self.assertTrue(self.apply(spec, main_control="carry"))
        self.assertAlmostEqual(self.hit().non_crit, 280 * 2 * 1.18, places=5)
        self.assertAlmostEqual(self.hit(actor="support").non_crit, 280 * 2)

    def test_rank9_and_potential_adjustment_change_amplification(self):
        spec = skill_modifier(self.characters, "antal", "终结技", "BUFF_BURN_UP")
        self.assertAlmostEqual(spec.magnitude.base, 0.16 * 1.1, places=6)
        self.assertEqual(spec.duration, 12)
        p0 = _load_character_from_json(ROOT / "assets/data/character_skills/antal.json", skill_rank=9, potential=0)
        p0spec = next(e.damage_modifier for e in p0.skills[3].effects if e.effect_id.value == "BUFF_BURN_UP")
        self.assertAlmostEqual(p0spec.magnitude.base, 0.16)

    def test_source_attribute_scaling_cap_and_potential_apply_to_entire_amplification(self):
        spec = skill_modifier(self.characters, "xaihi", "终结技", "BUFF_COLD_UP")
        self.assertIsNone(spec.magnitude.evaluate({}))
        self.assertAlmostEqual(spec.magnitude.evaluate({"source.智识": 1000}), (0.19 + 0.24) * 1.1, places=6)
        self.assertAlmostEqual(spec.magnitude.evaluate({"source.智识": 2000}), (0.19 + 0.30) * 1.1, places=6)
        self.apply(spec, inputs={"source.智识": 1000})
        self.assertAlmostEqual(self.hit(inputs={"source.智识": 2000}).non_crit, 560 * (1 + 0.43 * 1.1), places=4)

    def test_tangtang_uses_consumed_whirlpools_and_requested_rank(self):
        spec = skill_modifier(self.characters, "tangtang", "战技", "VULN_ALL", "消耗涡流形成额外水龙卷")
        self.assertIsNone(spec.magnitude.evaluate({}))
        for count, amount in ((0, 0), (1, 0.04), (2, 0.08)):
            self.assertAlmostEqual(spec.magnitude.evaluate({"consumed.whirlpool": count}), amount)
        self.assertIsNone(spec.magnitude.evaluate({"consumed.whirlpool": 3}))

    def test_current_shred_updates_gravity_vulnerability_at_hit_time(self):
        spec = skill_modifier(self.characters, "gilberta", "终结技", "VULN_ALL")
        self.apply(spec, inputs={"enemy.shred": 0})
        self.assertAlmostEqual(self.hit(inputs={"enemy.shred": 4}).non_crit, 560 * (1 + 0.26 + 4 * 0.026))
        self.assertAlmostEqual(self.hit(inputs={"enemy.shred": 0}).non_crit, 560 * 1.26)

    def test_buffs_expire_at_exact_boundary(self):
        self.apply(modifier("amp", DamageBucket.AMPLIFICATION, 0.2, recipient="team"), now=0)
        self.assertGreater(self.hit(now=9.999).non_crit, 560)
        self.assertAlmostEqual(self.hit(now=10).non_crit, 560)

    def test_same_source_refresh_different_sources_survive(self):
        spec = modifier("amp", DamageBucket.AMPLIFICATION, 0.2, recipient="team")
        self.apply(spec)
        self.apply(spec, now=5)
        self.apply(spec, source="carry", now=7)
        self.assertEqual(len(self.state.modifiers), 4)
        self.assertAlmostEqual(self.hit(now=14).non_crit, 560 * 1.4)
        self.assertAlmostEqual(self.hit(now=15).non_crit, 560 * 1.2)

    def test_morale_stacks_have_independent_expiry_and_cap(self):
        spec = load_character_progression("pogranichnik").damage_modifiers[0]
        for time in (0, 5, 10):
            self.apply(spec, source="carry", now=time)
        self.assertEqual(len(self.state.modifiers), 3)
        self.assertAlmostEqual(self.hit(now=19).non_crit, (100 * 1.44 + 20) * 4, places=4)
        self.assertAlmostEqual(self.hit(now=20).non_crit, (100 * 1.36 + 20) * 4, places=4)
        self.apply(spec, source="carry", now=21)
        self.apply(spec, source="carry", now=22)
        self.assertEqual(len(self.state.modifiers), 3)

    def test_endministrator_p3_self_and_ally_attack_do_not_stack_on_owner(self):
        specs = load_character_progression("endministrator").damage_modifiers
        for spec in specs:
            self.apply(spec, inputs={"enemy.has_crystal": 1})
        self.assertAlmostEqual(
            self.hit("物理", actor="support", inputs={"enemy.has_crystal": 1}).non_crit,
            (100 * 1.5 + 20) * 4 * 1.2,
            places=4,
        )
        self.assertAlmostEqual(
            self.hit("物理", inputs={"enemy.has_crystal": 0}).non_crit, (100 * 1.35 + 20) * 4, places=4
        )
        self.assertAlmostEqual(
            self.hit("寒冷", inputs={"enemy.has_crystal": 1}).non_crit, (100 * 1.35 + 20) * 4, places=4
        )

    def test_conditional_talent_reads_current_enemy_state(self):
        spec = load_character_progression("perlica").damage_modifiers[0]
        self.apply(spec, source="carry")
        self.assertIsNone(self.hit().expected)
        self.assertAlmostEqual(self.hit(inputs={"enemy.staggered": 0}).non_crit, 560)
        self.assertAlmostEqual(self.hit(inputs={"enemy.staggered": 1}).non_crit, 560 * 1.3, places=4)

    def test_random_special_gift_does_not_proc_on_every_ultimate_cast(self):
        spec = skill_modifier(self.characters, "purrchena", "终结技", "VULN_ALL")
        self.assertFalse(self.apply(spec, event="on_cast"))
        self.apply(spec)
        self.assertAlmostEqual(self.hit().non_crit, 560 * (1 + 0.02 * 1.2), places=5)

    def test_unknowns_are_explicit_only_for_matching_damage(self):
        spec = skill_modifier(self.characters, "typhoeus", "连携技", "VULN_NATURAL_BURST")
        # A genuinely unresolved modifier remains unpriced only for its matching tag.
        self.apply(replace(spec, lifetime_scope="timed", duration=None))
        self.assertAlmostEqual(self.hit("自然").non_crit, 560)
        hit = DamageHit("carry", "target", "自然", 2, damage_tag="natural_burst", can_crit=False)
        result = self.state.resolve_hit(self.panel, hit, now=1)
        self.assertIsNone(result.expected)
        self.assertEqual(result.unknown, (spec.key,))

    def test_arcane_forms_have_distinct_duration_and_will_cap(self):
        wisdom = skill_modifier(self.characters, "arcane", "连携技", "VULN_COLD", "应龙四式")
        will = skill_modifier(self.characters, "arcane", "连携技", "VULN_COLD", "应龙四式·阵诀·意")
        self.assertEqual(wisdom.duration, 4)
        self.assertEqual(will.duration, 6)
        self.assertAlmostEqual(wisdom.magnitude.evaluate({"source.意志": 1000}), .04)
        self.assertIsNone(will.magnitude.evaluate({}))
        self.assertAlmostEqual(will.magnitude.evaluate({"source.意志": 400}), .09, places=6)
        self.assertAlmostEqual(will.magnitude.evaluate({"source.意志": 1000}), .115, places=6)
        self.assertFalse(self.apply(will, event="combo_wisdom_hit", inputs={"source.意志": 1000}))
        self.apply(will, inputs={"source.意志": 400})
        # The native combo snapshots the current WILL on application, not on later hits.
        self.assertAlmostEqual(self.hit(now=5, inputs={"source.意志": 1000}).non_crit, 560 * 1.09, places=4)
        self.assertAlmostEqual(self.hit(now=6).non_crit, 560)

    def test_arcane_wisdom_early_end_replaces_combo_vulnerability(self):
        wisdom = skill_modifier(self.characters, "arcane", "连携技", "VULN_COLD", "应龙四式")
        early = skill_modifier(self.characters, "arcane", "战技", "VULN_COLD", "阵诀·智：战技命中囹圄敌人")
        self.apply(wisdom)
        self.apply(early, now=1)
        self.assertEqual(early.duration, 2)
        self.assertAlmostEqual(self.hit(now=2).non_crit, 560 * 1.04)
        self.assertEqual(len(self.state.modifiers), 1)
        self.assertAlmostEqual(self.hit(now=3).non_crit, 560)

    def test_arcane_ranked_cap_and_refund_are_not_max_rank_constants(self):
        maxchar = _load_character_from_json(ROOT / "assets/data/character_skills/arcane.json")
        maxspec = skill_modifier({"arcane": maxchar}, "arcane", "连携技", "VULN_COLD", "应龙四式·阵诀·意")
        self.assertAlmostEqual(maxspec.magnitude.evaluate({"source.意志": 1000}), .12, places=6)
        skill = next(s for s in self.characters["arcane"].skills if s.skill_id == "arcane_skill")
        self.assertEqual(skill.enhancement.resource_changes[0].amount, 28)
        maxskill = next(s for s in maxchar.skills if s.skill_id == "arcane_skill")
        self.assertEqual(maxskill.enhancement.resource_changes[0].amount, 30)

    def test_arcane_potential_adds_will_vulnerability_only_once(self):
        character = _load_character_from_json(ROOT / "assets/data/character_skills/arcane.json", skill_rank=9, potential=1)
        will = skill_modifier({"arcane": character}, "arcane", "连携技", "VULN_COLD", "应龙四式·阵诀·意")
        wisdom = skill_modifier({"arcane": character}, "arcane", "连携技", "VULN_COLD", "应龙四式")
        self.assertAlmostEqual(will.magnitude.evaluate({"source.意志": 1000}), .175, places=6)
        self.assertAlmostEqual(wisdom.magnitude.base, .04)
        # The extra 10 SP is a separately triggered potential producer already present
        # in progression; the ranked base payout stays 28, avoiding a double refund.
        skill = next(s for s in character.skills if s.skill_id == "arcane_skill")
        self.assertEqual(skill.enhancement.resource_changes[0].amount, 28)
        self.assertEqual(character.progression.active_potentials[0].resource_changes[0].amount, 10)

    def test_arrow_field_requires_actual_instance_and_correct_owner(self):
        spec = skill_modifier(self.characters, "typhoeus", "连携技", "VULN_NATURAL_BURST")
        self.assertEqual(spec.duration, 6)
        self.assertFalse(self.apply(spec, event="on_cast"))
        self.assertFalse(self.apply(spec))
        self.state.spawn_field("arrows", source_actor="support", now=1, duration=spec.duration)
        self.assertFalse(self.apply(spec, source="carry", now=2, field_id="arrows"))
        self.assertTrue(self.apply(spec, now=2, field_id="arrows"))
        hit = DamageHit("carry", "target", "自然", 2, damage_tag="natural_burst", can_crit=False)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=6.9).non_crit, 560 * 1.08, places=4)
        self.assertAlmostEqual(self.hit("自然", now=6).non_crit, 560)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=7).non_crit, 560)
        self.assertFalse(self.apply(spec, now=7, field_id="arrows"))

    def test_arrow_field_exit_reentry_and_early_destruction(self):
        spec = skill_modifier(self.characters, "typhoeus", "连携技", "VULN_NATURAL_BURST")
        hit = DamageHit("carry", "target", "自然", 2, damage_tag="natural_burst", can_crit=False)
        self.state.spawn_field("arrows", source_actor="support", now=0, duration=6)
        self.apply(spec, now=1, field_id="arrows")
        self.state.leave_field("arrows", "target")
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=2).non_crit, 560)
        self.apply(spec, now=5, field_id="arrows")
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=5).non_crit, 560 * 1.08, places=4)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=6).non_crit, 560)
        self.state.spawn_field("new_arrows", source_actor="support", now=6, duration=6)
        self.apply(spec, now=6, field_id="new_arrows")
        self.state.remove_field("new_arrows")
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=7).non_crit, 560)

    def test_overlapping_identical_fields_do_not_double_or_remove_each_other(self):
        spec = skill_modifier(self.characters, "typhoeus", "连携技", "VULN_NATURAL_BURST")
        hit = DamageHit("carry", "target", "自然", 2, damage_tag="natural_burst", can_crit=False)
        for name in ("a", "b"):
            self.state.spawn_field(name, source_actor="support", now=0, duration=6)
            self.apply(spec, now=0, field_id=name)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=1).non_crit, 560 * 1.08, places=4)
        self.state.leave_field("b", "target")
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=1).non_crit, 560 * 1.08, places=4)
        self.state.remove_field("a")
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=1).non_crit, 560)

    def test_field_signature_tracks_empty_field_and_unknown_overlap(self):
        empty = self.state.phase_signature(0)
        self.state.spawn_field("a", source_actor="support", now=0, duration=6)
        self.assertNotEqual(self.state.phase_signature(0), empty)
        self.assertNotEqual(self.state.phase_signature(1), self.state.phase_signature(2))
        spec = skill_modifier(self.characters, "typhoeus", "连携技", "VULN_NATURAL_BURST")
        self.apply(spec, now=2, field_id="a")
        self.state.spawn_field("b", source_actor="support", now=2, duration=6)
        self.apply(replace(spec, magnitude=ModifierMagnitude(.2)), now=2, field_id="b")
        hit = DamageHit("carry", "target", "自然", 2, damage_tag="natural_burst")
        self.assertEqual(self.state.resolve_hit(self.panel, hit, now=3).unknown, (spec.key,))
        self.assertEqual(self.state.phase_signature(8), empty)

    def test_ranked_native_parameters_validate_hash_rank_and_key(self):
        store = load_skill_timings()
        self.assertAlmostEqual(store.ranked_parameter("chr_0032_lizhiyan_combo_skill", "max_spell_vul_will", 9), .075)
        with self.assertRaisesRegex(ValueError, "Unavailable skill rank"):
            store.ranked_parameter("chr_0032_lizhiyan_combo_skill", "rate_pre", 13)
        with self.assertRaisesRegex(ValueError, "missing numeric skill parameter"):
            store.ranked_parameter("chr_0032_lizhiyan_combo_skill", "missing", 9)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "index.json").write_bytes((store.path / "index.json").read_bytes())
            (path / "ranked_blackboards.json.gz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Ranked blackboard hash mismatch"):
                SkillTimingStore(path).ranked_parameter("chr_0032_lizhiyan_combo_skill", "rate_pre", 9)

    def test_crit_clamp_and_noncritical_damage(self):
        self.apply(modifier("crit", DamageBucket.CRIT_RATE, 1, recipient="team"))
        self.apply(modifier("crit_damage", DamageBucket.CRIT_DAMAGE, 0.6, recipient="team"))
        self.assertAlmostEqual(self.hit().expected, 560 * 2.1)
        hit = DamageHit("carry", "target", "寒冷", 2, can_crit=False)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=1).expected, 560)

    def test_yvonne_stacks_use_remaining_stance_time(self):
        spec = skill_modifier(self.characters, "yvonne", "终结技", "BUFF_CRIT_RATE_UP", "强化普攻累积暴击层数")
        self.apply(spec, source="carry", now=2, inputs={"ultimate.remaining": 5, "source.crit_stacks": 2})
        self.assertAlmostEqual(self.hit(now=6, inputs={"source.crit_stacks": 10}).expected, 560 * (1 + 0.4 * 0.5))
        self.assertAlmostEqual(self.hit(now=7, inputs={"source.crit_stacks": 10}).expected, 560 * 1.05)

    def test_buff_removal_on_one_enemy_does_not_remove_other_enemies(self):
        spec = modifier("vuln", DamageBucket.VULNERABILITY, 0.2, recipient="enemy")
        self.apply(spec)
        self.state.apply(spec, source_actor="support", now=0, event=spec.trigger, enemy="other")
        self.state.remove(spec.key, "support", "target")
        self.assertAlmostEqual(self.hit().non_crit, 560)
        hit = DamageHit("carry", "other", "寒冷", 2)
        self.assertAlmostEqual(self.state.resolve_hit(self.panel, hit, now=1).non_crit, 560 * 1.2)

    def test_all_canonical_damage_effects_have_explicit_bindings_and_provenance(self):
        damage_ids = {
            "BUFF_ATTACK_UP",
            "BUFF_CRIT_RATE_UP",
            "BUFF_CRIT_DMG_UP",
            "BUFF_DAMAGE_UP",
            "BUFF_COLD_UP",
            "BUFF_BURN_UP",
            "BUFF_ELECTROMAGNETIC_UP",
            "BUFF_NATURAL_UP",
            "BUFF_SPELL_UP",
        }
        found = []
        for character in self.characters.values():
            for skill in character.skills:
                for effect in [*skill.effects, *(e for b in skill.enhancements for e in b.effects)]:
                    if effect.effect_id.value in damage_ids or effect.effect_id.value.startswith("VULN_"):
                        self.assertIsNotNone(effect.damage_modifier, f"{skill.skill_id}/{effect.effect_id}")
                        self.assertTrue(effect.damage_modifier.sources)
                        found.append(effect.damage_modifier)
        self.assertGreater(len(found), 30)
        index = json.loads((SNAPSHOT / "index.json").read_text(encoding="utf-8"))
        raw = (SNAPSHOT.parent / "damage_modifier_rules.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), index["damage_modifier_rules_sha256"])

    def test_bad_rank_or_missing_row_does_not_default_to_marker(self):
        with self.assertRaisesRegex(ValueError, "Unavailable skill rank"):
            load_all_characters(skill_rank=13)
        with self.assertRaisesRegex(ValueError, "Ambiguous/missing damage row"):
            bind_damage_modifier(
                {"strength": {"base": {"row": "missing"}}},
                skill_id="s",
                skills={"s": {"rank_stats": {"levels": ["RANK 1"], "rows": []}}},
                rank=1,
            )

    def test_fixed_equipment_skill_bonus_and_amplification_are_separate(self):
        spec = importlib.util.spec_from_file_location(
            "damage_calculator", ROOT / "scripts/skill-data/compute_damage_baseline.py"
        )
        calculator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(calculator)
        self.assertEqual(
            calculator._parse_stat_clause("战技伤害提升20%"), {"kind": "pct", "stat": "skill_dmg", "value": 20}
        )
        self.assertEqual(
            calculator._parse_stat_clause("寒冷增幅+10%"), {"kind": "pct", "stat": "amp_寒冷", "value": 10}
        )
        mods = []
        calculator._collect_stats("战技伤害提升20%。施加寒冷附着后，寒冷增幅+10%，持续5秒。", mods, [], "set")
        self.assertEqual(mods, [{"kind": "pct", "stat": "skill_dmg", "value": 20}])

    def test_fixed_panels_match_baseline_selection_and_do_not_include_rotation_bundles(self):
        rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual({row["key"] for row in rows}, set(self.characters))
        for row in rows:
            profile = row["profile"]
            self.assertEqual(profile["potential"], self.characters[row["key"]].progression.baseline.potential)
            self.assertEqual(profile["conditional_talent_state"], "untriggered")
            self.assertNotIn("cycle_expect", row)
            self.assertNotIn("cycle_expect_link4", row)
            panel = FixedDamagePanel(**row["panel"]["damage_basis"])
            self.assertAlmostEqual(panel.attack(), row["panel"]["ATK"], places=1)
            self.assertTrue(all("full_expect" not in s for s in row["skills"]))
        avywenna = next(row for row in rows if row["key"] == "avywenna")
        self.assertIn("chr_0012_avywen_potential_3", avywenna["profile"]["constant_passive_sources"])
        self.assertGreaterEqual(avywenna["panel"]["elem_dmg_pct"]["电磁"], 8)


if __name__ == "__main__":
    unittest.main()
