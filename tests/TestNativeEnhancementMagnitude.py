"""Proven ADDSS rate accumulation with explicit, still-unproduced marker counts."""

import unittest

from src.data.character_skills import get_character
from src.data.damage_modifiers import MagnitudeTerm, ModifierMagnitude, bind_damage_modifier
from src.data.damage_resolution import DamageHit, FixedDamagePanel, TimedDamageState
from src.data.native_zhuangfy_evidence import release_evidence

COUNT = "source.zhuangfy_talent1_marks"


class TestNativeEnhancementMagnitude(unittest.TestCase):
    def spec(self):
        return next(spec for spec in get_character("zhuang_fangyi").progression.damage_modifiers
                    if spec.key == "chr_0030_zhuangfy_talent_1_2:amplification")

    def test_actual_selected_native_edit_is_repeated_float32_not_linear(self):
        character = get_character("zhuang_fangyi")
        evidence = release_evidence(character)[0]
        edit = evidence["enhanced_action"]["enhancingList"][0]
        self.assertEqual(edit["operationType"], 1)  # Native ADDSS branch at 4e23184.
        self.assertEqual(edit["value"]["blackboardKey"], "enhance_rate")
        self.assertEqual(edit["buffIds"], ["buff_chr_0030_zhuangfy_talent1_mark"])
        self.assertEqual(evidence["keyword_template"], "buff_common_affixes_enhance_pulse")
        self.assertFalse(evidence["enhanced_action"]["overrideChildBuffId"])
        self.assertEqual(evidence["selected_keyword_child"], "buff_common_affixes_enhance_pulse_default_child")
        self.assertNotEqual(evidence["selected_keyword_child"], evidence["enhanced_action"]["childBuffId"]["value"])
        spec = self.spec()
        self.assertEqual(spec.magnitude.accumulation, "native_float32_repeated_add")
        self.assertEqual(spec.magnitude.base, evidence["selected_parameters"]["base_rate"])
        self.assertEqual(spec.magnitude.terms[0].coefficient, evidence["selected_parameters"]["enhance_rate"])
        expected = {0: .18000000715255737, 1: .20000000298023224,
                    3: .23999999463558197, 10: .3800000548362732}
        for count, rate in expected.items():
            self.assertEqual(spec.magnitude.evaluate({COUNT: count}), rate)
        linear = ModifierMagnitude(spec.magnitude.base, spec.magnitude.terms)
        self.assertNotEqual(spec.magnitude.evaluate({COUNT: 10}), linear.evaluate({COUNT: 10}))

    def test_missing_old_hit_count_and_invalid_marker_count_stay_unknown(self):
        magnitude = self.spec().magnitude
        self.assertIsNone(magnitude.evaluate({}))
        self.assertIsNone(magnitude.evaluate({"source.battle_lightning_hits": 0}))
        for count in (-1, .5, float("nan"), float("inf"), 100001):
            with self.subTest(count=count):
                self.assertIsNone(magnitude.evaluate({COUNT: count}))
        # Budget rejection is unknown, not gameplay clamping to 100000 marks.
        bad = ModifierMagnitude(1e100, (MagnitudeTerm(COUNT, 1),), accumulation="native_float32_repeated_add")
        self.assertIsNone(bad.evaluate({COUNT: 1}))

    def test_other_linear_formulas_and_unsupported_accumulation_shapes(self):
        linear = ModifierMagnitude(.18, (MagnitudeTerm(COUNT, .02),))
        self.assertEqual(linear.evaluate({COUNT: 3}), .24)
        data = {"key": "test", "bucket": "amplification", "elements": ["电磁"], "recipient": "self",
                "trigger": "explicit_event", "strength": {"base": .18, "terms": [{"input": COUNT, "coefficient": .02}]}}
        for accumulation, cap in (("unsupported", False), ("native_float32_repeated_add", True)):
            data["strength"]["accumulation"] = accumulation
            if cap:
                data["strength"]["terms"][0]["cap"] = .2
            with self.assertRaisesRegex(ValueError, "accumulation|uncapped"):
                bind_damage_modifier(data, skill_id="test", skills={}, rank=None)

    def test_damage_consumer_requires_marker_input_and_filters_electric(self):
        spec = self.spec()
        state = TimedDamageState(("actor",))
        state.apply(spec, source_actor="actor", enemy="target", now=0, event=spec.trigger, inputs={})
        panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        hit = DamageHit("actor", "target", "电磁", 1)
        result = state.resolve_hit(panel, hit, now=0, inputs={"source.battle_lightning_hits": 10})
        self.assertIsNone(result.expected)
        self.assertIn(spec.key, result.unknown)
        known = state.resolve_hit(panel, hit, now=0, inputs={COUNT: 10})
        self.assertEqual(known.buckets["amplification"], .3800000548362732)
        self.assertAlmostEqual(known.non_crit, 138.00000548362732)
        physical = state.resolve_hit(panel, DamageHit("actor", "target", "物理", 1), now=0)
        self.assertEqual(physical.non_crit, 100)
