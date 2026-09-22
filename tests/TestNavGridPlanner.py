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
from src.nav.zip_line_graph import ZipLineGraph, ZipLineLink, ZipLineNode

_CHARS = {".": CELL_UNKNOWN, "o": CELL_FREE, "#": CELL_BLOCKED}


def _grid(rows, *, origin=(0.0, 0.0, 0.0), cell_size=1.0):
    """用字符画建网格：第一行是 i=0，每行从左到右是 j=0..n。"""
    arr = np.array([[_CHARS[ch] for ch in row] for row in rows], dtype=np.uint8)
    return DenseGrid(arr, GridMeta(origin=origin, cell_size=cell_size, source="test"))


def _cells(res):
    return set(res.cells)


class TestCostModel(unittest.TestCase):
    """覆盖未知格、墙距和前沿的代价模型。"""

    def test_prefers_free_detour_over_unknown_shortcut(self):
        """未知格按 risk_cost 加价：绕路走可行走比直穿未知划算。"""
        grid = _grid(["ooo", "o.o", "ooo"])
        res = GridPlanner(grid, risk_cost=5.0).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        self.assertNotIn((1, 1), _cells(res))  # 没走中间那个未知格
        self.assertEqual(res.risk_cells, 0)  # 全程不冒风险
        # 绕行的最优代价：正交 1 + 斜向 √2 + 正交 1
        self.assertAlmostEqual(res.cost, 2.0 + math.sqrt(2), delta=1e-6)

    def test_unknown_shortcut_used_when_risk_cheap(self):
        grid = _grid(["ooo", "o.o", "ooo"])
        res = GridPlanner(grid, risk_cost=1.0).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        self.assertIn((1, 1), _cells(res))  # 风险便宜时走对角线
        self.assertEqual(res.risk_cells, 1)
        self.assertAlmostEqual(res.cost, 2 * math.sqrt(2), delta=1e-6)

    def test_blocked_is_never_crossed(self):
        grid = _grid(["o#o"])
        res = GridPlanner(grid).plan_cells((0, 0), (0, 2))
        self.assertFalse(res.ok)
        self.assertIn("找不到通路", res.reason)

    def test_allow_unknown_false_treats_unknown_as_blocked(self):
        """未知格视同阻挡：既不穿未知格，也不斜穿未知格的角。"""
        grid = _grid(["o.o", "ooo"])
        res = GridPlanner(grid, allow_unknown=False).plan_cells((0, 0), (0, 2))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.risk_cells, 0)
        self.assertNotIn((0, 1), _cells(res))  # 未知格不穿
        # 斜步 (0,0)->(1,1) 的角 (0,1) 是未知格，视同阻挡 -> 不许切角，
        # 只能绕：(0,0)->(1,0)->(1,1)->(1,2)->(0,2)，代价 4
        self.assertAlmostEqual(res.cost, 4.0, delta=1e-6)
        # 关掉斜向走法结果一样：斜步本来就被禁光了
        plain = GridPlanner(grid, allow_unknown=False, diagonal=False).plan_cells((0, 0), (0, 2))
        self.assertTrue(plain.ok, plain)
        self.assertAlmostEqual(plain.cost, 4.0, delta=1e-6)

    def test_allow_unknown_true_may_cut_unknown_corner(self):
        """允许穿越未知格时，未知格的角可以切（规则与 allow_unknown 一致，不是两套）。"""
        grid = _grid(["o.o", "ooo"])
        res = GridPlanner(grid, allow_unknown=True).plan_cells((0, 0), (0, 2))
        self.assertTrue(res.ok, res)
        self.assertAlmostEqual(res.cost, 2 * math.sqrt(2), delta=1e-6)  # 两个斜步

    def test_diagonal_equals_sqrt2(self):
        grid = _grid(["oo", "oo"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertEqual(res.cells, [(0, 0), (1, 1)])
        self.assertAlmostEqual(res.cost, math.sqrt(2), delta=1e-6)

    def test_four_connected_when_diagonal_disabled(self):
        grid = _grid(["oo", "oo"])
        res = GridPlanner(grid, diagonal=False).plan_cells((0, 0), (1, 1))
        self.assertTrue(res.ok, res)
        self.assertEqual(len(res.cells), 3)
        for (ai, aj), (bi, bj) in zip(res.cells, res.cells[1:], strict=False):
            self.assertEqual(abs(ai - bi) + abs(aj - bj), 1)

    def test_frontier_penalty_adds_cost_near_unknown(self):
        grid = _grid(["o.o", "ooo"])
        planner = GridPlanner(grid, frontier_margin=2, frontier_penalty=3.0)

        self.assertAlmostEqual(
            planner._step_cost(1, 0, 0, 0, CELL_FREE),
            4.0,
        )
        self.assertAlmostEqual(
            planner._step_cost(0, 0, 1, 0, CELL_FREE),
            1.0,
        )


class TestCornerCutting(unittest.TestCase):
    """验证规划器不会斜穿墙角。"""

    def test_path_does_not_cut_blocked_corner(self):
        grid = _grid(["o#", "oo"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.cells, [(0, 0), (1, 0), (1, 1)])  # 不能直接斜穿
        self.assertAlmostEqual(res.cost, 2.0, delta=1e-6)

    def test_simplify_keeps_corner_safe(self):
        """视线简化也不能把路径压过墙角。"""
        grid = _grid(["o#o", "ooo", "o#o"])
        res = GridPlanner(grid).plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        for _a, _b in zip(res.waypoints, res.waypoints[1:], strict=False):
            self.assertTrue(res.ok)
        # 每个航点都必须是可通行格（世界坐标反查）
        for x, z in res.waypoints:
            i, j = grid.index_of_world(x, z)
            self.assertTrue(grid.is_free(i, j), (i, j))


class TestSnapping(unittest.TestCase):
    """覆盖不可通行起终点的吸附行为。"""

    def test_start_in_blocked_is_moved_with_note(self):
        grid = _grid(["###", "#oo", "###"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 2))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.start_cell, (1, 1))
        self.assertTrue(res.notes and "起点" in res.notes[0])

    def test_goal_in_unknown_is_allowed(self):
        grid = _grid(["oooo", "ooo."])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 3))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.goal_cell, (1, 3))
        self.assertEqual(res.notes, [])

    def test_no_passable_anywhere(self):
        grid = _grid(["###", "###"])
        res = GridPlanner(grid).plan_cells((0, 0), (1, 1))
        self.assertFalse(res.ok)
        self.assertIn("不可通行", res.reason)


