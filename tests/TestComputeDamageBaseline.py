"""compute_damage_baseline 解析规则的单元测试。"""

import importlib.util
import sys
import unittest
import unittest.mock
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skill-data" / "compute_damage_baseline.py"
_spec = importlib.util.spec_from_file_location("compute_damage_baseline", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestParseStatClause(unittest.TestCase):
    def test_flat_stats(self):
        self.assertEqual(mod._parse_stat_clause("力量+41"),
                         {"kind": "flat", "stat": "力量", "value": 41})
        self.assertEqual(mod._parse_stat_clause("生命值+1000"),
                         {"kind": "flat", "stat": "生命值", "value": 1000})
        self.assertEqual(mod._parse_stat_clause("源石技艺强度+30"),
                         {"kind": "flat", "stat": "源石技艺强度", "value": 30})

    def test_pct_stats(self):
        self.assertEqual(mod._parse_stat_clause("攻击力+15%"),
                         {"kind": "pct", "stat": "atk_pct", "value": 15.0})
        self.assertEqual(mod._parse_stat_clause("所有技能伤害+20%"),
                         {"kind": "pct", "stat": "all_skill_dmg", "value": 20.0})
        self.assertEqual(mod._parse_stat_clause("暴击率+5%"),
                         {"kind": "pct", "stat": "crit_rate", "value": 5.0})

    def test_element_damage(self):
        self.assertEqual(mod._parse_stat_clause("电磁伤害+16.8%"),
                         {"kind": "pct", "stat": "elem_电磁", "value": 16.8})
        self.assertEqual(mod._parse_stat_clause("物理伤害+15.0%"),
                         {"kind": "pct", "stat": "elem_物理", "value": 15.0})

    def test_conditional_sentences_rejected_by_filter(self):
        # 含触发词的句子不参与面板
        self.assertFalse(mod._is_unconditional("当装备者治疗我方目标后，使该目标受到的伤害-15%"))
        self.assertFalse(mod._is_unconditional("装备者施加破防后，物理伤害+8%"))
        self.assertTrue(mod._is_unconditional("装备者所有技能伤害+20%"))

    def test_unconditional_parse_ignores_garbage(self):
        self.assertIsNone(mod._parse_stat_clause("同名效果无法叠加"))
        self.assertIsNone(mod._parse_stat_clause(""))


class TestSkillMultiplier(unittest.TestCase):
    def _rows(self, rows):
        return {"levels": ["R1"] * 12, "rows": [
            {"label": label, "values": [value] * 12} for label, value in rows
        ]}

    def test_sum_multiplier_rows(self):
        skill = {"rank_stats": self._rows([
            ("初始爆炸伤害倍率", "140%"),
            ("持续伤害每段倍率", "14%"),
            ("追加伤害倍率", "770%"),
            ("初始爆炸失衡值", "10"),
            ("技力消耗", "100"),
        ])}
        mult, stagger, cond = mod._skill_multiplier(skill)
        self.assertAlmostEqual(mult, 924.0)
        self.assertAlmostEqual(stagger, 10.0)
        self.assertEqual(cond, [])

    def test_decimal_multiplier(self):
        skill = {"rank_stats": self._rows([
            ("起跳射击伤害倍率", "50%"),
            ("强化射击自然爆发伤害倍率", "1.3"),
            ("空中普通攻击伤害", "65%"),
        ])}
        mult, _, _ = mod._skill_multiplier(skill)
        self.assertAlmostEqual(mult, 50 + 130 + 65)

    def test_excludes_finisher_and_ult_window(self):
        skill = {"rank_stats": self._rows([
            ("初始爆炸伤害倍率", "140%"),
            ("处决攻击倍率", "900%"),
            ("终结技期间 / 第一段倍率", "330%"),
        ])}
        mult, _, cond = mod._skill_multiplier(skill)
        self.assertAlmostEqual(mult, 140.0)
        self.assertEqual(len(cond), 2)

    def test_consumption_stack_damage_row_not_counted_but_visible(self):
        # 消耗型伤害行（如别礼「消耗每层附着 / 额外伤害倍率」）按消耗层数结算，
        # 每层口径待打桩（P1-8 C9），A 层不计入但必须出现在条件行里供审计
        skill = {"rank_stats": self._rows([
            ("冰刺伤害倍率", "160%"),
            ("斩击基础伤害倍率", "160%"),
            ("消耗每层附着 / 额外伤害倍率", "240%"),
            ("基础获得 / 终结技能量", "40"),
            ("消耗每层附着额外 / 获得终结技能量", "15"),
        ])}
        mult, _, cond = mod._skill_multiplier(skill)
        self.assertAlmostEqual(mult, 320.0)
        self.assertEqual(len(cond), 1)
        self.assertIn("240%", cond[0])
        self.assertIn("按消耗层数", cond[0])

    def test_support_skill_zero(self):
        skill = {"rank_stats": self._rows([
            ("基础治疗值", "324"),
            ("法术增幅效果", "15%"),
        ])}
        mult, _, _ = mod._skill_multiplier(skill)
        self.assertEqual(mult, 0.0)

    def test_buff_labels_do_not_add_to_damage_multiplier(self):
        skill = {"rank_stats": self._rows([
            ("初始伤害倍率", "140%"),
            ("攻击力提升", "10%"),
            ("攻击伤害提高", "12%"),
            ("攻击倍率增加", "15%"),
            ("暴击伤害倍率", "20%"),
        ])}
        mult, _, _ = mod._skill_multiplier(skill)
        self.assertEqual(mult, 140.0)


class TestCollectStats(unittest.TestCase):
    def test_set_effect_first_clause_only(self):
        mods = []
        trace = []
        mod._collect_stats("3件套组效果：装备者攻击力+15%。当装备者施放连携技时，战技伤害+30%。",
                           mods, trace, "测试")
        self.assertEqual(len(mods), 1)
        self.assertEqual(mods[0], {"kind": "pct", "stat": "atk_pct", "value": 15.0})

    def test_multi_clause_all_types(self):
        mods = []
        mod._collect_stats("装备者造成的所有类型伤害+20%，受到的所有类型伤害-10%，终结技充能效率+10%",
                           mods, [], "测试")
        kinds = sorted((m["kind"], m.get("stat")) for m in mods)
        self.assertIn(("pct", "all_damage"), kinds)
        self.assertIn(("pct", "ult_charge"), kinds)
        self.assertEqual(sum(1 for m in mods if m["kind"] == "taken_reduction"), 1)


class TestEquipmentStats(unittest.TestCase):
    def test_refinement_percentage_replaces_lv70_value(self):
        piece = {"lv70_stats": {"物理伤害加成": "+5%", "暴击率加成": "+12.5%"},
                 "refinement_max": {"物理伤害加成": "+9%"}}
        self.assertCountEqual(mod._piece_pct_mods(piece),
                              [("elem_物理", 9.0), ("crit_rate", 12.5)])

    def test_non_integer_lv70_value_is_not_flat_stat(self):
        piece = {"lv70_stats": {"攻击力": "+5%", "力量": 42},
                 "refinement_max": {"力量": "+51"}}
        self.assertIsNone(mod._piece_stat(piece, "攻击力"))
        self.assertEqual(mod._piece_stat(piece, "力量"), 51)

    def test_set_effect_requires_three_matching_pieces(self):
        piece = {"set": "主套", "set_effect": "3件套组效果：装备者攻击力+15%。"}
        equipments = {"甲": piece, "乙": piece, "丙": piece,
                      "异套": {"set": "副套", "set_effect": "3件套组效果：装备者攻击力+50%。"}}
        char = {"name": "测试", "element": "物理", "skills": []}
        for pieces, expected in [(["甲", "乙", "异套", None], 0),
                                 (["甲", "乙", "丙", "异套"], 15),
                                 (["甲", "乙", "missing", None], 0)]:
            with self.subTest(pieces=pieces):
                build = {"equipment": {"set_main": "主套", "pieces": pieces}}
                result = mod.compute_character("test", char, build, {}, equipments, {}, {})
                self.assertEqual(result["panel"]["atk_pct"], expected)

    def test_primary_trace_marks_element_fallback(self):
        char = {"name": "管理员", "element": "物理", "skills": []}
        result = mod.compute_character("endministrator", char, {}, {}, {}, {}, {})
        self.assertEqual(result["primary_stat"], "力量")
        self.assertIn("按元素推断", next(line for line in result["trace"] if "主能力:" in line))

    def test_same_name_wiki_candidates_use_item_with_ability_data(self):
        char = {
            "name": "同名角色",
            "element": "物理",
            "base_stats": {"rows": {"攻击力": [100], "力量": [10], "敏捷": [20]}, "levels": [90]},
            "skills": [],
        }
        result = mod.compute_character(
            "same_name",
            char,
            {},
            {},
            {},
            {"item-with-abilities": "力量"},
            {"同名角色": ["item-without-abilities", "item-with-abilities"]},
            {"item-with-abilities": "敏捷"},
        )
        self.assertEqual(result["primary_stat"], "力量")
        self.assertEqual(result["secondary_stat"], "敏捷")

    def test_primary_ability_equip_word_applies_multiplier(self):
        # 插板/护板类装备的「主能力」词条：主能力总值乘 (1+X%)，仅主能力、副能力不吃
        char = {"name": "测试", "element": "物理",
                "base_stats": {"rows": {"攻击力": [100], "力量": [10], "敏捷": [20]}, "levels": [90]},
                "skills": []}
        piece = {"set": "生物辅助装备组", "lv70_stats": {}, "refinement_max": {"主能力": "+26.9%"}}
        equipments = {"护板": piece}
        result = mod.compute_character("t", char, {"equipment": {"pieces": ["护板"]}},
                                       {}, equipments, {}, {})
        self.assertIn("含主能力词条+26.9%",
                      next(line for line in result["trace"] if "主能力:" in line))
        # ATK = 100 x (1 + 0.005 x 10 x 1.269) = 106.3（无词条时为 105.0）
        self.assertAlmostEqual(result["panel"]["ATK"], 106.3, places=1)


class TestCliPathValidation(unittest.TestCase):
    """--char/--out 输入校验（SonarCloud 路径注入热点）。"""

    def test_char_rejects_path_traversal(self):
        for bad in ("../evil", "a/b", "A", "purrchena;rm", "中文"):
            with self.subTest(bad=bad), \
                    unittest.mock.patch.object(sys, "argv", ["x", "--char", bad]):
                self.assertEqual(mod.main(), 2)

    def test_char_accepts_snake_case(self):
        # 合法 key 不应在校验处返回 2；且调试模式默认不写回（见 main 的 dry-run）
        with unittest.mock.patch.object(sys, "argv", ["x", "--char", "purrchena"]):
            code = mod.main()
        self.assertIn(code, (0, 1))

    def test_out_rejects_outside_data_dir(self):
        with unittest.mock.patch.object(sys, "argv",
                                        ["x", "--out", "C:/Windows/evil.json"]):
            self.assertEqual(mod.main(), 2)


class TestFullMultiplierAndCycleExpect(unittest.TestCase):
    """战技完整倍率覆盖（召唤物/多段机制）与循环期望排序口径。"""

    @staticmethod
    def _rows(rows):
        return {"levels": ["R1"] * 12, "rows": [
            {"label": label, "values": [value] * 12} for label, value in rows
        ]}

    def _char(self):
        return {
            "name": "青霆使",
            "element": "电磁",
            "base_stats": {"rows": {"攻击力": [1000], "意志": [10]}, "levels": [90]},
            "skills": [
                {"skill_id": "zhuang_fangyi_skill", "skill_type": "战技", "name": "惊霆诀",
                 "rank_stats": self._rows([("雷击伤害倍率", "45%")])},
                {"skill_id": "x_link", "skill_type": "连携技", "name": "连携",
                 "rank_stats": self._rows([("伤害倍率", "360%")])},
                {"skill_id": "x_normal", "skill_type": "普通攻击", "name": "普攻",
                 "rank_stats": self._rows([("伤害倍率", "551%")])},
            ],
        }

    def test_zhuang_fangyi_skill_full_multiplier_applies(self):
        result = mod.compute_character("zhuang_fangyi", self._char(), {}, {}, {}, {}, {}, {})
        skill = result["skills"][0]
        self.assertEqual(skill["multiplier_pct"], 45.0)
        self.assertEqual(skill["full_multiplier_pct"], 360.0)
        self.assertAlmostEqual(skill["full_expect"], skill["crit_expect"] * 8, places=0)

    def test_override_key_scoped(self):
        # 其他角色不吃庄方宜的覆盖表
        result = mod.compute_character("someone_else", self._char(), {}, {}, {}, {}, {}, {})
        self.assertNotIn("full_expect", result["skills"][0])

    def _typhoeus_char(self):
        # 提弗洛斯：浮空多段空中普攻 + 终结技箭雨，表行加总低估（wiki 2116）
        return {
            "name": "空中猎手",
            "element": "自然",
            "base_stats": {"rows": {"攻击力": [1000], "意志": [10]}, "levels": [90]},
            "skills": [
                {"skill_id": "typhoeus_skill", "skill_type": "战技", "name": "风矢穿林",
                 "rank_stats": self._rows([
                     ("起跳射击伤害倍率", "50%"),
                     ("空中普通攻击伤害", "65%"),
                     ("空中重击伤害", "100%"),
                     ("强化射击自然爆发伤害倍率", "1.3"),
                 ])},
                {"skill_id": "typhoeus_ultimate", "skill_type": "终结技", "name": "冰山呼告",
                 "rank_stats": self._rows([
                     ("强力箭爆炸伤害", "300%"),
                     ("常规箭雨伤害", "75%"),
                     ("强化箭雨伤害", "200%"),
                 ])},
            ],
        }

    def test_typhoeus_air_attack_overrides(self):
        result = mod.compute_character("typhoeus", self._typhoeus_char(), {}, {}, {}, {}, {}, {})
        skill = {s["skill_id"]: s for s in result["skills"]}
        self.assertEqual(skill["typhoeus_skill"]["multiplier_pct"], 345.0)
        # 满猎矢口径：基础段 410% + 猎矢消耗自然爆发 130% x 5 = 1060%（无猎矢保守口径为 540%）
        self.assertEqual(skill["typhoeus_skill"]["full_multiplier_pct"], 1060.0)
        self.assertEqual(skill["typhoeus_ultimate"]["multiplier_pct"], 575.0)
        self.assertEqual(skill["typhoeus_ultimate"]["full_multiplier_pct"], 800.0)

    def test_full_caliber_requirements_consistent(self):
        # 满口径依赖角色必须有保守口径覆盖，且注入保守覆盖后战技回退到 540%
        for key, requirement in mod.FULL_CALIBER_REQUIREMENTS.items():
            with self.subTest(key=key):
                self.assertIn("attach", requirement)
                self.assertTrue(
                    any(k[0] == key for k in mod.CONSERVATIVE_FULL_OVERRIDES),
                    "满口径依赖角色必须配置保守口径覆盖",
                )
        result = mod.compute_character(
            "typhoeus", self._typhoeus_char(), {}, {}, {}, {}, {}, {},
            full_overrides={**mod._SKILL_FULL_MULTIPLIER_OVERRIDES, **mod.CONSERVATIVE_FULL_OVERRIDES},
        )
        skill = {s["skill_id"]: s for s in result["skills"]}
        self.assertEqual(skill["typhoeus_skill"]["full_multiplier_pct"], 540.0)
        self.assertEqual(skill["typhoeus_ultimate"]["full_multiplier_pct"], 800.0)

    def test_typhoeus_override_key_scoped(self):
        result = mod.compute_character("someone_else", self._typhoeus_char(), {}, {}, {}, {}, {}, {})
        for s in result["skills"]:
            self.assertNotIn("full_expect", s)

    def test_cycle_expect_link4_field(self):
        """满连击口径：仅战技段 ×1.75（连击只加成下一发战技/终结技）。"""
        result = mod.compute_character("zhuang_fangyi", self._char(), {}, {}, {}, {}, {}, {})
        skills = {s["type"]: s for s in result["skills"]}
        expected = (skills["战技"]["full_expect"] * 1.75
                    + skills["连携技"]["crit_expect"]
                    + 2 * skills["普通攻击"]["crit_expect"])
        self.assertAlmostEqual(result["cycle_expect_link4"], expected, places=0)
        self.assertGreater(result["cycle_expect_link4"], result["cycle_expect"])

    def test_cycle_expect_combines_full_skill_link_and_normals(self):
        result = mod.compute_character("zhuang_fangyi", self._char(), {}, {}, {}, {}, {}, {})
        skills = {s["type"]: s for s in result["skills"]}
        expected = (skills["战技"]["full_expect"]
                    + skills["连携技"]["crit_expect"]
                    + 2 * skills["普通攻击"]["crit_expect"])
        self.assertAlmostEqual(result["cycle_expect"], expected, places=0)
        self.assertGreater(result["cycle_expect"], skills["战技"]["crit_expect"] * 8)


if __name__ == "__main__":
    unittest.main()
