"""Selected P5 critical terms stay on ultimate hits and out of global CRIT."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_catalog import build_combat_catalog
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, ModifierMagnitude
from src.data.damage_quote_data import read_fixed_quote
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState
from src.data.fixed_skill_modifiers import fixed_skill_crit
from src.data.skill_timing import SkillTimingStore

ROOT = Path(__file__).resolve().parents[1]


class TestFixedSkillModifiers(unittest.TestCase):
    def test_native_potential_and_damage_processor_use_the_same_critical_parameter(self):
        character = get_character("perlica")
        bonuses, sources = fixed_skill_crit(character)
        self.assertEqual(len(sources), 1)
        self.assertEqual(bonuses, {"ultimate": sources[0].parameters["crit"]})
        data = SkillTimingStore().record("chr_0004_pelica_ultimate_skill")["data"]

        def processors(value):
            if isinstance(value, dict):
                if "damageUnits" in value:
                    for unit in value["damageUnits"]:
                        if unit["damageAttributeType"] == 0:
                            yield unit["damageProcessors"]
                for child in value.values():
                    yield from processors(child)
            elif isinstance(value, list):
                for child in value:
                    yield from processors(child)

        found = list(processors(data))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0], [{"$tag": 9, "$type": "Beyond.Gameplay.Core.InstantModifyAttribute",
                                  "$value": {"modifyTargetSide": 0, "modifier": {
                                      "attributeType": 9, "formulaItem": 5, "modifyAttributeType": 0,
                                      "param": {"blackboardKey": "crit", "useBlackboardKey": True, "value": 0.0}}}}])

    def test_selected_potential_and_other_characters_do_not_gain_unselected_bonus(self):
        self.assertFalse(fixed_skill_crit(get_character("perlica", potential=4))[0])
        self.assertFalse(fixed_skill_crit(get_character("ember"))[0])
        character = get_character("perlica")
        passive = character.progression.potentials[-1]
        changed = replace(passive, modifiers=({"unsupported": True},))
        progression = replace(character.progression, potentials=(*character.progression.potentials[:-1], changed))
        with self.assertRaisesRegex(ValueError, "Changed reviewed"):
            fixed_skill_crit(replace(character, progression=progression))

    def test_fixed_tag_bonus_combines_with_timed_critical_once_and_clamps_after_addition(self):
        panel = FixedDamagePanel(100, 0, 0, 1, .1, .5, crit_rate_bonus={"ultimate": .3})
        state = TimedDamageState(("owner", "ally"))
        spec = DamageModifierSpec("crit", DamageBucket.CRIT_RATE, ("all",), "self",
                                  ModifierMagnitude(.2), "confirmed", duration=2)
        state.apply(spec, source_actor="owner", now=0, event="confirmed")
        for tag in ("normal", "skill", "combo", "ultimate", "physical_anomaly", "dot"):
            hit = DamageHit("owner", "target", "电磁", 1, damage_tag=tag, damage_tags=(tag, tag))
            result = state.resolve_hit(panel, hit, now=0)
            self.assertEqual(result.non_crit, 100)
            self.assertAlmostEqual(result.expected, 130 if tag == "ultimate" else 115)
        hit = DamageHit("owner", "target", "电磁", 1, damage_tag="ultimate")
        self.assertAlmostEqual(state.resolve_hit(panel, hit, now=2).expected, 120)
        self.assertEqual(state.resolve_hit(replace(panel, crit_rate=.9), hit, now=2).expected, 150)
        self.assertEqual(state.resolve_hit(panel, replace(hit, can_crit=False), now=2).expected, 100)
        ally_panel = replace(panel, crit_rate_bonus={})
        self.assertAlmostEqual(state.resolve_hit(ally_panel, replace(hit, actor="ally"), now=0).expected, 105)

    def test_default_fixed_quotes_and_catalog_preserve_displayed_critical_rate(self):
        row = next(row for row in json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(
            encoding="utf8")) if row["key"] == "perlica")
        panel = FixedDamagePanel(**row["panel"]["damage_basis"])
        bonus = fixed_skill_crit(get_character("perlica"))[0]["ultimate"]
        self.assertIn("chr_0004_pelica_potential_5", row["profile"]["constant_passive_sources"])
        for skill in row["skills"]:
            quote = read_fixed_quote(row, skill)
            rate = panel.crit_rate + (bonus if skill["type"] == "终结技" else 0)
            result = quote.resolve(TimedDamageState(("owner",)), actor="owner", enemy="target", now=0)
            self.assertAlmostEqual(result.expected / result.non_crit, 1 + rate * panel.crit_damage)
        world = build_combat_catalog(("佩丽卡", "余烬"), SkillTimingStore()).world
        self.assertEqual(world.characters["1"].panel.crit_rate_bonus, {"ultimate": bonus})
        self.assertFalse(world.characters["2"].panel.crit_rate_bonus)
        self.assertEqual(world.characters["1"].panel.crit_rate, panel.crit_rate)
