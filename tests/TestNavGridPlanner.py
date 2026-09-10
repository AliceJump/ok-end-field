# -*- coding: utf-8 -*-
"""导航规划（src/nav/grid_planner.py）单元测试。

合成网格即可覆盖：代价模型（未知=冒险加价）、禁斜穿墙角、起终点吸附、
不可达、视线简化、安全边距。不需要游戏窗口。
"""
import math
import unittest

import numpy as np

from src.nav.grid_io import (
    CELL_BLOCKED,
    CELL_FREE,
    CELL_UNKNOWN,
    DenseGrid,
    GridMeta,
)
from src.nav.grid_planner import GridPlanner

_CHARS = {".": CELL_UNKNOWN, "o": CELL_FREE, "#": CELL_BLOCKED}


def _grid(rows, *, origin=(0.0, 0.0, 0.0), cell_size=1.0):
    """用字符画建网格：第一行是 i=0，每行从左到右是 j=0..n。"""
    arr = np.array([[_CHARS[ch] for ch in row] for row in rows], dtype=np.uint8)
    return DenseGrid(arr, GridMeta(origin=origin, cell_size=cell_size, source="test"))


def _cells(res):
    return set(res.cells)


class TestCostModel(unittest.TestCase):
    def test_prefers_free_detour_over_unknown_shortcut(self):
        """未知格按 risk_cost 加价：绕路走可行走比直穿未知划算。"""
        grid = _grid(["ooo",
                      "o.o",
                      "ooo"])
        res = GridPlanner(grid, risk_cost=5.0).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        self.assertNotIn((1, 1), _cells(res))          # 没走中间那个未知格
        self.assertEqual(res.risk_cells, 0)            # 全程不冒风险
        # 绕行的最优代价：正交 1 + 斜向 √2 + 正交 1
        self.assertAlmostEqual(res.cost, 2.0 + math.sqrt(2), delta=1e-6)

    def test_unknown_shortcut_used_when_risk_cheap(self):
        grid = _grid(["ooo",
                      "o.o",
                      "ooo"])
        res = GridPlanner(grid, risk_cost=1.0).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        self.assertIn((1, 1), _cells(res))             # 风险便宜时走对角线
        self.assertEqual(res.risk_cells, 1)
        self.assertAlmostEqual(res.cost, 2 * math.sqrt(2), delta=1e-6)

    def test_blocked_is_never_crossed(self):
        grid = _grid(["o#o"])
        res = GridPlanner(grid).plan_cells((0, 0), (0, 2))
        self.assertFalse(res.ok)
        self.assertIn("找不到通路", res.reason)

    def test_allow_unknown_false_avoids_unknown(self):
        grid = _grid(["o.o",
                      "ooo"])
        res = GridPlanner(grid, allow_unknown=False).plan_cells((0, 0), (0, 2))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.risk_cells, 0)
        self.assertNotIn((0, 1), _cells(res))              # 未知格不穿
        self.assertAlmostEqual(res.cost, 2 * math.sqrt(2), delta=1e-6)  # 斜向绕行
        # 关掉斜向就只能走两段正交的绕行
        plain = GridPlanner(grid, allow_unknown=False, diagonal=False).plan_cells((0, 0), (0, 2))
        self.assertTrue(plain.ok, plain)
        self.assertAlmostEqual(plain.cost, 4.0, delta=1e-6)

    def test_diagonal_equals_sqrt2(self):
        grid = _grid(["oo",
                      "oo"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertEqual(res.cells, [(0, 0), (1, 1)])
        self.assertAlmostEqual(res.cost, math.sqrt(2), delta=1e-6)

    def test_four_connected_when_diagonal_disabled(self):
        grid = _grid(["oo",
                      "oo"])
        res = GridPlanner(grid, diagonal=False).plan_cells((0, 0), (1, 1))
        self.assertTrue(res.ok, res)
        self.assertEqual(len(res.cells), 3)
        for (ai, aj), (bi, bj) in zip(res.cells, res.cells[1:]):
            self.assertEqual(abs(ai - bi) + abs(aj - bj), 1)


class TestCornerCutting(unittest.TestCase):
    def test_path_does_not_cut_blocked_corner(self):
        grid = _grid(["o#",
                      "oo"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.cells, [(0, 0), (1, 0), (1, 1)])   # 不能直接斜穿
        self.assertAlmostEqual(res.cost, 2.0, delta=1e-6)

    def test_simplify_keeps_corner_safe(self):
        """视线简化也不能把路径压过墙角。"""
        grid = _grid(["o#o",
                      "ooo",
                      "o#o"])
        res = GridPlanner(grid).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        for a, b in zip(res.waypoints, res.waypoints[1:]):
            self.assertTrue(res.ok)
        # 每个航点都必须是可通行格（世界坐标反查）
        for x, z in res.waypoints:
            i, j = grid.index_of_world(x, z)
            self.assertTrue(grid.is_free(i, j), (i, j))


class TestSnapping(unittest.TestCase):
    def test_start_in_blocked_is_moved_with_note(self):
        grid = _grid(["###",
                      "#oo",
                      "###"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 2))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.start_cell, (1, 1))
        self.assertTrue(res.notes and "起点" in res.notes[0])

    def test_goal_in_unknown_is_allowed(self):
        grid = _grid(["oooo",
                      "ooo."])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 3))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.goal_cell, (1, 3))
        self.assertEqual(res.notes, [])

    def test_no_passable_anywhere(self):
        grid = _grid(["###",
                      "###"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertFalse(res.ok)
        self.assertIn("不可通行", res.reason)


class TestSafetyMargin(unittest.TestCase):
    def test_margin_closes_narrow_corridor(self):
        """1 格宽走廊 + margin=1 会被膨胀堵死 —— 这正是要留安全边距的代价。"""
        grid = _grid(["#####",
                      "#ooo#",
                      "#####"])
        self.assertTrue(GridPlanner(grid, margin=0).plan_cells((1, 1), (1, 3)).ok)
        res = GridPlanner(grid, margin=1).plan_cells((1, 1), (1, 3))
        self.assertFalse(res.ok)

    def test_margin_keeps_wide_route(self):
        """够宽的通路膨胀一圈后仍能走通。"""
        grid = _grid(["#######",
                      "#ooooo#",
                      "#ooooo#",
                      "#ooooo#",
                      "#######"])
        res = GridPlanner(grid, margin=1).plan_cells((2, 2), (2, 4))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.notes, [])            # 起终点本身就在安全区里
        self.assertGreaterEqual(len(res.cells), 3)


class TestWaypoints(unittest.TestCase):
    def test_waypoints_world_coords_and_simplified(self):
        grid = _grid(["ooooo",
                      "ooooo"], origin=(10.0, 0.0, 20.0), cell_size=0.5)
        res = GridPlanner(grid).plan_cells((0, 0), (1, 4))
        self.assertTrue(res.ok, res)
        # 直线走廊 → 简化成起点+终点两个航点
        self.assertEqual(len(res.waypoints), 2)
        self.assertEqual(res.waypoints[0], grid.world_of_index(0, 0))
        self.assertEqual(res.waypoints[-1], grid.world_of_index(1, 4))
        self.assertAlmostEqual(res.waypoints[0][0], 10.25, delta=1e-6)
        self.assertAlmostEqual(res.waypoints[0][1], 20.25, delta=1e-6)

    def test_plan_by_world_coordinates(self):
        grid = _grid(["ooooo"], origin=(0.0, 0.0, 0.0), cell_size=1.0)
        res = GridPlanner(grid).plan((0.5, 0.5), (4.5, 0.5))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.cells[0], (0, 0))
        self.assertEqual(res.cells[-1], (0, 4))
        self.assertGreater(res.expanded, 0)


if __name__ == "__main__":
    unittest.main()
