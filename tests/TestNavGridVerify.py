# -*- coding: utf-8 -*-
"""独立校验器 ``scripts/nav/verify_grid.py`` 的判定逻辑测试。

为什么要专门测它：这个工具的**措辞**先后错过两次
（把"没有可行走邻居"说成"误点"、把被禁斜穿规则制造的孤立当成"真被困"），
而它给出的结论是要给人看的。这里把三类单格事实与两个口径的连通性钉死。

被测代码刻意留在脚本里（不搬进 ``src/nav``）：校验器要**独立于** ``grid_io``
才有验收意义，所以测试直接从脚本导入。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "nav"))

from verify_grid import _components, _single_cell_facts  # noqa: E402

UNKNOWN, FREE, BLOCKED = 0, 1, 2
_CH = {".": UNKNOWN, "o": FREE, "#": BLOCKED}


def _cells(rows):
    return np.array([[_CH[ch] for ch in row] for row in rows], dtype=np.uint8)


class TestSingleCellFacts(unittest.TestCase):
    def test_free_walled_in_all_eight(self):
        """可行走格八邻全是阻挡 = 真被困（唯一支持"多半是误点"的证据）。"""
        facts = _single_cell_facts(_cells(["###",
                                           "#o#",
                                           "###"]))
        self.assertEqual(facts["free_walled"], [(1, 1)])
        self.assertEqual(facts["free_lonely"], [])
        self.assertEqual(facts["unknown_walled"], [])

    def test_free_lonely_with_unknown_neighbours(self):
        """可行走格四周是未知 → 只是"没连到已标出的通路"，不是被困。"""
        facts = _single_cell_facts(_cells(["...",
                                           ".o.",
                                           "..."]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [(1, 1)])
        self.assertEqual(facts["unknown_walled"], [])

    def test_corner_rule_island_is_not_reported(self):
        """被禁斜穿规则制造的"孤立"不该报：对角有可行走格，游戏里能走过去。

        正交四邻全阻挡，只剩两个对角可达；这正是"规划器图里孤立、物理能到"的情形。
        """
        facts = _single_cell_facts(_cells(["#o#",
                                           "#o#",
                                           "#o#"]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [])
        # 中间那格的正交邻居全是阻挡，但它在规划器图里并不孤立（对角连通）
        self.assertNotIn((1, 1), facts["free_walled"])

    def test_unknown_walled_is_not_a_misclick(self):
        """未知格被墙围死 = 没人画过的封闭小空间，要单独归类，不能混进误点。"""
        facts = _single_cell_facts(_cells(["###",
                                           "#.#",
                                           "###"]))
        self.assertEqual(facts["unknown_walled"], [(1, 1)])
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [])

    def test_edge_only_counts_in_bounds_neighbours(self):
        """边角只数界内邻居：角上的可行走格只有 3 个邻居，即使全阻挡也不算"八邻全阻挡"。"""
        facts = _single_cell_facts(_cells(["o#",
                                           "##"]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [(0, 0)])


class TestComponents(unittest.TestCase):
    def test_two_bases_differ_when_unknown_bridges(self):
        """两个口径会给出不同块数：未知格能把"可行走到不了"的两块连起来。"""
        cells = _cells(["o.o"])
        blocked = cells == BLOCKED
        free_only = _components(cells == FREE, blocked)
        passable = _components(cells != BLOCKED, blocked)
        self.assertEqual(free_only, [1, 1])          # 不冒险：两块
        self.assertEqual(passable, [3])              # 规划器可达：连成一块

    def test_no_corner_cutting_splits_components(self):
        """禁斜穿墙角：对角相连但两个角都被挡时，规划器图上不连通。"""
        cells = _cells(["o#",
                        "#o"])
        blocked = cells == BLOCKED
        self.assertEqual(_components(cells == FREE, blocked), [1, 1])
        # 去掉禁斜穿规则（纯八连通）就是一块——这正是"被自己规则制造出来的孤立"
        pure = _components(cells == FREE, np.zeros_like(blocked))
        self.assertEqual(pure, [2])

    def test_counts_and_sizes(self):
        cells = _cells(["ooo",
                        "o#o",
                        "ooo"])
        blocked = cells == BLOCKED
        self.assertEqual(_components(cells != BLOCKED, blocked), [8])


if __name__ == "__main__":
    unittest.main()
