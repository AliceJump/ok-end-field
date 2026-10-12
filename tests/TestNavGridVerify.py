"""独立校验器 ``scripts/nav/verify_grid.py`` 的判定逻辑测试。

为什么要专门测它：这个工具的**措辞**先后错过两次
（把"没有可行走邻居"说成"误点"、把被禁斜穿规则制造的孤立当成"真被困"），
而它给出的结论是要给人看的。这里把三类单格事实与两个口径的连通性钉死。

被测代码刻意留在脚本里（不搬进 ``src/nav``）：校验器要**独立于** ``grid_io``
才有验收意义，所以测试直接从脚本导入。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "nav"))

from verify_grid import (
    POLICY_ALLOW_UNKNOWN,
    POLICY_NO_UNKNOWN,
    Report,
    _components,
    _resolve_within_root,
    _single_cell_facts,
    _walk_masks,
    compare_legacy,
    report_cells,
)

UNKNOWN, FREE, BLOCKED = 0, 1, 2
_CH = {".": UNKNOWN, "o": FREE, "#": BLOCKED}


def _cells(rows):
    return np.array([[_CH[ch] for ch in row] for row in rows], dtype=np.uint8)


class TestSingleCellFacts(unittest.TestCase):
    """覆盖单格孤立和围死诊断。"""

    def test_free_walled_in_all_eight(self):
        """可行走格八邻全是阻挡 = 真被困（唯一支持"多半是误点"的证据）。"""
        facts = _single_cell_facts(_cells(["###", "#o#", "###"]))
        self.assertEqual(facts["free_walled"], [(1, 1)])
        self.assertEqual(facts["free_lonely"], [])
        self.assertEqual(facts["unknown_walled"], [])

    def test_free_lonely_with_unknown_neighbours(self):
        """可行走格四周是未知 → 只是"没连到已标出的通路"，不是被困。"""
        facts = _single_cell_facts(_cells(["...", ".o.", "..."]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [(1, 1)])
        self.assertEqual(facts["unknown_walled"], [])

    def test_corner_rule_island_is_not_reported(self):
        """被禁斜穿规则制造的"孤立"不该报：对角有可行走格，游戏里能走过去。

        正交四邻全阻挡，只剩两个对角可达；这正是"规划器图里孤立、物理能到"的情形。
        """
        facts = _single_cell_facts(_cells(["o#o", "#o#", "o#o"]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [])
        # 中间那格的正交邻居全是阻挡，但四角可行走，因此并不孤立。
        self.assertNotIn((1, 1), facts["free_walled"])

    def test_unknown_walled_is_not_a_misclick(self):
        """未知格被墙围死 = 没人画过的封闭小空间，要单独归类，不能混进误点。"""
        facts = _single_cell_facts(_cells(["###", "#.#", "###"]))
        self.assertEqual(facts["unknown_walled"], [(1, 1)])
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [])

    def test_edge_only_counts_in_bounds_neighbours(self):
        """边角只数界内邻居：角上的可行走格只有 3 个邻居，即使全阻挡也不算"八邻全阻挡"。"""
        facts = _single_cell_facts(_cells(["o#", "##"]))
        self.assertEqual(facts["free_walled"], [])
        self.assertEqual(facts["free_lonely"], [(0, 0)])


class TestComponents(unittest.TestCase):
    """覆盖连通块统计与禁斜穿规则。"""

    def test_two_bases_differ_when_unknown_bridges(self):
        """两个口径会给出不同块数：未知格能把"可行走到不了"的两块连起来。"""
        cells = _cells(["o.o"])
        free_only = _components(cells == FREE, cells == FREE)
        passable = _components(cells != BLOCKED, cells != BLOCKED)
        self.assertEqual(free_only, [1, 1])  # 不冒险：两块
        self.assertEqual(passable, [3])  # 规划器可达：连成一块

    def test_no_corner_cutting_splits_components(self):
        """禁斜穿墙角：对角相连但两个角都被挡时，规划器图上不连通。"""
        cells = _cells(["o#", "#o"])
        self.assertEqual(_components(cells == FREE, cells == FREE), [1, 1])
        # 去掉禁斜穿规则（守卫全放行）就是一块——这正是"被自己规则制造出来的孤立"
        pure = _components(cells == FREE, np.ones_like(cells, dtype=bool))
        self.assertEqual(pure, [2])

    def test_counts_and_sizes(self):
        cells = _cells(["ooo", "o#o", "ooo"])
        self.assertEqual(_components(cells != BLOCKED, cells != BLOCKED), [8])


class TestWalkPredicateIsSingleSourced(unittest.TestCase):
    """可走判据必须**只有一个来源**，而且节点与守卫同源。

    回归：本工具原先在"可行走（不冒险）"那一行用了「节点认 Free、守卫只查阻挡」这组
    **不匹配**的判据——斜边于是能借一个"节点不算可走、守卫却放行"的未知正交格把两片并
    起来，块数被低估。实测 base01 报 5 块（真值 8）、map02 报 11 块（真值 12），与编辑器
    同一处偏差。
    """

    def test_mismatched_predicates_merge_diagonal_blocks(self):
        """反例：判据不匹配时会把两个 Free 片并成一块——这正是原实现犯的错。"""
        cells = _cells(["o.", ".o"])
        same_source = _components(cells == FREE, cells == FREE)
        mismatched = _components(cells == FREE, cells != BLOCKED)
        self.assertEqual(same_source, [1, 1], "同源判据：Unknown 的正交格挡住斜边")
        self.assertEqual(mismatched, [2], "不匹配判据：斜边被放行，两块被并起来")

    def test_walk_masks_are_same_source_per_policy(self):
        """两种策略各自给出的节点/守卫掩码必须逐格相同。"""
        cells = _cells(["o.o", "#.#"])
        for policy in (POLICY_NO_UNKNOWN, POLICY_ALLOW_UNKNOWN):
            node, guard = _walk_masks(cells, policy)
            self.assertTrue((node == guard).all(), policy)
        node_free, _ = _walk_masks(cells, POLICY_NO_UNKNOWN)
        self.assertEqual(int(node_free.sum()), 2, "不冒险档只认 Free")
        node_loose, _ = _walk_masks(cells, POLICY_ALLOW_UNKNOWN)
        # o.o / #.# -> 非阻挡 = 两个 Free + 两个 Unknown = 4 格
        self.assertEqual(int(node_loose.sum()), 4, "允许穿越档认非阻挡")

    def test_unknown_rejected_as_a_policy(self):
        with self.assertRaises(ValueError):
            _walk_masks(_cells(["o"]), "whatever")


class TestAnomalyHierarchy(unittest.TestCase):
    """块数 / 单格块 / 孤立 / 真被困 的关系与方向。

    图论块数是**最大**的集合，多出来的是"对角有可通行格、而角上被挡"这类**我们自己造的
    孤立**（禁斜穿规则的产物）。``free_walled`` 与 ``free_lonely`` 在**报出的集合**上互斥
    （同一格不该被两条提示各报一次），但**判据**上 ``walled ⇒ 无可行走邻居 ⇒ 单格块``。
    """

    @staticmethod
    def _counts(cells):
        node, guard = _walk_masks(cells, POLICY_NO_UNKNOWN)
        sizes = _components(node, guard)
        facts = _single_cell_facts(cells)
        return {
            "blocks": len(sizes),
            "singletons": sum(1 for size in sizes if size == 1),
            "walled": len(facts["free_walled"]),
            "lonely": len(facts["free_lonely"]),
        }

    @staticmethod
    def _singleton_blocks(cells):
        """图论单格块的坐标集合（与 _components 同规则，但保留坐标）。"""
        node, guard = _walk_masks(cells, POLICY_NO_UNKNOWN)
        height, width = node.shape
        seen = np.zeros((height, width), bool)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
        singles = set()
        for r0, c0 in zip(*np.nonzero(node), strict=True):
            r0, c0 = int(r0), int(c0)
            if seen[r0, c0]:
                continue
            seen[r0, c0] = True
            stack, pts = [(r0, c0)], []
            while stack:
                i, j = stack.pop()
                pts.append((i, j))
                for di, dj in dirs:
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < height and 0 <= nj < width):
                        continue
                    if di and dj and (not guard[i + di, j] or not guard[i, j + dj]):
                        continue
                    if node[ni, nj] and not seen[ni, nj]:
                        seen[ni, nj] = True
                        stack.append((ni, nj))
            if len(pts) == 1:
                singles.add(pts[0])
        return singles

    def test_singleton_blocks_decompose_exactly(self):
        """恒等式：单格块 = 真被困 + 孤立 + 假孤立（逐例成立，不是数据巧合）。

        由此得到可证等价：``单格块 == 被困 + 孤立`` ⟺ 假孤立 == 0。
        注意左边要带被困数——写成"单格块 == 孤立"是另一回事。
        """
        cases = {
            "假孤立(o#/#o)": _cells(["o#", "#o"]),
            "真被困": _cells(["###", "#o#", "###"]),
            "真孤立(o#/#.)": _cells(["o#", "#."]),
            "正常相邻": _cells(["oo"]),
            "对角可走": _cells(["o.", ".o"]),
            "混合": _cells(["o#o", "#.#", "o#."]),
        }
        for name, cells in cases.items():
            with self.subTest(name=name):
                singles = self._singleton_blocks(cells)
                facts = _single_cell_facts(cells)
                walled = {tuple(p) for p in facts["free_walled"]}
                lonely = {tuple(p) for p in facts["free_lonely"]}
                false_iso = singles - walled - lonely
                self.assertEqual(len(singles), len(walled) + len(lonely) + len(false_iso), "分解必须精确相等")
                # 等价的两侧必须同时成立或不成立
                self.assertEqual(len(singles) == len(walled) + len(lonely), len(false_iso) == 0, name)

    def test_every_reported_anomaly_sits_in_a_singleton_block(self):
        """不变量：报出的异常格必然落在单格块里（判据比图连通性弱）。

        一旦破了就是实现漂移：说明提示的格子与图上算的块对不上。
        """
        cases = {
            "真被困": _cells(["###", "#o#", "###"]),
            "真孤立": _cells(["o#", "#."]),
            "假孤立": _cells(["o#", "#o"]),
            "混合": _cells(["o#o", "#.#", "#o#"]),
        }
        for name, cells in cases.items():
            with self.subTest(name=name):
                singles = self._singleton_blocks(cells)
                facts = _single_cell_facts(cells)
                reported = {tuple(p) for p in facts["free_walled"]} | {tuple(p) for p in facts["free_lonely"]}
                self.assertEqual(reported - singles, set(), f"{name}: 有异常格不在单格块里")
                self.assertEqual(
                    {tuple(p) for p in facts["free_walled"]} & {tuple(p) for p in facts["free_lonely"]},
                    set(),
                    f"{name}: 两组必须互斥",
                )

    def test_reported_sets_are_disjoint_and_inside_singleton_blocks(self):
        """报出的两组互斥；并集不超过单格块数。"""
        grids = {
            "中间态(对角相通但两正交被挡)": _cells(["o#", "#o"]),
            "真被困(八邻全阻挡)": _cells(["###", "#o#", "###"]),
            "正常相邻": _cells(["oo"]),
            "孤立但有未知邻居": _cells(["o.", "#."]),
        }
        for name, cells in grids.items():
            with self.subTest(name=name):
                c = self._counts(cells)
                self.assertLessEqual(c["walled"] + c["lonely"], c["singletons"], name)

    def test_walled_cells_are_excluded_from_lonely(self):
        """真被困的格不再出现在孤立里——否则同一格会被两条提示各报一次。"""
        c = self._counts(_cells(["###", "#o#", "###"]))
        self.assertEqual(c["walled"], 1)
        self.assertEqual(c["lonely"], 0, "八邻全阻挡的格被 walled 收走，不该重复出现在 lonely")

    def test_false_isolation_inflates_block_count_without_any_anomaly(self):
        """中间态：图上 2 个单格块，但格子级一条异常都报不出来——块数高估孤立。

        ``o#``/``#o`` 两格对角相通、两个正交格都是阻挡，于是规划器图上互不相连；但每格
        都有一个**可行走**的对角邻居，所以既不是 lone、更不是 trapped。
        """
        c = self._counts(_cells(["o#", "#o"]))
        self.assertEqual(c["singletons"], 2, "图上确实是两个单格块")
        self.assertEqual(c["lonely"], 0, "两格都有可行走邻居，不是孤立")
        self.assertEqual(c["walled"], 0, "更不是真被困")
        self.assertGreater(c["singletons"], c["walled"] + c["lonely"], "块数比格子级事实多，方向是高估")

    def test_equal_singletons_and_lonely_does_not_mean_all_trapped(self):
        """回归：相等**不**意味着"都是真被困"，只意味着没有假孤立。

        实测 base01 就是 8 块 / 2 个单格块 / 2 个孤立 / **0 个真被困**——相等与"有没有真
        被困"是两件事。早先我把这条关系说反过，这里钉住。

        注意选例：``o.``/``.o`` **不是**本例——那两格互为对角可行走邻居，属于**假孤立**
        （单格块 2、孤立 0）。要相等，得是"连对角都没有可行走邻居"的格子。
        """
        c = self._counts(_cells(["o#", "#."]))  # 唯一可行走格的对角是未知格
        self.assertEqual(c["walled"], 0, "邻居里有未知格，不算被围住")
        self.assertEqual(c["singletons"], 1)
        self.assertEqual(c["lonely"], 1, "只剩未知/阻挡邻居")
        self.assertEqual(c["singletons"], c["lonely"], "本例没有假孤立")
        self.assertEqual(c["walled"], 0, "真被困可以是 0，而相等仍成立")

    def test_diagonal_free_neighbour_is_a_false_isolation(self):
        """反例：``o.``/``.o`` 两格互为**对角可行走**邻居 —— 单格块 2、孤立 0。

        这是"单格块数 == 孤立格数"之外的另一半：块数可以比格子级事实多。
        """
        c = self._counts(_cells(["o.", ".o"]))
        self.assertEqual(c["singletons"], 2)
        self.assertEqual(c["lonely"], 0, "两格互为可行走邻居（对角）")
        self.assertEqual(c["walled"], 0)

    def test_lonely_requires_no_free_neighbour(self):
        c = self._counts(_cells(["o.", "#."]))
        self.assertEqual(c["lonely"], 1, "唯一的可行走格只有未知邻居")
        self.assertEqual(c["walled"], 0, "邻居里有未知格，不算被围住")


class TestPathSafety(unittest.TestCase):
    """CLI 输入必须限制在当前工作区内，防止路径穿越读取任意文件。"""

    def test_accepts_relative_path_inside_root(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            resolved = _resolve_within_root(Path("nested") / "grid.json", root)

            self.assertEqual(resolved, (root / "nested" / "grid.json").resolve())

    def test_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()

            with self.assertRaises(ValueError):
                _resolve_within_root(Path("..") / "outside.json", root)

    def test_rejects_absolute_path_outside_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            outside = Path(temporary) / "outside.json"
            root.mkdir()
            outside.write_text("{}", encoding="utf-8")

            with self.assertRaises(ValueError):
                _resolve_within_root(outside, root)


class TestInvalidGridShapes(unittest.TestCase):
    """无效网格只报告原始错误，不应在统计阶段再次崩溃。"""

    def test_report_cells_skips_non_2d_and_empty_arrays(self):
        rep = Report("invalid")
        meta = {"cell_size": 1.0, "origin": (0.0, 0.0, 0.0), "raw": {}}

        report_cells(np.array([0, 1, 2], dtype=np.uint8), meta, rep)
        report_cells(np.empty((0, 2), dtype=np.uint8), meta, rep)

        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.lines, [])

    def test_connectivity_handles_empty_masks(self):
        rep = Report("blocked")
        cells = _cells(["###"])

        report_cells(cells, {"cell_size": 1.0, "origin": (0.0, 0.0, 0.0), "raw": {}}, rep)

        self.assertIn("可行走（不冒险即可达）：0 块", rep.lines)
        self.assertFalse(any("最大占比" in line for line in rep.lines))

    def test_compare_legacy_rejects_grid_shape_mismatch(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            legacy = root / "legacy.json"
            legacy.write_text(
                json.dumps(
                    {
                        "cells": [[0, 0], [1, 0]],
                        "blocked": [],
                        "origin": [0.0, 0.0, 0.0],
                        "cell_size": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            rep = Report("shape")
            cells = _cells(["oo", "oo"])
            meta = {"cell_size": 1.0, "origin": (0.0, 0.0, 0.0), "raw": {}}

            compare_legacy(cells, meta, legacy, rep)

        self.assertTrue(any("新网格形状不符" in error for error in rep.errors))


if __name__ == "__main__":
    unittest.main()