class TestSafetyMargin(unittest.TestCase):
    """验证墙距参数只影响偏好、不封死窄路。"""

    def test_margin_keeps_only_dangerous_route_available(self):
        """窄路不满足离墙偏好时仍可通行；没有替代路线时必须继续规划。"""
        grid = _grid(["#####", "#ooo#", "#####"])
        res = GridPlanner(grid, margin=2, wall_penalty=5.0).plan_cells((1, 1), (1, 3))
        self.assertTrue(res.ok, res)

    def test_wall_penalty_adds_cost_without_blocking(self):
        """离墙不足只增加进入该格的代价，不会把格子改成不可通行。"""
        grid = _grid(["#####", "#ooo#", "#ooo#", "#ooo#", "#####"])
        planner = GridPlanner(grid, margin=2, wall_penalty=3.0)

        self.assertTrue(planner._passable(1, 2))
        self.assertAlmostEqual(
            planner._step_cost(1, 1, 1, 2, CELL_FREE),
            4.0,
        )
        self.assertAlmostEqual(
            planner._step_cost(2, 1, 2, 2, CELL_FREE),
            1.0,
        )


class TestWaypoints(unittest.TestCase):
    """覆盖航点简化与替代线安全性。"""

    def test_waypoints_world_coords_and_simplified(self):
        grid = _grid(["ooooo", "ooooo"], origin=(10.0, 0.0, 20.0), cell_size=0.5)
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

    def test_waypoints_can_form_arbitrary_angle_segments(self):
        grid = _grid(["oooooo", "oooooo", "oooooo", "oooooo"])
        res = GridPlanner(grid).plan((0.5, 0.5), (4.5, 2.5))
        self.assertTrue(res.ok, res)
        self.assertEqual(len(res.waypoints), 2)
        start_x, start_z = res.waypoints[0]
        end_x, end_z = res.waypoints[-1]
        self.assertNotEqual(abs(end_x - start_x), abs(end_z - start_z))
        self.assertNotEqual(start_z, end_z)


