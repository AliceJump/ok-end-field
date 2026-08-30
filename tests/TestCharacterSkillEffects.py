"""角色技能效果 ID 一致性测试。"""

import json
import unittest
from pathlib import Path

from src.data.character_skills import load_all_characters
from src.data.effects import EffectType, match_effect_terms


_ROOT = Path(__file__).resolve().parent.parent
_CHARACTER_SKILLS_DIR = _ROOT / "assets" / "data" / "character_skills"
_VALID_TARGETS = {"enemy", "ally", "self", "field"}


class TestCharacterSkillEffects(unittest.TestCase):
    def test_all_character_skill_files_load(self):
        characters = load_all_characters()
        json_files = list(_CHARACTER_SKILLS_DIR.glob("*.json"))

        self.assertEqual(len(characters), len(json_files))
        self.assertEqual(len(characters), len(set(characters)))

    def test_all_effect_ids_and_targets_are_valid(self):
        valid_effect_ids = {effect.value for effect in EffectType}

        for json_file in _CHARACTER_SKILLS_DIR.glob("*.json"):
            with self.subTest(json_file=json_file.name):
                data = json.loads(json_file.read_text(encoding="utf-8"))
                for skill in data.get("skills") or []:
                    effect_groups = [skill.get("effects") or []]
                    enhancement = skill.get("enhancement") or {}
                    effect_groups.append(enhancement.get("effects") or [])
                    trigger_condition = enhancement.get("trigger_condition") or {}
                    if isinstance(trigger_condition, dict):
                        effect_groups.append(trigger_condition.get("effects") or [])

                    for effects in effect_groups:
                        for effect in effects:
                            if isinstance(effect, str):
                                effect_id = effect
                            else:
                                effect_id = effect["effect_id"]
                                self.assertIn(effect.get("target", "enemy"), _VALID_TARGETS)
                                count = effect.get("count", 1)
                                self.assertTrue(
                                    count is None or isinstance(count, int),
                                    f"{json_file.name}/{skill.get('name')} count 必须为整数或 null",
                                )
                            self.assertIn(
                                effect_id,
                                valid_effect_ids,
                                f"{json_file.name}/{skill.get('name')} 使用了未知效果 ID {effect_id}",
                            )

    def test_normal_attack_heavy_strike_is_not_treated_as_lift(self):
        self.assertEqual(match_effect_terms("重击会造成18点失衡"), [("失衡", EffectType.STATUS_STAGGER)])
        self.assertEqual(match_effect_terms("造成击飞"), [("击飞", EffectType.STATUS_HEAVY_HIT)])
        self.assertEqual(match_effect_terms("造成猛击"), [("猛击", EffectType.STATUS_HEAVY_STRIKE)])

    def test_physical_review_fixes(self):
        characters = load_all_characters()

        dapan = characters["da_pan"]
        dapan_link = next(skill for skill in dapan.skills if skill.skill_id == "dapan_link")
        self.assertEqual([effect.effect_id for effect in dapan_link.effects], [EffectType.STATUS_HEAVY_STRIKE])

        lifeng = characters["li_feng"]
        lifeng_skill = next(skill for skill in lifeng.skills if skill.skill_id == "lifeng_skill")
        self.assertEqual([effect.effect_id for effect in lifeng_skill.effects], [EffectType.STATUS_KNOCKDOWN])
        self.assertEqual(
            [effect.effect_id for effect in lifeng_skill.enhancement.effects],
            [EffectType.VULN_PHYSICAL],
        )
        lifeng_ultimate = next(skill for skill in lifeng.skills if skill.skill_id == "lifeng_ultimate")
        self.assertEqual(
            [effect.effect_id for effect in lifeng_ultimate.enhancement.effects],
            [EffectType.TRIGGER_ADDITIONAL],
        )

        azrila = characters["yu_jin"]
        azrila_skill = next(skill for skill in azrila.skills if skill.skill_id == "azrila_skill")
        self.assertEqual(azrila_skill.enhancement.effects, [])

        wulfa = characters["luo_qian"]
        wulfa_link = next(skill for skill in wulfa.skills if skill.skill_id == "wulfa_link")
        self.assertEqual(
            [effect.effect_id for effect in wulfa_link.enhancement.effects],
            [EffectType.BUFF_CRIT_RATE_UP, EffectType.BUFF_CRIT_DMG_UP],
        )
        wulfa_ultimate = next(skill for skill in wulfa.skills if skill.skill_id == "wulfa_ultimate")
        self.assertFalse(wulfa_ultimate.has_enhancement)
        self.assertIsNone(wulfa_ultimate.enhancement)

    def test_spell_reaction_review_fixes(self):
        characters = load_all_characters()

        yvonne = characters["yi_feng"]
        yvonne_skill = next(skill for skill in yvonne.skills if skill.skill_id == "yvonne_skill")
        self.assertEqual(
            [effect.effect_id for effect in yvonne_skill.enhancement.effects],
            [EffectType.CLEAR_ATTACH, EffectType.STATUS_FROZEN],
        )
        yvonne_ultimate = next(skill for skill in yvonne.skills if skill.skill_id == "yvonne_ultimate")
        self.assertEqual(
            [effect.effect_id for effect in yvonne_ultimate.effects],
            [EffectType.BUFF_CRIT_DMG_UP, EffectType.BUFF_CRIT_RATE_UP],
        )
        self.assertEqual(yvonne_ultimate.enhancement.effects[1].count, -1)

        laevat = characters["lai_wan_ting"]
        laevat_skill = next(skill for skill in laevat.skills if skill.skill_id == "laevat_skill")
        self.assertEqual(laevat_skill.enhancement.effects[0].effect_id, EffectType.STACK_MOLTEN)
        self.assertEqual(laevat_skill.enhancement.effects[0].count, -4)
        laevat_link = next(skill for skill in laevat.skills if skill.skill_id == "laevat_link")
        self.assertEqual([effect.effect_id for effect in laevat_link.effects], [EffectType.STACK_MOLTEN])

        zhuangfy = characters["zhuang_fang_yi"]
        zhuangfy_skill = next(skill for skill in zhuangfy.skills if skill.skill_id == "zhuangfy_skill")
        self.assertEqual(zhuangfy_skill.enhancement.effects, [])

        jue = characters["jue"]
        jue_skill = next(skill for skill in jue.skills if skill.skill_id == "lizhiyan_skill")
        self.assertFalse(jue_skill.has_enhancement)
        jue_link = next(skill for skill in jue.skills if skill.skill_id == "lizhiyan_link")
        self.assertEqual(
            [effect.effect_id for effect in jue_link.effects],
            [
                EffectType.STATUS_SPELL_INFLICT,
                EffectType.VULN_NATURAL,
                EffectType.VULN_COLD,
                EffectType.STATUS_CONFINEMENT,
            ],
        )

        ardelia = characters["ai_er_dai_la"]
        ardelia_skill = next(skill for skill in ardelia.skills if skill.skill_id == "ardelia_skill")
        self.assertEqual(ardelia_skill.effects, [])
        self.assertEqual(ardelia_skill.enhancement.effects[0].count, -1)

        aglina = characters["jie_er_pei_ta"]
        aglina_link = next(skill for skill in aglina.skills if skill.skill_id == "aglina_link")
        self.assertEqual([effect.effect_id for effect in aglina_link.effects], [EffectType.STATUS_HEAVY_HIT])

    def test_remaining_character_review_fixes(self):
        characters = load_all_characters()

        antal = characters["an_ta_er"]
        antal_skill = next(skill for skill in antal.skills if skill.skill_id == "antal_skill")
        self.assertEqual(
            [effect.effect_id for effect in antal_skill.effects],
            [EffectType.STATUS_FOCUS, EffectType.VULN_ELECTROMAGNETIC, EffectType.VULN_BURN],
        )
        antal_link = next(skill for skill in antal.skills if skill.skill_id == "antal_link")
        self.assertEqual([effect.effect_id for effect in antal_link.effects], [EffectType.TRIGGER_REPEAT_EFFECT])

        ikut = characters["hu_guang"]
        ikut_skill = next(skill for skill in ikut.skills if skill.skill_id == "ikut_skill")
        self.assertEqual(ikut_skill.enhancement.effects[0].count, -1)
        ikut_ultimate = next(skill for skill in ikut.skills if skill.skill_id == "ikut_ultimate")
        self.assertEqual(ikut_ultimate.enhancement.effects[0].count, -1)

        bounda = characters["ying_shi"]
        bounda_skill = next(skill for skill in bounda.skills if skill.skill_id == "bounda_skill")
        self.assertEqual(bounda_skill.effects[0].effect_id, EffectType.MECH_BOMB)
        bounda_link = next(skill for skill in bounda.skills if skill.skill_id == "bounda_link")
        self.assertEqual([effect.effect_id for effect in bounda_link.effects], [EffectType.STATUS_SPELL_INFLICT])

        deepfin = characters["a_lie_shi"]
        deepfin_skill = next(skill for skill in deepfin.skills if skill.skill_id == "deepfin_skill")
        self.assertEqual(deepfin_skill.effects, [])
        self.assertEqual(deepfin_skill.enhancement.effects[0].count, -1)

        meurs = characters["ka_qi_er"]
        meurs_skill = next(skill for skill in meurs.skills if skill.skill_id == "meurs_skill")
        self.assertIn(EffectType.BUFF_PROTECTION, [effect.effect_id for effect in meurs_skill.effects])
        meurs_ultimate = next(skill for skill in meurs.skills if skill.skill_id == "meurs_ultimate")
        self.assertEqual(meurs_ultimate.effects[0].effect_id, EffectType.DEBUFF_WEAKEN)

        seraph = characters["sai_xi"]
        seraph_skill = next(skill for skill in seraph.skills if skill.skill_id == "seraph_skill")
        self.assertEqual(seraph_skill.enhancement.effects[0].effect_id, EffectType.MECH_SUPPORT_CRYSTAL)
        seraph_ultimate = next(skill for skill in seraph.skills if skill.skill_id == "seraph_ultimate")
        self.assertEqual(
            [effect.effect_id for effect in seraph_ultimate.effects],
            [EffectType.BUFF_COLD_UP, EffectType.BUFF_NATURAL_UP],
        )

        lastrite = characters["bie_li"]
        lastrite_link = next(skill for skill in lastrite.skills if skill.skill_id == "lastrite_link")
        self.assertEqual(lastrite_link.effects[0].effect_id, EffectType.CLEAR_COLD)
        self.assertEqual(lastrite_link.effects[0].count, -1)

        liino = characters["li_nuo"]
        liino_skill = next(skill for skill in liino.skills if skill.skill_id == "liino_skill")
        self.assertEqual(liino_skill.effects[0].effect_id, EffectType.STATUS_SINGING)
        liino_ultimate = next(skill for skill in liino.skills if skill.skill_id == "liino_ultimate")
        self.assertIn(EffectType.STATUS_HIGH_SINGING, [effect.effect_id for effect in liino_ultimate.effects])


if __name__ == "__main__":
    unittest.main()
