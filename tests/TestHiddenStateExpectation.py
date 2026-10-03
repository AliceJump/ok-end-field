from __future__ import annotations

import unittest

from src.data.character_mechanics import load_character_mechanics
from src.data.hidden_state_expectation import (
    DiscreteBelief,
    HiddenStateExpectation,
    load_damage_envelopes,
)


class TestDiscreteBelief(unittest.TestCase):
    def test_maximum_entropy_stack_has_distinct_full_probability_and_expected_fraction(self):
        belief = DiscreteBelief.maximum_entropy(4)
        self.assertAlmostEqual(belief.expected, 2.0)
        self.assertAlmostEqual(belief.full_probability, 0.2)
        self.assertAlmostEqual(belief.expected / belief.maximum, 0.5)

    def test_uncertain_gain_propagates_probability_mass(self):
        belief = DiscreteBelief.certain(0, 4).add(1, 0.75)
        self.assertAlmostEqual(belief.probabilities[0], 0.25)
        self.assertAlmostEqual(belief.probabilities[1], 0.75)
        self.assertAlmostEqual(belief.expected, 0.75)

    def test_uniform_range_gain_does_not_assume_maximum(self):
        belief = DiscreteBelief.certain(0, 9).add_uniform(1, 3)
        self.assertAlmostEqual(belief.expected, 2.0)
        self.assertAlmostEqual(belief.probabilities[1], 1 / 3)
        self.assertAlmostEqual(belief.probabilities[2], 1 / 3)
        self.assertAlmostEqual(belief.probabilities[3], 1 / 3)


class TestHiddenDamageExpectation(unittest.TestCase):
    def setUp(self):
        self.mechanics = load_character_mechanics()
        self.envelopes = load_damage_envelopes()

    def _battle_transition(self, actor):
        return next(item for item in self.mechanics[actor].transitions if item.action == "battle")

    def test_typhoeus_with_natural_provider_uses_expected_stack_fraction_not_full(self):
        hidden = HiddenStateExpectation(
            ["提弗洛斯", "洁尔佩塔", "?", "?"],
            self.mechanics,
        )
        estimate = hidden.estimate_damage(
            "提弗洛斯",
            "战技",
            self.envelopes[("提弗洛斯", "战技")],
            self._battle_transition("提弗洛斯"),
        )
        self.assertAlmostEqual(estimate.full_probability, 0.2)
        self.assertAlmostEqual(estimate.expected_fraction, 0.5)
        self.assertGreater(estimate.expected, estimate.low)
        self.assertLess(estimate.expected, estimate.high)
        self.assertAlmostEqual(estimate.expected, (estimate.low + estimate.high) / 2)

    def test_typhoeus_without_natural_provider_falls_to_low_endpoint(self):
        hidden = HiddenStateExpectation(
            ["提弗洛斯", "佩丽卡", "狼卫", "管理员"],
            self.mechanics,
        )
        estimate = hidden.estimate_damage(
            "提弗洛斯",
            "战技",
            self.envelopes[("提弗洛斯", "战技")],
            self._battle_transition("提弗洛斯"),
        )
        self.assertEqual(estimate.full_probability, 0)
        self.assertEqual(estimate.expected_fraction, 0)
        self.assertAlmostEqual(estimate.expected, estimate.low)

    def test_yvonne_per_attachment_damage_uses_expected_layers(self):
        hidden = HiddenStateExpectation(
            ["伊冯", "洁尔佩塔", "别礼", "余烬"],
            self.mechanics,
        )
        envelope = self.envelopes[("伊冯", "战技")]
        estimate = hidden.estimate_damage(
            "伊冯",
            "战技",
            envelope,
            self._battle_transition("伊冯"),
        )
        self.assertEqual(envelope.stack_resource, "spell_attach")
        self.assertEqual(envelope.stack_maximum, 4)
        self.assertAlmostEqual(estimate.full_probability, 0.2)
        self.assertAlmostEqual(estimate.expected_fraction, 0.5)
        self.assertAlmostEqual(estimate.high, estimate.low * 3)
        self.assertAlmostEqual(estimate.expected, estimate.low * 2)

    def test_yvonne_without_attachment_provider_does_not_invent_stack_damage(self):
        hidden = HiddenStateExpectation(
            ["伊冯", "陈千语", "黎风", "管理员"],
            self.mechanics,
            capabilities={},
        )
        estimate = hidden.estimate_damage(
            "伊冯",
            "战技",
            self.envelopes[("伊冯", "战技")],
            self._battle_transition("伊冯"),
        )
        self.assertEqual(estimate.full_probability, 0)
        self.assertEqual(estimate.expected_fraction, 0)
        self.assertAlmostEqual(estimate.expected, estimate.low)

    def test_zhuang_first_ultimate_battle_is_known_full_not_probabilistic(self):
        hidden = HiddenStateExpectation(
            ["庄方宜", "佩丽卡", "诀", "洛茜"],
            self.mechanics,
        )
        transition = next(item for item in self.mechanics["庄方宜"].transitions if item.phase == "天理合真首次惊霆诀")
        estimate = hidden.estimate_damage(
            "庄方宜",
            "战技",
            self.envelopes[("庄方宜", "战技")],
            transition,
        )
        self.assertEqual(estimate.full_probability, 1)
        self.assertEqual(estimate.expected_fraction, 1)
        self.assertAlmostEqual(estimate.expected, estimate.high)


if __name__ == "__main__":
    unittest.main()
