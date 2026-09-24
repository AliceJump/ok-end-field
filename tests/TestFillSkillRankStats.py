"""fill_skill_rank_stats 纯函数测试：数值表转换与描述拼接。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "skill-data" / "fill_skill_rank_stats.py"
_SPEC = importlib.util.spec_from_file_location("fill_skill_rank_stats", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

fill = _MODULE


class TestFlattenDescription(unittest.TestCase):
    def test_multi_line_joined_without_separator(self):
        self.assertEqual(fill._flatten_description("第一段。\n第二段。\n"), "第一段。第二段。")

    def test_blank_lines_dropped(self):
        self.assertEqual(fill._flatten_description("A。\n\n B。\n"), "A。B。")


class TestRankStats(unittest.TestCase):
    def test_normal_table(self):
        table = [
            ["技能等级", "RANK 1", "RANK 2", "专精3"],
            ["伤害倍率", "100%", "110%", "200%"],
            ["失衡值", "10", "10", "10"],
        ]
        stats = fill._rank_stats(table)
        self.assertEqual(stats["levels"], ["RANK 1", "RANK 2", "专精3"])
        self.assertEqual(stats["rows"][0], {"label": "伤害倍率", "values": ["100%", "110%", "200%"]})
        self.assertEqual(stats["rows"][1]["values"], ["10", "10", "10"])

    def test_short_row_padded(self):
        table = [["技能等级", "RANK 1", "RANK 2"], ["倍率", "100%"]]
        stats = fill._rank_stats(table)
        self.assertEqual(stats["rows"][0]["values"], ["100%", ""])

    def test_invalid_header_returns_none(self):
        self.assertIsNone(fill._rank_stats([]))
        self.assertIsNone(fill._rank_stats([["其他", "RANK 1"]]))
        self.assertIsNone(fill._rank_stats([["技能等级"]]))

    def test_rows_without_label_skipped(self):
        table = [["技能等级", "RANK 1"], ["倍率", "100%"], ["", "ignored"]]
        stats = fill._rank_stats(table)
        self.assertEqual(len(stats["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
