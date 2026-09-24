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


class TestCliPathValidation(unittest.TestCase):
    """--char/--out 输入校验（SonarCloud 路径注入热点）。"""

    def test_char_rejects_path_traversal(self):
        for bad in ("../evil", "a/b", "A", "puqiena;rm", "中文"):
            with self.subTest(bad=bad), \
                    unittest.mock.patch.object(sys, "argv", ["x", "--char", bad]):
                self.assertEqual(mod.main(), 2)

    def test_char_accepts_snake_case(self):
        # 合法 key 不应在校验处返回 2；且调试模式默认不写回（见 main 的 dry-run）
        with unittest.mock.patch.object(sys, "argv", ["x", "--char", "puqiena"]):
            code = mod.main()
        self.assertIn(code, (0, 1))

    def test_out_rejects_outside_data_dir(self):
        with unittest.mock.patch.object(sys, "argv",
                                        ["x", "--out", "C:/Windows/evil.json"]):
            self.assertEqual(mod.main(), 2)


if __name__ == "__main__":
    unittest.main()
