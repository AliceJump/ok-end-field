"""战斗快照状态容器测试。"""

import unittest

from src.data.combat_model import EnemyCombatState, TeamCombatState
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
            self.assertIs(
                enemy.apply_infliction(EffectType.ATTACH_NATURAL),
                EffectType.STATUS_SPELL_BURST,
            )
            self.assertEqual(enemy.infliction_stacks, expected)
        # 第 5 次施加仍封顶 4 层，继续触发爆发但不再涨层
        self.assertIs(
            enemy.apply_infliction(EffectType.ATTACH_NATURAL),
            EffectType.STATUS_SPELL_BURST,
        )
        self.assertEqual(enemy.infliction_stacks, 4)

    def test_cross_element_reacts_by_new_element_and_clears_all(self):
        enemy = EnemyCombatState()
        enemy.apply_infliction(EffectType.ATTACH_COLD)
        enemy.apply_infliction(EffectType.ATTACH_COLD)
        enemy.apply_infliction(EffectType.ATTACH_COLD)  # 3 层寒冷
        # 施加灼热 → 燃烧（类型由新元素决定，强度由旧层数决定——旧层数由调用方取）
        self.assertIs(
            enemy.apply_infliction(EffectType.ATTACH_BURN),
            EffectType.STATUS_BURNING,
        )
        self.assertIsNone(enemy.infliction_element)
        self.assertEqual(enemy.infliction_stacks, 0)

    def test_each_cross_element_maps_to_its_reaction(self):
        mapping = {
            EffectType.ATTACH_BURN: EffectType.STATUS_BURNING,
            EffectType.ATTACH_ELECTROMAGNETIC: EffectType.STATUS_CONDUCTING,
            EffectType.ATTACH_COLD: EffectType.STATUS_FROZEN,
            EffectType.ATTACH_NATURAL: EffectType.STATUS_CORROSION,
        }
        for new_element, reaction in mapping.items():
            with self.subTest(new_element=new_element):
                enemy = EnemyCombatState()
                # 先挂一个异元素附着（首次施加无反应），再施加 new_element 触发交叉反应
                other = next(e for e in {
                    EffectType.ATTACH_BURN,
                    EffectType.ATTACH_ELECTROMAGNETIC,
                    EffectType.ATTACH_COLD,
                    EffectType.ATTACH_NATURAL,
                } - {new_element})
                enemy.apply_infliction(other)
                self.assertIs(enemy.apply_infliction(new_element), reaction)

    def test_non_attach_element_rejected(self):
        enemy = EnemyCombatState()
        with self.assertRaises(ValueError):
            enemy.apply_infliction(EffectType.STATUS_FROZEN)

    def test_shred_add_caps_and_consume_returns_all(self):
        enemy = EnemyCombatState()
        for _ in range(6):
            enemy.add_shred()
        self.assertEqual(enemy.shred_stacks, 4)
        self.assertEqual(enemy.consume_shred(), 4)
        self.assertEqual(enemy.shred_stacks, 0)
        self.assertEqual(enemy.consume_shred(), 0)


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
        team = TeamCombatState()
        team.add_link(3)
        self.assertEqual(team.consume_link(), 3)
        self.assertEqual(team.link_stacks, 0)


if __name__ == "__main__":
    unittest.main()
