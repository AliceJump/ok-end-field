"""官方 WIKI 技能自动分析脚本测试。"""

import importlib.util
import sys
import unittest
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "skill-data" / "analyze_operator_skills.py"
_SPEC = importlib.util.spec_from_file_location("analyze_operator_skills", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

_conditions = _MODULE._conditions
_stack_rules = _MODULE._stack_rules
_needs_separate_enhancement = _MODULE._needs_separate_enhancement


class TestAnalyzeOperatorSkills(unittest.TestCase):
    def test_ignores_normal_attack_segments_and_stagger_points(self):
        text = "对敌人进行至多5段攻击。作为主控干员时，重击会造成18点失衡。"
        self.assertEqual(_stack_rules(text), [])

    def test_detects_state_stack_threshold(self):
        rules = _stack_rules("当敌人达到4层破防时可以发动")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["subject"], "破防")
        self.assertEqual(rules[0]["count"], 4)
        self.assertEqual(rules[0]["operator"], ">=")

        rules = _stack_rules("当有敌人进入3层及以上寒冷附着状态时可以发动")
        self.assertEqual(rules[0]["subject"], "寒冷附着")
        self.assertEqual(rules[0]["operator"], ">=")

    def test_detects_named_resource_count(self):
        rules = _stack_rules("生成3柄青霆剑，一次战技最多生成3柄青霆剑")
        self.assertEqual([rule["count"] for rule in rules], [3, 3])
        self.assertTrue(all("青霆剑" in rule["raw"] for rule in rules))

    def test_detects_multiple_independent_conditions(self):
        text = (
            "造成4段自然伤害，若命中目标身上粘有自制炸弹，则立刻将其引爆。"
            "若最后一段伤害命中处于2层及以上寒冷附着或自然附着的目标，"
            "则再次施加该法术附着。"
        )
        conditions = _conditions(text)
        self.assertEqual(len(conditions), 2)
        self.assertIn("自制炸弹", conditions[0].trigger_text)
        self.assertIn("再次施加", conditions[1].result_text)
        self.assertTrue(conditions[1].repeats)

    def test_marks_activation_condition(self):
        conditions = _conditions("当有敌人进入燃烧或腐蚀状态时可以发动。")
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0].role, "activation")
        self.assertIn("STATUS_BURNING", conditions[0].trigger_effects)
        self.assertIn("STATUS_CORROSION", conditions[0].trigger_effects)

    def test_hit_resource_gain_is_base_output(self):
        condition = _conditions("如果命中敌人，会获得1层熔火。")[0]
        self.assertFalse(_needs_separate_enhancement(condition))

        condition = _conditions("如果已拥有4层熔火，则消耗所有层数并追加攻击。")[0]
        self.assertTrue(_needs_separate_enhancement(condition))


if __name__ == "__main__":
    unittest.main()