class TestZipLinePlanning(unittest.TestCase):
    """验证滑索边能跨过普通网格不可达的阻挡区域。"""

    def test_zip_line_connects_two_walkable_islands(self):
        grid = _grid(["oo#oo"])
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.2, 0.0, 0.2)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 4.2, 0.0, 0.2)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=4.0, max_range_m=80.0)],
        )

        blocked = GridPlanner(grid).plan_cells((0, 0), (0, 4))
        self.assertFalse(blocked.ok)

        result = GridPlanner(grid, zip_lines=graph).plan_cells((0, 0), (0, 4))

        self.assertTrue(result.ok, result)
        self.assertEqual(
            GridPlanner(grid, zip_lines=graph).zip_line_stats(),
            {
                "nodes": 2,
                "links": 1,
                "mapped_nodes": 2,
                "directed_edges": 2,
            },
        )
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(result.cells, [(0, 0), (0, 4)])
        route_step = result.zip_line_steps[0]
        self.assertEqual(route_step.entry_cell, (0, 0))
        self.assertEqual(route_step.exit_cell, (0, 4))
        self.assertEqual(result.waypoints[route_step.entry_waypoint_index], first.xz)
        self.assertEqual(result.waypoints[route_step.exit_waypoint_index], second.xz)

    def test_required_zip_start_forces_first_move_to_zip_line(self):
        grid = _grid(["ooo"])
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 2.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=2.0, max_range_m=80.0)],
        )
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_cost_factor=100.0,
            zip_line_boarding_cost=100.0,
        )

        walking = planner.plan_cells((0, 0), (0, 2))
        forced = planner.plan_cells(
            (0, 0),
            (0, 2),
            required_zip_line_start_id="a",
        )

        self.assertTrue(walking.ok, walking)
        self.assertEqual(walking.zip_line_steps, [])
        self.assertTrue(forced.ok, forced)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in forced.zip_line_steps[0].steps],
            [("a", "b")],
        )

    def test_unmapped_required_start_continues_along_zip_line_graph(self):
        grid = _grid(["###oo"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        target = ZipLineNode("target", "test", "lv1", "滑索架", 3.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [source, target],
            [ZipLineLink("source", "target", distance_m=3.0, max_range_m=80.0)],
        )
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
        )

        result = planner.plan_cells(
            (0, 0),
            (0, 4),
            required_zip_line_start_id="source",
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("source", "target")],
        )
        self.assertEqual(result.waypoints[0], source.xz)
        self.assertEqual(result.waypoints[1], target.xz)

    def test_unmapped_prefix_merges_with_immediate_following_zip_edge(self):
        grid = _grid(["###o###o"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        target = ZipLineNode("target", "test", "lv1", "滑索架", 3.5, 0.0, 0.5)
        final = ZipLineNode("final", "test", "lv1", "滑索架", 7.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [source, target, final],
            [
                ZipLineLink("source", "target", distance_m=3.0, max_range_m=80.0),
                ZipLineLink("target", "final", distance_m=6.0, max_range_m=80.0),
            ],
        )
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
        )

        result = planner.plan_cells(
            (0, 0),
            (0, 7),
            required_zip_line_start_id="source",
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(len(result.zip_line_steps), 1)
        route_step = result.zip_line_steps[0]
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in route_step.steps],
            [("source", "target"), ("target", "final")],
        )
        self.assertEqual(route_step.entry_waypoint_index, 0)
        self.assertEqual(result.waypoints[route_step.entry_waypoint_index], source.xz)
        self.assertEqual(result.waypoints[route_step.exit_waypoint_index], final.xz)

    def test_unmapped_start_uses_complete_route_without_returning_to_nearest_mapped_node(self):
        grid = _grid(["###o###o"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        nearest = ZipLineNode("nearest", "test", "lv1", "滑索架", 3.5, 0.0, 0.5)
        relay = ZipLineNode("relay", "test", "lv1", "滑索架", 5.5, 0.0, 0.5)
        final = ZipLineNode("final", "test", "lv1", "滑索架", 7.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [source, nearest, relay, final],
            [
                ZipLineLink("source", "nearest", distance_m=3.0, max_range_m=80.0),
                ZipLineLink("source", "relay", distance_m=4.0, max_range_m=80.0),
                ZipLineLink("relay", "final", distance_m=5.0, max_range_m=80.0),
            ],
        )
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
        )

        result = planner.plan_cells(
            (0, 0),
            (0, 7),
            required_zip_line_start_id="source",
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("source", "relay"), ("relay", "final")],
        )

    def test_unmapped_intermediate_zipline_is_traversed_as_chain(self):
        width = 61
        cells = np.full((1, width), CELL_BLOCKED, dtype=np.uint8)
        cells[0, :2] = CELL_FREE
        cells[0, -2:] = CELL_FREE
        grid = DenseGrid(cells, GridMeta(origin=(0.0, 0.0, 0.0), cell_size=1.0))
        first = ZipLineNode("a", "test", "lv1", "长距滑索架", 0.5, 0.0, 0.5)
        middle = ZipLineNode("b", "test", "lv1", "长距滑索架", 30.5, 0.0, 0.5)
        last = ZipLineNode("c", "test", "lv1", "长距滑索架", 59.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, middle, last],
            [
                ZipLineLink("a", "b", distance_m=30.0, max_range_m=110.0),
                ZipLineLink("b", "c", distance_m=29.0, max_range_m=110.0),
            ],
        )

        planner = GridPlanner(grid, zip_lines=graph)
        result = planner.plan_cells((0, 0), (0, width - 1))

        self.assertTrue(result.ok, result)
        self.assertEqual(planner.zip_line_stats()["mapped_nodes"], 2)
        self.assertEqual(planner.zip_line_stats()["directed_edges"], 2)
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("a", "b"), ("b", "c")],
        )


