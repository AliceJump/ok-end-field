"""效果语义元数据表完整性测试。"""

import unittest

from src.data.effect_semantics import (
    EFFECT_SEMANTICS,
    ConsumePolicy,
    EffectKind,
    EffectOwner,
)
from src.data.effects import EffectType


class TestEffectSemantics(unittest.TestCase):
    def test_covers_every_effect_type(self):
        """语义表必须覆盖全部 87 个效果枚举，不得遗漏、不得多余。"""
        self.assertEqual(set(EFFECT_SEMANTICS), set(EffectType))

    def test_link_is_team_pool_with_cap4_consumed_on_use(self):
        sem = EFFECT_SEMANTICS[EffectType.STACK_COMBO]
        self.assertIs(sem.kind, EffectKind.POOL)
        self.assertIs(sem.owner, EffectOwner.TEAM)
        self.assertEqual(sem.cap, 4)
        self.assertIs(sem.consume, ConsumePolicy.ON_USE)

    def test_inflictions_are_enemy_pools_capped_at_4(self):
        for element in (
            EffectType.ATTACH_COLD,
            EffectType.ATTACH_BURN,
            EffectType.ATTACH_ELECTROMAGNETIC,
            EffectType.ATTACH_NATURAL,
        ):
            with self.subTest(element=element):
                sem = EFFECT_SEMANTICS[element]
                self.assertIs(sem.kind, EffectKind.POOL)
                self.assertIs(sem.owner, EffectOwner.ENEMY)
                self.assertEqual(sem.cap, 4)

    def test_shatter_burst_are_events(self):
        self.assertIs(EFFECT_SEMANTICS[EffectType.STATUS_BROKEN].kind, EffectKind.EVENT)
        self.assertIs(EFFECT_SEMANTICS[EffectType.STATUS_SPELL_BURST].kind, EffectKind.EVENT)
        self.assertIs(EFFECT_SEMANTICS[EffectType.STATUS_HEAVY_STRIKE].kind, EffectKind.EVENT)

    def test_character_pools_keep_official_caps(self):
        self.assertEqual(EFFECT_SEMANTICS[EffectType.STACK_SIGN].cap, 8)
        self.assertEqual(EFFECT_SEMANTICS[EffectType.STACK_HUNTING_ARROW].cap, 4)
        self.assertEqual(EFFECT_SEMANTICS[EffectType.STACK_QINGTING_SWORD].cap, 3)

    # 上限未实测的池（文案无数字，待补 rank_stats/实测后移入 EFFECT_SEMANTICS）
    POOLS_WITH_UNKNOWN_CAP = {
        EffectType.STACK_IRON_OATH,
        EffectType.STACK_BLOOD_WING,
        EffectType.STACK_MORALE,
        EffectType.STACK_SEED,
        EffectType.STACK_TRACE,
        EffectType.STACK_CHARGE,
    }

    def test_pool_always_has_cap_event_never_has_cap(self):
        for effect, sem in EFFECT_SEMANTICS.items():
            with self.subTest(effect=effect):
                if sem.kind is EffectKind.POOL:
                    if effect in self.POOLS_WITH_UNKNOWN_CAP:
                        self.assertIsNone(sem.cap, f"{effect} 已实测出上限，请从 POOLS_WITH_UNKNOWN_CAP 移除")
                    else:
                        self.assertIsNotNone(sem.cap, f"{effect} 是池但无上限")
                else:
                    self.assertIsNone(sem.cap, f"{effect} 非池却有上限")


if __name__ == "__main__":
    unittest.main()
