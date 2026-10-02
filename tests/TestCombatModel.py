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
    def test_infliction_expires_at_twenty_seconds(self):
        enemy = EnemyCombatState(states={EffectType.STATUS_BURNING: 20.0})
        enemy.apply_infliction(EffectType.ATTACH_COLD)
        enemy.tick(19.9)
        self.assertIs(enemy.infliction_element, EffectType.ATTACH_COLD)
        self.assertIn(EffectType.STATUS_BURNING, enemy.states)
        enemy.tick(0.1)
        self.assertIsNone(enemy.infliction_element)
        self.assertEqual(enemy.infliction_stacks, 0)
        self.assertEqual(enemy.infliction_time_left, 0)
        self.assertEqual(enemy.states, {})
        self.assertIsNone(enemy.apply_infliction(EffectType.ATTACH_BURN))

    def test_same_element_refreshes_timer_and_cross_element_clears_it(self):
        enemy = EnemyCombatState()
        enemy.apply_infliction(EffectType.ATTACH_COLD)
        enemy.tick(19)
        enemy.apply_infliction(EffectType.ATTACH_COLD)
        self.assertEqual(enemy.infliction_time_left, 20)
        enemy.tick(19)
        self.assertEqual(enemy.infliction_stacks, 2)
        enemy.apply_infliction(EffectType.ATTACH_BURN)
        self.assertEqual(enemy.infliction_time_left, 0)

    def test_tick_expires_timed_states_without_affecting_shred(self):
        enemy = EnemyCombatState(shred_stacks=3, states={EffectType.STATUS_FROZEN: 2, EffectType.STATUS_BURNING: 4})
        enemy.tick(2)
        self.assertEqual(enemy.states, {EffectType.STATUS_BURNING: 2})
        self.assertEqual(enemy.shred_stacks, 3)

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