class TestFailureDiagnostics(unittest.TestCase):
    """覆盖规划失败原因和诊断字段。"""

    """失败原因必须能区分「策略性不可达」（关了穿越未知格）和「真被阻挡隔断」。"""

    def test_unknown_disabled_reason_names_the_switch(self):
        grid = _grid(["o#o"])
        res = GridPlanner(grid, allow_unknown=False).plan_cells((0, 0), (0, 2))
        self.assertFalse(res.ok)
        self.assertIn("允许穿越未知格", res.reason)
        self.assertFalse(res.cap_exceeded)

    def test_blocked_separation_reason_says_exhausted(self):
        grid = _grid(["o#o"])
        res = GridPlanner(grid, allow_unknown=True).plan_cells((0, 0), (0, 2))
        self.assertFalse(res.ok)
        self.assertIn("穷尽", res.reason)
        self.assertNotIn("允许穿越未知格", res.reason)

    def test_cap_exceeded_is_flagged_and_reported(self):
        grid = _grid(["oooooooo"])
        res = GridPlanner(grid, max_expand=1).plan_cells((0, 0), (0, 7))
        self.assertFalse(res.ok)
        self.assertTrue(res.cap_exceeded)
        self.assertIn("搜索规模超限", res.reason)

    def test_time_budget_is_reported_separately(self):
        grid = _grid(["oooooooo"])
        res = GridPlanner(grid).plan_cells((0, 0), (0, 7), time_budget_s=0.0)
        self.assertFalse(res.ok)
        self.assertTrue(res.timed_out)
        self.assertFalse(res.cap_exceeded)
        self.assertIn("规划超时", res.reason)

    def test_snap_note_names_state_and_offset(self):
        """起点落在未知格并被挪走时，note 要说清原格状态和挪了多远。"""
        grid = _grid(["o#."])
        res = GridPlanner(grid, allow_unknown=False).plan_cells((0, 2), (0, 0))
        self.assertTrue(res.ok, res)
        self.assertTrue(res.notes, res.notes)
        self.assertIn("未知格", res.notes[0])
        self.assertIn("偏移 2 格", res.notes[0])


