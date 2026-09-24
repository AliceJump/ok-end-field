"""TestSkillRotation — 伤害优先技能释放序列的排序规则。"""

from __future__ import annotations

import unittest

from src.data.skill_rotation import (
    clear_cache,
    generate_damage_rotation,
    load_damage_baseline,
)


class TestGenerateDamageRotation(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    def test_order_by_damage_desc(self):
        baseline = {"A": 3000.0, "B": 1000.0, "C": 2000.0}
        # D 无基准数据 → 排最后
        self.assertEqual(
            generate_damage_rotation(["A", "B", "C", "D"], baseline), ["1", "3", "2", "4"]
        )

    def test_unknown_member_placeholder_last(self):
        baseline = {"A": 100.0, "B": 200.0}
        # "?" 视为 0 伤害，排在已知角色之后（B 200 > A 100）；两个未知保持队位顺序
        self.assertEqual(generate_damage_rotation(["?", "A", "?", "B"], baseline), ["4", "2", "1", "3"])

    def test_missing_data_falls_back_to_position_order(self):
        self.assertEqual(generate_damage_rotation(["X", "Y", "Z", "W"], {}), ["1", "2", "3", "4"])

    def test_tie_keeps_position_order(self):
        baseline = {"A": 500.0, "B": 500.0, "C": 100.0}
        self.assertEqual(generate_damage_rotation(["A", "B", "C", "D"], baseline), ["1", "2", "3", "4"])

    def test_baseline_argument_overrides_cache(self):
        # 显式传入 baseline 时不读取缓存文件
        clear_cache()
        self.assertEqual(
            generate_damage_rotation(["A", "B"], {"A": 1.0}), ["1", "2"]
        )

    def test_real_baseline_loads_and_orders(self):
        baseline = load_damage_baseline()
        self.assertTrue(baseline, "damage_baseline.json 应存在且非空")
        # 真实队伍：位置1=赛希(0，纯辅助) 2=弭弗(最高) 3=莱万汀 4=噗切娜
        tokens = generate_damage_rotation(["赛希", "弭弗", "莱万汀", "噗切娜"], baseline)
        self.assertEqual(tokens, ["2", "3", "4", "1"])


class TestLoadDamageBaseline(unittest.TestCase):
    def tearDown(self):
        clear_cache()

    def test_loads_all_characters(self):
        clear_cache()
        baseline = load_damage_baseline()
        self.assertGreaterEqual(len(baseline), 30)
        self.assertGreater(baseline.get("弭弗", 0), baseline.get("赛希", 0))

    def test_missing_file_returns_empty(self):
        clear_cache()
        baseline = load_damage_baseline(__import__("pathlib").Path("Z:/nonexistent.json"))
        self.assertEqual(baseline, {})


if __name__ == "__main__":
    unittest.main()
