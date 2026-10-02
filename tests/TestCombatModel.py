"""战斗快照状态容器测试。"""

import unittest

from src.data.combat_model import (
    PHYSICAL_RULES,
    SPELL_BURST_RULE,
    SPELL_REACTION_BY_ELEMENT,
    EnemyCombatState,
    TeamCombatState,
)
from src.data.effects import EffectType


class TestEnemyCombatState(unittest.TestCase):
    def test_first_infliction_sets_one_stack_without_event(self):
        enemy = EnemyCombatState()
        self.assertIsNone(enemy.apply_infliction(EffectType.ATTACH_NATURAL))
        self.assertIs(enemy.infliction_element, EffectType.ATTACH_NATURAL)
        self.assertEqual(enemy.infliction_stacks, 1)

    def test_same_element_stacks_and_bursts_with_cap(self):
        enemy = EnemyCombatState()
        enemy.apply_infliction(EffectType.ATTACH_NATURAL)
        for expected in (2, 3, 4):
            self.assertIs(enemy.apply_infliction(EffectType.ATTACH_NATURAL), EffectType.STATUS_SPELL_BURST)
            self.assertEqual(enemy.infliction_stacks, expected)
        self.assertIs(enemy.apply_infliction(EffectType.ATTACH_NATURAL), EffectType.STATUS_SPELL_BURST)
        self.assertEqual(enemy.infliction_stacks, 4)

    def test_cross_element_reacts_and_clears_all(self):
        enemy = EnemyCombatState()
        for _ in range(3):
            enemy.apply_infliction(EffectType.ATTACH_COLD)
        self.assertIs(enemy.apply_infliction(EffectType.ATTACH_BURN), EffectType.STATUS_BURNING)
        self.assertIsNone(enemy.infliction_element)
        self.assertEqual(enemy.infliction_stacks, 0)

    def test_each_cross_element_maps_to_its_reaction(self):
        mapping = {
            EffectType.ATTACH_BURN: EffectType.STATUS_BURNING,
            EffectType.ATTACH_ELECTROMAGNETIC: EffectType.STATUS_CONDUCTING,
            EffectType.ATTACH_COLD: EffectType.STATUS_FROZEN,
            EffectType.ATTACH_NATURAL: EffectType.STATUS_CORROSION,
        }
        elements = tuple(mapping)
        for new_element, reaction in mapping.items():
            with self.subTest(new_element=new_element):
                enemy = EnemyCombatState()
                other = next(element for element in elements if element is not new_element)
                enemy.apply_infliction(other)
                self.assertIs(enemy.apply_infliction(new_element), reaction)

    def test_non_attach_element_rejected(self):
        with self.assertRaises(ValueError):
            EnemyCombatState().apply_infliction(EffectType.STATUS_FROZEN)

    def test_shred_consume_keeps_event_on_zero_consume(self):
        enemy = EnemyCombatState()
        for _ in range(6):
            enemy.add_shred()
        self.assertEqual(enemy.shred_stacks, 4)
        self.assertEqual(enemy.consume_shred(), 4)
        self.assertEqual(enemy.shred_stacks, 0)
        self.assertEqual(enemy.transient_events[EffectType.EVENT_SHRED_CONSUMED], 4)
        self.assertEqual(enemy.consume_shred(), 0)
        self.assertEqual(enemy.transient_events[EffectType.EVENT_SHRED_CONSUMED], 4)

    def test_shred_consume_accumulates_within_action(self):
        enemy = EnemyCombatState(shred_stacks=2)
        self.assertEqual(enemy.consume_shred(), 2)
        enemy.add_shred(3)
        self.assertEqual(enemy.consume_shred(), 3)
        self.assertEqual(enemy.transient_events[EffectType.EVENT_SHRED_CONSUMED], 5)

    def test_consumed_shred_event_is_transient(self):
        enemy = EnemyCombatState(shred_stacks=3)
        enemy.consume_shred()
        self.assertEqual(enemy.transient_events, {EffectType.EVENT_SHRED_CONSUMED: 3})
        enemy.clear_transient_events()
        self.assertEqual(enemy.transient_events, {})


class TestTeamCombatState(unittest.TestCase):
    def test_link_add_caps_at_four(self):
        team = TeamCombatState()
        for _ in range(7):
            team.add_link()
        self.assertEqual(team.link_stacks, 4)

    def test_link_bonus_official_table(self):
        team = TeamCombatState()
        self.assertEqual(team.link_bonus(is_ult=False), 0.0)
        self.assertEqual(team.link_bonus(is_ult=True), 0.0)
        for stacks, skill, ult in ((1, 0.30, 0.20), (2, 0.45, 0.30), (3, 0.60, 0.40), (4, 0.75, 0.50)):
            team.link_stacks = stacks
            with self.subTest(stacks=stacks):
                self.assertAlmostEqual(team.link_bonus(is_ult=False), skill)
                self.assertAlmostEqual(team.link_bonus(is_ult=True), ult)

    def test_link_consume_clears(self):
        team = TeamCombatState(link_stacks=3)
        self.assertEqual(team.consume_link(), 3)
        self.assertEqual(team.link_stacks, 0)


class TestReactionRules(unittest.TestCase):
    def test_spell_rules_cover_all_four_elements(self):
        self.assertEqual(
            set(SPELL_REACTION_BY_ELEMENT),
            {
                EffectType.ATTACH_BURN,
                EffectType.ATTACH_ELECTROMAGNETIC,
                EffectType.ATTACH_COLD,
                EffectType.ATTACH_NATURAL,
            },
        )
        for element, rule in SPELL_REACTION_BY_ELEMENT.items():
            with self.subTest(element=element):
                self.assertTrue(rule.consumes_all)
                self.assertIsNotNone(rule.applies)

    def test_burst_is_same_element_fixed_multiplier(self):
        self.assertTrue(SPELL_BURST_RULE.same_element_only)
        self.assertFalse(SPELL_BURST_RULE.consumes_all)
        self.assertEqual(SPELL_BURST_RULE.multiplier, 160.0)
        self.assertFalse(SPELL_BURST_RULE.scales_with_stacks)

    def test_all_spell_reactions_scale_with_stacks(self):
        for rule in SPELL_REACTION_BY_ELEMENT.values():
            self.assertEqual(rule.multiplier, 80.0)
            self.assertTrue(rule.scales_with_stacks)

    def test_physical_chain_official_multipliers(self):
        by_name = {rule.name: rule for rule in PHYSICAL_RULES}
        self.assertEqual(by_name["碎冰"].multiplier, 120.0)
        self.assertEqual(by_name["击飞"].multiplier, 120.0)
        self.assertEqual(by_name["倒地"].multiplier, 120.0)
        self.assertEqual(by_name["猛击"].multiplier, 150.0)
        self.assertTrue(by_name["猛击"].consumes_all)
        self.assertTrue(by_name["猛击"].scales_with_stacks)
        self.assertEqual(by_name["碎甲"].multiplier, 50.0)
        self.assertTrue(by_name["碎甲"].consumes_all)
        self.assertTrue(by_name["碎甲"].scales_with_stacks)
        self.assertFalse(by_name["击飞"].consumes_all)
        self.assertFalse(by_name["倒地"].consumes_all)
        self.assertIs(by_name["碎冰"].requires, EffectType.STATUS_FROZEN)
        self.assertIsNone(by_name["碎冰"].applies)


if __name__ == "__main__":
    unittest.main()
