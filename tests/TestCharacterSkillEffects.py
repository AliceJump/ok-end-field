"""角色技能效果 ID 一致性测试。"""

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from src.data.character_skills import _load_character_from_json, load_all_characters
from src.data.effects import EFFECT_DESCRIPTIONS, EffectType, match_effect_terms
from src.data.skill_types import TriggerEffectGroup

_ROOT = Path(__file__).resolve().parent.parent
_CHARACTER_SKILLS_DIR = _ROOT / "assets" / "data" / "character_skills"
_VALID_TARGETS = {"enemy", "ally", "self", "field"}


class TestCharacterSkillEffects(unittest.TestCase):
    def _load_temp_character(self, temp_dir: Path, skill: dict):
        data = {
            "character_id": "test_character",
            "name": "测试角色",
            "element": "物理",
            "skills": [skill],
        }
        path = temp_dir / "character.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return _load_character_from_json(path).skills[0]

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
                    enhancements = skill.get("enhancements") or []
                    for enhancement in enhancements:
                        effect_groups.append(enhancement.get("effects") or [])
                        trigger_condition = enhancement.get("trigger_condition") or {}
                        if isinstance(trigger_condition, dict):
                            trigger_effects = trigger_condition.get("effects") or []
                            if isinstance(trigger_effects, dict):
                                effect_groups.extend(trigger_effects.get(operator) or [] for operator in ("all", "any"))
                            else:
                                effect_groups.append(trigger_effects)

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

    def test_trigger_condition_effect_schema_is_explicit_and_valid(self):
        valid_effect_ids = {effect.value for effect in EffectType}

        for json_file in _CHARACTER_SKILLS_DIR.glob("*.json"):
            data = json.loads(json_file.read_text(encoding="utf-8"))
            for skill in data.get("skills") or []:
                enhancements = skill.get("enhancements") or []
                for enhancement in enhancements:
                    trigger_condition = enhancement.get("trigger_condition") or {}
                    if not isinstance(trigger_condition, dict):
                        continue
                    condition_effects = trigger_condition.get("effects")
                    if not isinstance(condition_effects, dict):
                        continue
                    with self.subTest(json_file=json_file.name, skill=skill.get("skill_id")):
                        operators = [operator for operator in ("all", "any") if operator in condition_effects]
                        self.assertEqual(len(operators), 1)
                        requires = condition_effects[operators[0]]
                        self.assertIsInstance(requires, list)
                        self.assertTrue(all(effect_id in valid_effect_ids for effect_id in requires))

    def test_normal_attack_heavy_strike_is_not_treated_as_lift(self):
        self.assertEqual(match_effect_terms("重击会造成18点失衡"), [("失衡", EffectType.STATUS_STAGGER)])
        self.assertEqual(match_effect_terms("造成击飞"), [("击飞", EffectType.STATUS_HEAVY_HIT)])
        self.assertEqual(match_effect_terms("造成猛击"), [("猛击", EffectType.STATUS_HEAVY_STRIKE)])

    def test_purge_colloquialism_maps_to_clear_status(self):
        # 「净化」是社区对官方「清除异常状态」（CLEAR_STATUS）的转述
        self.assertEqual(match_effect_terms("净化目标"), [("净化", EffectType.CLEAR_STATUS)])

    def test_shatter_term_maps_to_status_broken(self):
        # STATUS_BROKEN 官方名即「碎冰」（Shatter），枚举名带历史包袱但不可删除
        self.assertEqual(match_effect_terms("碎冰"), [("碎冰", EffectType.STATUS_BROKEN)])
        # 与「碎甲」（STATUS_SHATTER）术语互不混淆
        self.assertEqual(match_effect_terms("碎甲"), [("碎甲", EffectType.STATUS_SHATTER)])
        self.assertIn("碎冰", EFFECT_DESCRIPTIONS[EffectType.STATUS_BROKEN])

    def test_link_is_team_shared_stack_not_character_owned(self):
        # 连击是官方队伍共享机制（Link），术语应命中 STACK_COMBO
        self.assertEqual(match_effect_terms("获得连击"), [("连击", EffectType.STACK_COMBO)])
        self.assertEqual(
            match_effect_terms("若该技能消耗了连击"),
            [("连击", EffectType.STACK_COMBO)],
        )
        self.assertIn("队伍连击层数", EFFECT_DESCRIPTIONS[EffectType.STACK_COMBO])

    def test_has_enhancement_is_derived_from_parsed_branches(self):
        branch = {
            "name": "条件效果",
            "trigger_condition": {"text": "目标处于冻结", "effects": ["STATUS_FROZEN"]},
            "effects": [],
        }
        base_skill = {
            "skill_id": "test_skill",
            "name": "测试技能",
            "skill_type": "战技",
            "element": "物理",
        }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            cases = [
                ({"enhancements": [branch]}, True),
                ({"enhancements": [branch], "has_enhancement": False}, True),
                ({"has_enhancement": True}, False),
                ({"has_enhancement": False}, False),
            ]
            for fields, expected in cases:
                with self.subTest(fields=fields):
                    skill = self._load_temp_character(temp_dir, {**base_skill, **fields})
                    self.assertEqual(skill.has_enhancement, expected)
                    self.assertEqual(bool(skill.enhancements), expected)
                    self.assertEqual(skill.enhancement, skill.enhancements[0] if expected else None)

    def test_trigger_condition_operator_groups_load_and_evaluate(self):
        branches = [
            {
                "name": "全部条件",
                "trigger_condition": {
                    "text": "聚焦目标进入破防",
                    "effects": {"all": ["STATUS_FOCUS", "STATUS_SHRED"]},
                },
                "effects": [],
            },
            {
                "name": "任一条件",
                "trigger_condition": {
                    "text": "目标冻结或失衡",
                    "effects": {"any": ["STATUS_FROZEN", "STATUS_STAGGER"]},
                },
                "effects": [],
            },
            {
                "name": "动态事件",
                "trigger_condition": {
                    "text": "其他干员的连携技造成伤害",
                    "effects": {"all": []},
                },
                "effects": [],
            },
        ]
        skill = {
            "skill_id": "test_skill",
            "name": "测试技能",
            "skill_type": "战技",
            "element": "物理",
            "enhancements": branches,
        }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            loaded = self._load_temp_character(Path(temp_dir_name), skill)

        self.assertEqual(
            [enhancement.trigger_effects for enhancement in loaded.enhancements[:2]],
            [
                [EffectType.STATUS_FOCUS, EffectType.STATUS_SHRED],
                [EffectType.STATUS_FROZEN, EffectType.STATUS_STAGGER],
            ],
        )
        self.assertEqual(
            [enhancement.trigger_effect_groups[0].operator for enhancement in loaded.enhancements[:2]],
            ["all", "any"],
        )
        all_enhancement, any_enhancement, dynamic_enhancement = loaded.enhancements
        self.assertTrue(all_enhancement.is_trigger_satisfied({EffectType.STATUS_FOCUS, EffectType.STATUS_SHRED}))
        self.assertFalse(all_enhancement.is_trigger_satisfied({EffectType.STATUS_FOCUS}))
        self.assertTrue(any_enhancement.is_trigger_satisfied({EffectType.STATUS_FROZEN}))
        self.assertFalse(any_enhancement.is_trigger_satisfied({EffectType.STATUS_FOCUS}))
        self.assertEqual(
            dynamic_enhancement.trigger_effect_groups,
            [TriggerEffectGroup(operator="all", effects=())],
        )
        self.assertTrue(dynamic_enhancement.is_trigger_satisfied(set()))

        typhoeus_link = next(
            skill for skill in load_all_characters()["typhoeus"].skills if skill.skill_id == "typhoeus_link"
        )
        self.assertFalse(typhoeus_link.enhancement.is_trigger_satisfied(set()))
        self.assertFalse(typhoeus_link.enhancement.is_trigger_satisfied({EffectType.STACK_SIGN: 7}))
        self.assertTrue(typhoeus_link.enhancement.is_trigger_satisfied({EffectType.STACK_SIGN: 8}))
        self.assertEqual(typhoeus_link.enhancement.effects[0].count, -8)

        self.assertEqual(
            [effect.effect_id for effect in typhoeus_link.effects],
            [EffectType.DEBUFF_SPEED_DOWN, EffectType.VULN_NATURAL_BURST],
        )
        self.assertTrue(all(effect.value == 1 for effect in typhoeus_link.effects))
        self.assertTrue(all(effect.duration is None for effect in typhoeus_link.effects))
        self.assertTrue(all(effect.target == "enemy" for effect in typhoeus_link.effects))

    def test_requested_trigger_condition_branches(self):
        def load_skill(file_name, skill_id):
            data = json.loads((_CHARACTER_SKILLS_DIR / file_name).read_text(encoding="utf-8"))
            return next(skill for skill in data["skills"] if skill["skill_id"] == skill_id)

        gilberta = load_skill("gilberta.json", "gilberta_ultimate")
        self.assertEqual(
            [branch["trigger_condition"]["effects"] for branch in gilberta["enhancements"]],
            [{"all": ["STATUS_SHRED"]}, {"all": ["STATUS_HEAVY_HIT"]}],
        )
        self.assertEqual(
            [[effect["effect_id"] for effect in branch["effects"]] for branch in gilberta["enhancements"]],
            [["VULN_ALL"], ["STATUS_HEAVY_HIT"]],
        )

        antal = load_skill("antal.json", "antal_link")
        listed_statuses = {
            "STATUS_HEAVY_STRIKE",
            "STATUS_HEAVY_HIT",
            "STATUS_KNOCKDOWN",
            "STATUS_SPELL_INFLICT",
            "STATUS_SHATTER",
        }
        self.assertEqual(len(antal["enhancements"]), len(listed_statuses))
        self.assertEqual(
            {tuple(branch["trigger_condition"]["effects"]["all"]) for branch in antal["enhancements"]},
            {("STATUS_FOCUS", status) for status in listed_statuses},
        )

        tangtang = load_skill("tangtang.json", "tangtang_link")
        self.assertEqual(
            tangtang["enhancements"][0]["trigger_condition"]["effects"],
            {"any": ["ATTACH_COLD", "STATUS_SPELL_BURST"]},
        )

    def test_enhancement_compatibility_view_tracks_enhancements(self):
        skill_fields = {field.name for field in dataclasses.fields(load_all_characters()["xaihi"].skills[1])}
        self.assertIn("enhancements", skill_fields)
        self.assertNotIn("enhancement", skill_fields)
        self.assertNotIn("has_enhancement", skill_fields)

    def test_physical_review_fixes(self):
        characters = load_all_characters()

        da_pan = characters["da_pan"]
        da_pan_link = next(skill for skill in da_pan.skills if skill.skill_id == "da_pan_link")
        self.assertEqual(
            [effect.effect_id for effect in da_pan_link.enhancement.effects], [EffectType.STATUS_HEAVY_STRIKE]
        )

        lifeng = characters["lifeng"]
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

        ember = characters["ember"]
        ember_skill = next(skill for skill in ember.skills if skill.skill_id == "ember_skill")
        self.assertEqual(ember_skill.enhancement.effects, [])

        rossi = characters["rossi"]
        rossi_link = next(skill for skill in rossi.skills if skill.skill_id == "rossi_link")
        self.assertEqual(
            [effect.effect_id for effect in rossi_link.enhancements[0].effects],
            [EffectType.BUFF_CRIT_RATE_UP, EffectType.BUFF_CRIT_DMG_UP],
        )
        self.assertEqual(
            [effect.effect_id for effect in rossi_link.enhancements[1].effects],
            [EffectType.STACK_SHRED],
        )
        rossi_ultimate = next(skill for skill in rossi.skills if skill.skill_id == "rossi_ultimate")
        self.assertFalse(rossi_ultimate.has_enhancement)
        self.assertIsNone(rossi_ultimate.enhancement)

    def test_spell_reaction_review_fixes(self):
        characters = load_all_characters()

        yvonne = characters["yvonne"]
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

        laevatain = characters["laevatain"]
        laevatain_skill = next(skill for skill in laevatain.skills if skill.skill_id == "laevatain_skill")
        self.assertEqual(laevatain_skill.enhancement.effects[0].effect_id, EffectType.STACK_MOLTEN)
        self.assertEqual(laevatain_skill.enhancement.effects[0].count, -4)
        laevatain_link = next(skill for skill in laevatain.skills if skill.skill_id == "laevatain_link")
        self.assertEqual([effect.effect_id for effect in laevatain_link.enhancement.effects], [EffectType.STACK_MOLTEN])

        zhuang_fangyi = characters["zhuang_fangyi"]
        zhuang_fangyi_skill = next(skill for skill in zhuang_fangyi.skills if skill.skill_id == "zhuang_fangyi_skill")
        self.assertEqual(len(zhuang_fangyi_skill.enhancements), 2)
        self.assertEqual(
            [effect.effect_id for effect in zhuang_fangyi_skill.enhancements[0].effects],
            [EffectType.STATUS_CONDUCTING, EffectType.STACK_QINGTING_SWORD],
        )
        self.assertEqual(zhuang_fangyi_skill.enhancements[0].effects[0].count, -1)
        self.assertIsNone(zhuang_fangyi_skill.enhancements[0].effects[1].count)
        self.assertEqual(
            [effect.effect_id for effect in zhuang_fangyi_skill.enhancements[1].effects],
            [EffectType.STACK_QINGTING_SWORD],
        )
        self.assertEqual(zhuang_fangyi_skill.enhancements[1].effects[0].count, 1)
        self.assertEqual([effect.effect_id for effect in zhuang_fangyi_skill.effects], [EffectType.STACK_QINGTING_SWORD])
        self.assertEqual(zhuang_fangyi_skill.effects[0].count, -1)
        self.assertEqual(zhuang_fangyi_skill.stagger_value, 15)
        self.assertIn("45%", zhuang_fangyi_skill.damage_multiplier)
        self.assertIn("9%", zhuang_fangyi_skill.damage_multiplier)
        self.assertIn("6倍", zhuang_fangyi_skill.damage_multiplier)

        arcane = characters["arcane"]
        arcane_skill = next(skill for skill in arcane.skills if skill.skill_id == "arcane_skill")
        self.assertFalse(arcane_skill.has_enhancement)
        arcane_link = next(skill for skill in arcane.skills if skill.skill_id == "arcane_link")
        self.assertEqual(
            [effect.effect_id for effect in arcane_link.enhancement.effects],
            [
                EffectType.STATUS_SPELL_INFLICT,
                EffectType.VULN_NATURAL,
                EffectType.VULN_COLD,
                EffectType.STATUS_CONFINEMENT,
            ],
        )

        ardelia = characters["ardelia"]
        ardelia_skill = next(skill for skill in ardelia.skills if skill.skill_id == "ardelia_skill")
        self.assertEqual(ardelia_skill.effects, [])
        self.assertEqual(ardelia_skill.enhancement.effects[0].count, -1)

        gilberta = characters["gilberta"]
        gilberta_link = next(skill for skill in gilberta.skills if skill.skill_id == "gilberta_link")
        self.assertEqual(
            [effect.effect_id for effect in gilberta_link.enhancement.effects], [EffectType.STATUS_HEAVY_HIT]
        )

    def test_remaining_character_review_fixes(self):
        characters = load_all_characters()

        antal = characters["antal"]
        antal_skill = next(skill for skill in antal.skills if skill.skill_id == "antal_skill")
        self.assertEqual(
            [effect.effect_id for effect in antal_skill.effects],
            [EffectType.STATUS_FOCUS, EffectType.VULN_ELECTROMAGNETIC, EffectType.VULN_BURN],
        )
        antal_link = next(skill for skill in antal.skills if skill.skill_id == "antal_link")
        self.assertEqual(
            [effect.effect_id for effect in antal_link.enhancement.effects], [EffectType.TRIGGER_REPEAT_EFFECT]
        )

        arclight = characters["arclight"]
        arclight_skill = next(skill for skill in arclight.skills if skill.skill_id == "arclight_skill")
        self.assertEqual(arclight_skill.enhancement.effects[0].count, -1)
        arclight_ultimate = next(skill for skill in arclight.skills if skill.skill_id == "arclight_ultimate")
        self.assertEqual(arclight_ultimate.enhancement.effects[0].count, -1)

        fluorite = characters["fluorite"]
        fluorite_skill = next(skill for skill in fluorite.skills if skill.skill_id == "fluorite_skill")
        self.assertEqual(fluorite_skill.effects[0].effect_id, EffectType.MECH_BOMB)
        fluorite_link = next(skill for skill in fluorite.skills if skill.skill_id == "fluorite_link")
        self.assertEqual(
            [effect.effect_id for effect in fluorite_link.enhancement.effects], [EffectType.STATUS_SPELL_INFLICT]
        )

        alesh = characters["alesh"]
        alesh_skill = next(skill for skill in alesh.skills if skill.skill_id == "alesh_skill")
        self.assertEqual(alesh_skill.effects, [])
        self.assertEqual(alesh_skill.enhancement.effects[0].count, -1)

        endministrator = characters["endministrator"]
        endministrator_normal = next(skill for skill in endministrator.skills if skill.skill_id == "endministrator_normal")
        endministrator_link = next(skill for skill in endministrator.skills if skill.skill_id == "endministrator_link")
        endministrator_ultimate = next(skill for skill in endministrator.skills if skill.skill_id == "endministrator_ultimate")
        self.assertNotIn(EffectType.STATUS_ORIGINIUM_CRYSTAL, [effect.effect_id for effect in endministrator_normal.effects])
        self.assertEqual([effect.effect_id for effect in endministrator_link.effects], [EffectType.STATUS_ORIGINIUM_CRYSTAL])
        self.assertEqual(
            [effect.effect_id for effect in endministrator_link.enhancement.effects],
            [EffectType.STATUS_ORIGINIUM_CRYSTAL, EffectType.TRIGGER_ADDITIONAL],
        )
        self.assertEqual(endministrator_link.enhancement.effects[0].count, -1)
        self.assertEqual(endministrator_ultimate.enhancement.trigger_effects, [EffectType.STATUS_ORIGINIUM_CRYSTAL])
        self.assertEqual(endministrator_ultimate.enhancement.effects[0].effect_id, EffectType.STATUS_ORIGINIUM_CRYSTAL)
        self.assertEqual(endministrator_ultimate.enhancement.effects[0].count, -1)

        catcher = characters["catcher"]
        catcher_skill = next(skill for skill in catcher.skills if skill.skill_id == "catcher_skill")
        self.assertIn(EffectType.BUFF_PROTECTION, [effect.effect_id for effect in catcher_skill.effects])
        catcher_ultimate = next(skill for skill in catcher.skills if skill.skill_id == "catcher_ultimate")
        self.assertEqual(catcher_ultimate.effects[0].effect_id, EffectType.DEBUFF_WEAKEN)

        xaihi = characters["xaihi"]
        xaihi_skill = next(skill for skill in xaihi.skills if skill.skill_id == "xaihi_skill")
        self.assertEqual(xaihi_skill.effects[0].effect_id, EffectType.MECH_SUPPORT_CRYSTAL)
        self.assertEqual(xaihi_skill.enhancements[0].effects[0].effect_id, EffectType.BUFF_HEAL)
        self.assertEqual(xaihi_skill.enhancements[1].effects[0].effect_id, EffectType.BUFF_SPELL_UP)
        xaihi_ultimate = next(skill for skill in xaihi.skills if skill.skill_id == "xaihi_ultimate")
        self.assertEqual(
            [effect.effect_id for effect in xaihi_ultimate.effects],
            [EffectType.BUFF_COLD_UP, EffectType.BUFF_NATURAL_UP],
        )

        last_rite = characters["last_rite"]
        last_rite_link = next(skill for skill in last_rite.skills if skill.skill_id == "last_rite_link")
        self.assertEqual(last_rite_link.enhancement.effects[0].effect_id, EffectType.CLEAR_COLD)
        self.assertEqual(last_rite_link.enhancement.effects[0].count, -1)

        liino = characters["liino"]
        liino_skill = next(skill for skill in liino.skills if skill.skill_id == "liino_skill")
        self.assertEqual(liino_skill.effects[0].effect_id, EffectType.STATUS_SINGING)
        liino_ultimate = next(skill for skill in liino.skills if skill.skill_id == "liino_ultimate")
        self.assertIn(EffectType.STATUS_HIGH_SINGING, [effect.effect_id for effect in liino_ultimate.effects])

        pogranichnik = characters["pogranichnik"]
        pogranichnik_ultimate = next(skill for skill in pogranichnik.skills if skill.skill_id == "pogranichnik_ultimate")
        self.assertEqual(len(pogranichnik_ultimate.enhancements), 2)
        self.assertEqual(pogranichnik_ultimate.enhancements[0].effects[0].count, -1)

        fluorite_ultimate = next(skill for skill in fluorite.skills if skill.skill_id == "fluorite_ultimate")
        self.assertEqual(len(fluorite_ultimate.enhancements), 2)
        self.assertEqual(fluorite_ultimate.enhancements[1].effects[0].effect_id, EffectType.STATUS_SPELL_INFLICT)


if __name__ == "__main__":
    unittest.main()
