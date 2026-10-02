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
        """语义表必须覆盖全部效果枚举，不得遗漏、不得多余。"""
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

    def test_character_pools_keep_verified_caps_and_scopes(self):
        expected = {
            EffectType.STACK_MOLTEN: (EffectOwner.SELF, 4),
            EffectType.STACK_IRON_OATH: (EffectOwner.SELF, 5),
            EffectType.STACK_COMBO: (EffectOwner.TEAM, 4),
            EffectType.STACK_MORALE: (EffectOwner.ACTOR, 3),
            EffectType.STACK_WHIRLPOOL: (EffectOwner.FIELD, 2),
            EffectType.STACK_QINGTING_SWORD: (EffectOwner.FIELD, 9),
            EffectType.STACK_SIGN: (EffectOwner.SELF, 8),
            EffectType.STACK_HUNTING_ARROW: (EffectOwner.SELF, 4),
        }
        for effect, (owner, cap) in expected.items():
            with self.subTest(effect=effect):
                sem = EFFECT_SEMANTICS[effect]
                self.assertIs(sem.kind, EffectKind.POOL)
                self.assertIs(sem.owner, owner)
                self.assertEqual(sem.cap, cap)

    def test_generic_operator_buffs_use_actor_scope(self):
        for effect in (
            EffectType.BUFF_ATTACK_UP,
            EffectType.BUFF_CRIT_RATE_UP,
            EffectType.BUFF_CRIT_DMG_UP,
            EffectType.BUFF_SHIELD,
            EffectType.BUFF_HEAL,
            EffectType.BUFF_DAMAGE_UP,
            EffectType.BUFF_COLD_UP,
            EffectType.BUFF_BURN_UP,
            EffectType.BUFF_ELECTROMAGNETIC_UP,
            EffectType.BUFF_NATURAL_UP,
            EffectType.BUFF_SPELL_UP,
            EffectType.BUFF_PROTECTION,
        ):
            with self.subTest(effect=effect):
                self.assertIs(EFFECT_SEMANTICS[effect].owner, EffectOwner.ACTOR)

    def test_category_aliases_are_predicates_not_runtime_states(self):
        for effect in (
            EffectType.STATUS_SHRED,
            EffectType.STATUS_SPELL_INFLICT,
            EffectType.STATUS_SPELL_ANOMALY,
        ):
            with self.subTest(effect=effect):
                self.assertIs(EFFECT_SEMANTICS[effect].kind, EffectKind.PREDICATE)

    def test_entities_operations_and_unresolved_are_explicit(self):
        self.assertIs(EFFECT_SEMANTICS[EffectType.STACK_BLOOD_WING].kind, EffectKind.ENTITY)
        self.assertIs(EFFECT_SEMANTICS[EffectType.MECH_BOMB].kind, EffectKind.ENTITY)
        self.assertIs(EFFECT_SEMANTICS[EffectType.MECH_SUPPORT_CRYSTAL].kind, EffectKind.ENTITY)
        self.assertIs(EFFECT_SEMANTICS[EffectType.CLEAR_ATTACH].kind, EffectKind.OPERATION)
        self.assertIs(EFFECT_SEMANTICS[EffectType.PLACE_THUNDER_SPEAR].kind, EffectKind.OPERATION)
        self.assertIs(EFFECT_SEMANTICS[EffectType.STACK_SEED].kind, EffectKind.UNRESOLVED)
        self.assertIs(EFFECT_SEMANTICS[EffectType.STACK_CHARGE].kind, EffectKind.UNRESOLVED)
        self.assertIs(EFFECT_SEMANTICS[EffectType.MECH_RADAR].kind, EffectKind.UNRESOLVED)
        self.assertIs(EFFECT_SEMANTICS[EffectType.MECH_TURRET].kind, EffectKind.UNRESOLVED)

    def test_consumed_shred_event_and_replacement_states(self):
        consumed = EFFECT_SEMANTICS[EffectType.EVENT_SHRED_CONSUMED]
        self.assertIs(consumed.kind, EffectKind.EVENT)
        self.assertIs(consumed.owner, EffectOwner.ENEMY)

        for effect in (
            EffectType.STATUS_MIFU_ZHUIXING_READY,
            EffectType.STATUS_MIFU_KAITIAN_READY,
            EffectType.STATUS_CAMILLE_PURSUIT_READY,
        ):
            with self.subTest(effect=effect):
                sem = EFFECT_SEMANTICS[effect]
                self.assertIs(sem.kind, EffectKind.STATE)
                self.assertIs(sem.owner, EffectOwner.SELF)
                self.assertIs(sem.consume, ConsumePolicy.ON_USE)

        blood_surge = EFFECT_SEMANTICS[EffectType.STACK_CAMILLE_BLOOD_SURGE]
        self.assertIs(blood_surge.kind, EffectKind.POOL)
        self.assertIs(blood_surge.owner, EffectOwner.SELF)
        self.assertEqual(blood_surge.cap, 5)

    def test_zhuang_fangyi_ultimate_states_are_private(self):
        stance = EFFECT_SEMANTICS[EffectType.STATUS_TIANLI_HEZHEN]
        first_skill = EFFECT_SEMANTICS[EffectType.STATUS_TIANLI_FIRST_SKILL_READY]
        self.assertIs(stance.kind, EffectKind.STATE)
        self.assertIs(stance.owner, EffectOwner.SELF)
        self.assertIs(first_skill.kind, EffectKind.STATE)
        self.assertIs(first_skill.owner, EffectOwner.SELF)
        self.assertIs(first_skill.consume, ConsumePolicy.ON_USE)

    def test_thunder_spears_are_field_entities(self):
        for effect in (
            EffectType.MECH_THUNDER_SPEAR,
            EffectType.MECH_STRONG_THUNDER_SPEAR,
        ):
            with self.subTest(effect=effect):
                sem = EFFECT_SEMANTICS[effect]
                self.assertIs(sem.kind, EffectKind.ENTITY)
                self.assertIs(sem.owner, EffectOwner.FIELD)

    def test_unresolved_effects_are_not_term_inference_targets(self):
        from src.data.effects import EFFECT_TERMS

        unresolved = {
            effect
            for effect, sem in EFFECT_SEMANTICS.items()
            if sem.kind is EffectKind.UNRESOLVED
        }
        inferred = set(EFFECT_TERMS.values())
        self.assertFalse(unresolved & inferred)

    def test_only_pools_have_caps(self):
        for effect, sem in EFFECT_SEMANTICS.items():
            with self.subTest(effect=effect):
                if sem.kind is EffectKind.POOL:
                    self.assertIsNotNone(sem.cap, f"{effect} 是池但没有明确上限")
                else:
                    self.assertIsNone(sem.cap, f"{effect} 非池却有上限")


if __name__ == "__main__":
    unittest.main()
