"""Confirmed final attributes share damage/read views and reversible lifetimes."""

import json
import struct
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.combat_simulation import CombatWorldState
from src.data.damage_attributes import MAIN_ATTACK_RATE, SECONDARY_ATTACK_RATE, DamageAttributeBasis
from src.data.damage_modifiers import DamageBucket, DamageModifierSpec, MagnitudeTerm, ModifierMagnitude
from src.data.damage_quote_data import read_fixed_quote
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState

ROOT = Path(__file__).resolve().parents[1]


class TestDynamicDamageAttributes(unittest.TestCase):
    def setUp(self):
        self.basis = DamageAttributeBasis("智识", "力量", (("力量", 10.34567), ("敏捷", 20),
                                                        ("智识", 100.123456), ("意志", 30)))
        self.panel = FixedDamagePanel(100, .2, 20, self.basis.factor(), 0, .5, attribute_basis=self.basis)
        self.state = TimedDamageState(("caster", "ally"))
        self.hit = DamageHit("caster", "target", "寒冷", 1)

    def add(self, attribute="智识", amount=20, key="first", source="producer", now=0, duration=5):
        self.state.apply_final_attribute_delta(actor="caster", key=key, source=source,
            attribute=attribute, amount=amount, now=now, duration=duration)

    def result(self, now=0, panel=None):
        return self.state.resolve_hit(panel or self.panel, self.hit, now=now)

    def test_primary_secondary_and_unrelated_stats_rebuild_factor_without_reapplying_fixed_terms(self):
        original = self.panel.attack()
        self.add("智识", 20)
        self.add("力量", 30, key="second")
        self.add("敏捷", 40, key="third")
        spec = DamageModifierSpec("attack", DamageBucket.ATTACK, ("all",), "self",
                                 ModifierMagnitude(.15), "confirmed", duration=5)
        self.state.apply(spec, source_actor="caster", now=0, event="confirmed")
        self.assertAlmostEqual(self.result().non_crit,
                               (100 * 1.35 + 20) * (self.basis.factor() + 20 * MAIN_ATTACK_RATE + 30 * SECONDARY_ATTACK_RATE))
        self.assertEqual(self.panel.attack(), original)
        self.assertEqual(self.state.effective_attributes("caster", dict(self.basis.totals), now=0)["敏捷"], 60)
        self.assertEqual(self.result(5).non_crit, original)

    def test_refresh_independent_sources_and_early_removal_restore_only_their_contribution(self):
        self.add()
        self.add(amount=10, source="other", now=1, duration=10)
        self.add(amount=30, now=2, duration=5)
        self.assertAlmostEqual(self.result(5).non_crit, 140 * (self.basis.factor() + 40 * MAIN_ATTACK_RATE))
        self.state.remove_final_attribute_delta(actor="caster", key="first", source="producer")
        self.assertAlmostEqual(self.result(6).non_crit, 140 * (self.basis.factor() + 10 * MAIN_ATTACK_RATE))
        self.assertEqual(self.result(11).non_crit, self.panel.attack())

    def test_unknown_values_missing_basis_and_unknown_lifeng_conversion_are_not_zero(self):
        self.add(amount=None)
        self.assertIsNone(self.result().expected)
        self.assertNotIn("智识", self.state.effective_attributes("caster", dict(self.basis.totals), now=0))
        self.assertEqual(self.result(5).expected, self.panel.attack())
        self.add(now=5)
        self.assertIsNone(self.result(5, replace(self.panel, attribute_basis=None)).expected)
        guarded = replace(self.basis, unverified_attack_dependencies=("智识", "意志"))
        self.assertIsNone(self.result(5, replace(self.panel, attribute_basis=guarded)).expected)
        self.assertEqual(self.result(10).expected, self.panel.attack())

    def test_world_read_and_damage_views_agree_without_aliasing_native_domains(self):
        world = CombatWorldState(("caster", "ally"), regen=0)
        world.damage_state = self.state
        character = world.characters["caster"]
        character.panel = self.panel
        character.attributes.update(dict(self.basis.totals))
        character.attributes["native.final_nonconverted.41"] = 80
        character.attributes["ATK"] = self.panel.attack()
        self.add()
        values = world.damage_inputs("caster")
        self.assertAlmostEqual(values["source.智识"], 120.123456)
        self.assertEqual(values["source.native.final_nonconverted.41"], 80)
        self.assertEqual(values["source.ATK"], self.result().non_crit)
        self.assertNotIn("source.native.armed_nonconverted.41", values)
        world.advance(5)
        self.assertEqual(world.damage_inputs("caster")["source.ATK"], self.panel.attack())

    def test_prediction_and_phase_signature_keep_attribute_state_isolated(self):
        world = CombatWorldState(("caster", "ally"), regen=0)
        signature = world.damage_state.phase_signature(0)
        fork = world.fork()
        fork.damage_state.apply_final_attribute_delta(actor="caster", key="a", source="confirmed",
            attribute="力量", amount=20, now=0, duration=5)
        self.assertNotEqual(signature, fork.damage_state.phase_signature(0))
        self.assertEqual(signature, world.damage_state.phase_signature(0))
        self.assertEqual(signature, fork.damage_state.phase_signature(5))

    def test_unknown_current_source_stat_does_not_reuse_old_hit_evaluation_value(self):
        spec = DamageModifierSpec("live_wis", DamageBucket.AMPLIFICATION, ("all",), "team",
            ModifierMagnitude(terms=(MagnitudeTerm("source.智识", .001),)), "confirmed",
            duration=5, evaluation="hit")
        self.state.apply(spec, source_actor="ally", now=0, event="confirmed", inputs={"source.智识": 100})
        self.assertAlmostEqual(self.result().non_crit, self.panel.attack() * 1.1)
        self.state.apply_final_attribute_delta(actor="ally", key="unknown", source="confirmed",
            attribute="智识", amount=None, now=0, duration=5)
        self.assertIsNone(self.result().non_crit)

    def test_lifetime_and_numeric_contract_reject_implicit_or_nonfinite_values(self):
        for overrides in ({"amount": float("nan")}, {"duration": None}, {"duration": -1}, {"attribute": "native.final_nonconverted.41"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.add(**overrides)

    def test_every_fixed_quote_retains_unrounded_basis_and_prices_known_final_delta(self):
        rows = json.loads((ROOT / "assets/data/fixed_damage_baseline.json").read_text(encoding="utf-8"))
        for row in rows:
            with self.subTest(character=row["key"]):
                quote = read_fixed_quote(row, row["skills"][0])
                self.assertIsNotNone(quote.panel.attribute_basis)
                self.assertAlmostEqual(quote.panel.attribute_basis.factor(), quote.panel.attribute_factor, places=12)
                state = TimedDamageState(("caster",))
                state.apply_final_attribute_delta(actor="caster", key="confirmed", source="scenario",
                    attribute=row["primary_stat"], amount=20, now=0, duration=5)
                result = quote.resolve(state, actor="caster", enemy="target", now=0)
                if row["primary_stat"] in quote.panel.attribute_basis.unverified_attack_dependencies:
                    self.assertIsNone(result.expected)
                else:
                    base = quote.resolve(TimedDamageState(("caster",)), actor="caster", enemy="target", now=0)
                    self.assertAlmostEqual(result.expected / base.expected, (quote.panel.attribute_factor + .1) / quote.panel.attribute_factor)
                self.assertAlmostEqual(quote.resolve(state, actor="caster", enemy="target", now=5).expected,
                                       quote.expected, delta=.051)

    def test_fractional_final_stats_are_floored_only_for_attack_conversion(self):
        self.assertEqual(MAIN_ATTACK_RATE, struct.unpack("<f", struct.pack("<f", .005))[0])
        self.assertEqual(SECONDARY_ATTACK_RATE, struct.unpack("<f", struct.pack("<f", .002))[0])
        self.assertEqual(self.basis.factor(), 1 + 100 * MAIN_ATTACK_RATE + 10 * SECONDARY_ATTACK_RATE)
        self.add(amount=.7)
        self.assertEqual(self.result().non_crit, self.panel.attack())
        self.assertAlmostEqual(self.state.effective_attributes("caster", dict(self.basis.totals), now=0)["智识"],
                               100.823456)
        self.add(amount=.9, now=1)
        self.assertAlmostEqual(self.result(1).non_crit, self.panel.attack() + 140 * MAIN_ATTACK_RATE)
        self.assertEqual(self.result(6).non_crit, self.panel.attack())

    def test_independent_fractional_layers_are_summed_before_floor_and_reversible(self):
        self.add(amount=.6, source="one", duration=5)
        self.add(amount=.6, source="two", duration=10)
        self.assertAlmostEqual(self.result().non_crit, self.panel.attack() + 140 * MAIN_ATTACK_RATE)
        self.assertEqual(self.result(5).non_crit, self.panel.attack())
        self.assertAlmostEqual(self.state.effective_attributes("caster", dict(self.basis.totals), now=5)["智识"],
                               100.723456)
        self.assertEqual(self.result(10).non_crit, self.panel.attack())

    def test_small_negative_delta_crosses_original_floor_and_secondary_boundary(self):
        self.add(amount=-.2)
        self.add("力量", .7, source="second")
        self.assertAlmostEqual(self.result().non_crit,
                               self.panel.attack() + 140 * (-MAIN_ATTACK_RATE + SECONDARY_ATTACK_RATE))
        self.state.remove_final_attribute_delta(actor="caster", key="first", source="producer")
        self.assertAlmostEqual(self.result().non_crit, self.panel.attack() + 140 * SECONDARY_ATTACK_RATE)
        self.assertEqual(self.result(5).non_crit, self.panel.attack())

    def test_flooring_does_not_replace_live_attribute_scaled_bonus_with_integer_input(self):
        self.add(amount=.7)
        spec = DamageModifierSpec("live", DamageBucket.AMPLIFICATION, ("all",), "self",
            ModifierMagnitude(terms=(MagnitudeTerm("source.智识", .001),)), "confirmed",
            duration=5, evaluation="hit")
        self.state.apply(spec, source_actor="caster", now=0, event="confirmed")
        world = CombatWorldState(("caster", "ally"), regen=0)
        world.characters["caster"].panel = self.panel
        world.characters["caster"].attributes.update(self.basis.totals)
        world.damage_state = self.state
        result = self.state.resolve_hit(self.panel, self.hit, now=0, inputs=world.damage_inputs("caster"))
        self.assertAlmostEqual(result.non_crit, self.panel.attack() * (1 + .100823456))

    def test_old_or_unknown_attack_conversion_contract_is_rejected(self):
        data = {"schema_version": 2, "domain": "final_panel", "attack_conversion": "floor_final_main_sub",
                "primary": "智识", "secondary": "力量", "totals": dict(self.basis.totals),
                "unverified_attack_dependencies": []}
        self.assertEqual(DamageAttributeBasis.from_dict(data), self.basis)
        for override in ({"schema_version": 1}, {"attack_conversion": "unrounded"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                DamageAttributeBasis.from_dict({**data, **override})