class TestAdaptiveWeight(unittest.TestCase):
    """覆盖触顶后的加权降级搜索。"""

    """精确搜索触顶后按 risk_cost 加权重搜：可行时保持最优，不可行时至少出路径。

    用的是全未知格的大片区域：未知格步进代价是 risk_cost 倍，而启发式只按最小代价 1
    估，所以精确搜索会像 Dijkstra 一样铺开；加权后直奔目标。机制与真实大图
    （``map02_4``）上一样，只是规模小到可以放进单测。
    """

    @staticmethod
    def _open_unknown(size=20):
        return _grid(["." * size] * size)

    def test_retry_after_cap_finds_route_and_notes_degradation(self):
        # 精确搜索要展开几百格才到目标，把预算卡在中间即可稳定触发降级
        res = GridPlanner(self._open_unknown(), risk_cost=5.0, max_expand=50).plan_cells((0, 0), (19, 19))
        self.assertTrue(res.ok, res)
        self.assertFalse(res.cap_exceeded)
        self.assertTrue(any("加权" in note for note in res.notes), res.notes)

    def test_exact_search_wins_when_budget_fits(self):
        res = GridPlanner(self._open_unknown(), risk_cost=5.0, max_expand=400_000).plan_cells((0, 0), (19, 19))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.notes, [])  # 没触发降级
        self.assertFalse(res.cap_exceeded)

    def test_no_retry_when_failure_is_exhaustion_not_cap(self):
        """真被阻挡隔断（可达区域穷尽）时不该白白重搜一次。"""
        grid = _grid(["o#o"])
        res = GridPlanner(grid, allow_unknown=True).plan_cells((0, 0), (0, 2))
        self.assertFalse(res.ok)
        self.assertNotIn("加权", res.reason)
        self.assertEqual(res.notes, [])

    def test_adaptive_weight_can_be_disabled(self):
        res = GridPlanner(self._open_unknown(), risk_cost=5.0, max_expand=50, adaptive_weight=False).plan_cells(
            (0, 0), (19, 19)
        )
        self.assertFalse(res.ok)
        self.assertTrue(res.cap_exceeded)
        self.assertIn("搜索规模超限", res.reason)

    def test_weighted_route_is_still_wall_and_corner_safe(self):
        """加权只改扩展顺序：路线依旧不穿阻挡格、不切阻挡角。"""
        grid = _grid(["." * 3, "." * 3, ".#."])
        planner = GridPlanner(grid, risk_cost=5.0, max_expand=6)
        res = planner.plan_cells((0, 0), (2, 2))
        self.assertTrue(res.ok, res)
        self.assertTrue(any("加权" in note for note in res.notes), res.notes)
        for i, j in res.cells:
            self.assertTrue(planner.passable(i, j), (i, j))
        for (ai, aj), (bi, bj) in zip(res.cells, res.cells[1:], strict=False):
            if ai != bi and aj != bj:
                self.assertTrue(planner._corner_ok(ai, aj, bi - ai, bj - aj), (ai, aj, bi, bj))


if __name__ == "__main__":
    unittest.main()
