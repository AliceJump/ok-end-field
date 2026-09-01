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
_document_tables = _MODULE._document_tables
_document_text = _MODULE._document_text
_skill_tables = _MODULE._skill_tables
_stack_rules = _MODULE._stack_rules
_needs_separate_enhancement = _MODULE._needs_separate_enhancement
_validate_snapshot_manifest = _MODULE._validate_snapshot_manifest


def _text_block(text: str) -> dict:
    return {"kind": "text", "text": {"inlineElements": [{"text": {"text": text}}]}}


def _table_block(prefix: str, rows: list[list[str]], block_map: dict) -> dict:
    row_ids = [f"{prefix}_row_{index}" for index in range(len(rows))]
    column_count = max(len(row) for row in rows)
    column_ids = [f"{prefix}_column_{index}" for index in range(column_count)]
    cell_map = {}
    for row_index, row in enumerate(rows):
        for column_index in range(column_count):
            text_id = f"{prefix}_text_{row_index}_{column_index}"
            block_map[text_id] = _text_block(row[column_index] if column_index < len(row) else "")
            cell_map[f"{row_ids[row_index]}_{column_ids[column_index]}"] = {"childIds": [text_id]}
    return {
        "kind": "table",
        "table": {
            "rowIds": row_ids,
            "columnIds": column_ids,
            "cellMap": cell_map,
        },
    }


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

    def test_rejects_incomplete_latest_manifest(self):
        manifest = {"complete": False, "incomplete_reasons": ["operator_failures: 1"]}
        with self.assertRaisesRegex(ValueError, "latest.json 指向不完整快照"):
            _validate_snapshot_manifest(manifest, explicit_snapshot=False, allow_partial=False)

    def test_explicit_partial_snapshot_requires_opt_in(self):
        manifest = {"complete": False, "incomplete_reasons": ["catalog_subset: requested 1 of 2"]}
        with self.assertRaisesRegex(ValueError, "--allow-partial"):
            _validate_snapshot_manifest(manifest, explicit_snapshot=True, allow_partial=False)
        _validate_snapshot_manifest(manifest, explicit_snapshot=True, allow_partial=True)

    def test_nested_document_keeps_text_and_tables_separate(self):
        block_map = {
            "description": _text_block("造成自然伤害。"),
            "inner": {"kind": "container", "childIds": ["rank_table", "material_wrapper"]},
            "material_wrapper": {"kind": "container", "blockIds": ["material_table"]},
            "root": {"kind": "container", "childIds": ["description", "outer"]},
            "outer": {"kind": "container", "blockIds": ["inner"]},
        }
        rank_rows = [["技能等级", "RANK 1"], ["伤害倍率", "100%"]]
        material_rows = [["技能等级", "材料消耗"], ["专精 1", "折金票 x3"]]
        block_map["rank_table"] = _table_block("rank", rank_rows, block_map)
        block_map["material_table"] = _table_block("material", material_rows, block_map)
        document_map = {"skill": {"blockIds": ["root"], "blockMap": block_map}}

        description = _document_text(document_map, "skill")
        tables = _document_tables(document_map, "skill")
        rank_table, material_table = _skill_tables(tables)

        self.assertEqual(description, "造成自然伤害。")
        self.assertNotIn("[[", description)
        self.assertNotIn("技能等级", description)
        self.assertEqual(rank_table, rank_rows)
        self.assertEqual(material_table, material_rows)


if __name__ == "__main__":
    unittest.main()
