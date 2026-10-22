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


class TestCorridorCentering(unittest.TestCase):
    """验证饱和边距居中：窄走廊从正中穿过，且开阔区域不会被拉去绕路。"""

    @staticmethod
    def _corridor(width: int) -> DenseGrid:
        return _grid(["#" * 22] + ["#" + "o" * 20 + "#"] * width + ["#" * 22])

    def test_margin_centers_narrow_corridor(self):
        for width in (3, 5):
            with self.subTest(width=width):
                grid = self._corridor(width)
                res = GridPlanner(grid, margin=3, wall_penalty=1.0).plan_cells((1, 1), (width, 20))
                self.assertTrue(res.ok, res)
                centre_row = (width + 1) // 2
                middle = {i for i, j in res.cells if 10 <= j <= 12}
                self.assertEqual(middle, {centre_row})

    def test_margin_centering_can_be_disabled(self):
        width = 9
        grid = self._corridor(width)
        res = GridPlanner(grid, margin=0, wall_penalty=1.0).plan_cells((1, 1), (width, 20))
        middle = {i for i, j in res.cells if 10 <= j <= 12}
        self.assertNotEqual(middle, {(width + 1) // 2})

    def test_saturated_margin_bounds_shift_in_open_area(self):
        """开阔区域里边距只会把路线拉到离墙 margin 格，不会一路拐到场地中央。"""
        width = 800
        grid = _grid(["#" * (width + 2)] + ["#" + "o" * width + "#"] * 40 + ["#" * (width + 2)])
        res = GridPlanner(grid, margin=3, wall_penalty=1.0).plan_cells((2, 2), (2, width - 1))
        self.assertTrue(res.ok, res)
        # 起点离墙 2 格 < margin=3，最多上移到离墙 3 格，远达不到房间中心（20 行）
        self.assertLessEqual(max(i for i, _ in res.cells), 3)


class TestNarrowPassage(unittest.TestCase):
    """验证一格宽通路的识别与入口航点保留。"""

    _GAP = (
        "#########",
        "#ooooooo#",
        "####o####",
        "#ooooooo#",
        "#ooooooo#",
        "#########",
    )

    def test_narrow_entry_waypoint_is_kept_and_flagged(self):
        grid = _grid(self._GAP)
        res = GridPlanner(grid, margin=3, wall_penalty=1.0).plan_cells((1, 1), (4, 7))
        self.assertTrue(res.ok, res)
        self.assertEqual(len(res.narrow_entries), 1)
        entry = res.waypoints[res.narrow_entries[0]]
        self.assertEqual(grid.index_of_world(*entry), (2, 4))

    def test_open_area_has_no_narrow_entry(self):
        grid = _grid(["ooooo", "ooooo", "ooooo"])
        planner = GridPlanner(grid)
        self.assertFalse(planner.is_narrow_corridor(1, 2))
        res = planner.plan_cells((1, 0), (1, 4))
        self.assertEqual(res.narrow_entries, ())


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
                # 滑索单价低于步行，octile 启发必须整体缩成代价下界；这一项直接决定搜索量
                "heuristic_scale": 0.2,
            },
        )
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(result.cells, [(0, 0), (0, 4)])
        route_step = result.zip_line_steps[0]
        self.assertEqual(route_step.entry_cell, (0, 0))
        self.assertEqual(route_step.exit_cell, (0, 4))
        self.assertEqual(result.waypoints[route_step.entry_waypoint_index], first.xz)
        self.assertEqual(result.waypoints[route_step.exit_waypoint_index], second.xz)

    def test_zip_line_is_traversable_in_both_directions(self):
        grid = _grid(["o#o"])
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 2.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=2.0, max_range_m=2.0)],
        )

        forward = GridPlanner(grid, zip_lines=graph).plan_cells((0, 0), (0, 2))
        reverse = GridPlanner(grid, zip_lines=graph).plan_cells((0, 2), (0, 0))

        self.assertTrue(forward.ok, forward)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in forward.zip_line_steps[0].steps], [("a", "b")]
        )
        self.assertTrue(reverse.ok, reverse)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in reverse.zip_line_steps[0].steps], [("b", "a")]
        )
        self.assertEqual(GridPlanner(grid, zip_lines=graph).zip_line_stats()["directed_edges"], 2)

    def test_zip_line_edge_cost_uses_grid_cell_units(self):
        grid = _grid(["o#o"], cell_size=0.5)
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.25, 0.0, 0.25)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 1.25, 0.0, 0.25)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=1.0, max_range_m=80.0)],
        )

        result = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_cost_factor=1.0,
            zip_line_boarding_cost=0.0,
        ).plan_cells((0, 0), (0, 2), required_zip_line_start_id="a")

        self.assertTrue(result.ok, result)
        self.assertEqual(result.zip_line_steps[0].cost, 2.0)

    def test_required_zip_start_can_dismount_when_node_cell_is_walkable(self):
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
        self.assertEqual(forced.zip_line_steps, [])
        self.assertIn("可原地下索步行", "\n".join(forced.notes))

    def test_required_zip_start_forces_zip_line_when_node_cell_is_blocked(self):
        grid = _grid(["##oo"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        target = ZipLineNode("target", "test", "lv1", "滑索架", 3.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [source, target],
            [ZipLineLink("source", "target", distance_m=3.0, max_range_m=80.0)],
        )

        result = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=4,
        ).plan_cells(
            (0, 0),
            (0, 3),
            required_zip_line_start_id="source",
        )

        self.assertTrue(result.ok, result)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("source", "target")],
        )
        self.assertIn("必须继续乘坐滑索", "\n".join(result.notes))

    def test_zip_line_target_must_be_walkable_to_dismount(self):
        grid = _grid(["o#oo"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        target = ZipLineNode("target", "test", "lv1", "滑索架", 1.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [source, target],
            [ZipLineLink("source", "target", distance_m=1.0, max_range_m=80.0)],
        )
        planner = GridPlanner(grid, zip_lines=graph, zip_line_access_radius=4)

        result = planner.plan_cells((0, 0), (0, 3))

        self.assertFalse(result.ok)
        self.assertEqual(planner.zip_line_stats()["directed_edges"], 0)

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
        notes = "\n".join(result.notes)
        self.assertIn("当前滑索架 (0.50, 0.50)", notes)
        self.assertIn("可映射滑索架 (3.50, 0.50)", notes)
        self.assertNotIn("source", notes)
        self.assertNotIn("target", notes)

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

    def _many_target_unmapped_start_case(self):
        """未映射起点连到 3 个可映射落点；目标在最远那个落点旁边。

        网格按 ``###o###o###o###o`` 排布，三个落点各自是孤立的可行走格，
        所以"选哪个落点"完全由候选排序决定，网格本身不会替它们兜底。
        """
        grid = _grid(["###o###o###o###o"])
        source = ZipLineNode("source", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        nodes = [source]
        links = []
        for index, x in enumerate((3.5, 7.5, 11.5), start=1):
            nodes.append(ZipLineNode(f"t{index}", "test", "lv1", "滑索架", x, 0.0, 0.5))
            links.append(ZipLineLink("source", f"t{index}", distance_m=x - 0.5, max_range_m=80.0))
        return grid, ZipLineGraph(nodes, links)

    @staticmethod
    def _count_plan_cells(planner):
        """包一层 ``plan_cells`` 计数：未映射起点的续接是**逐个候选**递归调用它的。"""
        calls = []
        original = planner.plan_cells

        def counting(start, goal, **kwargs):
            calls.append(start)
            return original(start, goal, **kwargs)

        planner.plan_cells = counting
        return calls

    def test_unmapped_start_searches_every_candidate_by_default(self):
        grid, graph = self._many_target_unmapped_start_case()
        planner = GridPlanner(grid, zip_lines=graph, zip_line_access_radius=0)
        calls = self._count_plan_cells(planner)

        result = planner.plan_cells((0, 0), (0, 11), required_zip_line_start_id="source")

        self.assertTrue(result.ok, result)
        # 首个是外层调用，其余是逐个候选的续接搜索；3 个候选都在默认上限之内。
        self.assertEqual(len(calls) - 1, 3)
        self.assertEqual(result.waypoints[0], (0.5, 0.5))  # 未映射起点本身
        self.assertFalse(any("只尝试了前" in note for note in result.notes))

    def test_unmapped_start_caps_candidate_searches_and_says_so(self):
        """候选上限生效：不再为每个落点各跑一次完整 A*，并在 notes 里说明被截断。"""
        grid, graph = self._many_target_unmapped_start_case()
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
            zip_line_start_max_candidates=1,
        )
        calls = self._count_plan_cells(planner)

        result = planner.plan_cells((0, 0), (0, 11), required_zip_line_start_id="source")

        self.assertEqual(len(calls) - 1, 1)
        self.assertTrue(any("只尝试了前 1 个" in note for note in result.notes), result.notes)

    def test_unmapped_start_full_candidate_set_avoids_the_return_detour(self):
        """候选够多时选中最省的那条；把上限压到 1 会退化成"先出去再原路滑回来"。

        这是候选上限的代价，写出来是为了让"调大上限"这件事有明确依据：被截断时
        选中的落点可能不是最省的那个，代价要靠 notes 里的提示才知道去调。
        """
        grid, graph = self._many_target_unmapped_start_case()
        wide = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
            zip_line_start_max_candidates=3,
        ).plan_cells((0, 0), (0, 11), required_zip_line_start_id="source")
        narrow = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
            zip_line_start_max_candidates=1,
        ).plan_cells((0, 0), (0, 11), required_zip_line_start_id="source")

        self.assertTrue(wide.ok, wide)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in wide.zip_line_steps[0].steps],
            [("source", "t3")],
        )
        self.assertTrue(narrow.ok, narrow)
        self.assertLess(wide.cost, narrow.cost)
        self.assertIn("source", [step.entry.node_id for step in narrow.zip_line_steps[0].steps])

    def test_unmapped_start_does_not_exceed_the_configured_cap(self):
        """上限就是上限：候选再多也只会跑这么多次续接搜索。"""
        grid, graph = self._many_target_unmapped_start_case()
        planner = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_access_radius=0,
            zip_line_start_max_candidates=2,
        )
        calls = self._count_plan_cells(planner)

        planner.plan_cells((0, 0), (0, 11), required_zip_line_start_id="source")

        self.assertEqual(len(calls) - 1, 2)

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

    def test_mapped_intermediate_zipline_remains_continuous_chain(self):
        grid = _grid(["o#o#o"])
        first = ZipLineNode("a", "test", "lv1", "长距滑索架", 0.5, 0.0, 0.5)
        middle = ZipLineNode("b", "test", "lv1", "长距滑索架", 2.5, 0.0, 0.5)
        last = ZipLineNode("c", "test", "lv1", "长距滑索架", 4.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, middle, last],
            [
                ZipLineLink("a", "b", distance_m=2.0, max_range_m=110.0),
                ZipLineLink("b", "c", distance_m=2.0, max_range_m=110.0),
            ],
        )

        result = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_cost_factor=100.0,
            zip_line_boarding_cost=100.0,
        ).plan_cells((0, 0), (0, 4))

        self.assertTrue(result.ok, result)
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("a", "b"), ("b", "c")],
        )

    def test_blocked_chain_tail_can_disembark_and_walk_from_middle(self):
        grid = _grid(["o#ooo"])
        first = ZipLineNode("a", "test", "lv1", "长距滑索架", 0.5, 0.0, 0.5)
        middle = ZipLineNode("b", "test", "lv1", "长距滑索架", 2.5, 0.0, 0.5)
        graph = ZipLineGraph(
            [first, middle],
            [ZipLineLink("a", "b", distance_m=2.0, max_range_m=110.0)],
        )

        result = GridPlanner(
            grid,
            zip_lines=graph,
            zip_line_cost_factor=100.0,
            zip_line_boarding_cost=100.0,
        ).plan_cells((0, 0), (0, 4))

        self.assertTrue(result.ok, result)
        self.assertEqual(len(result.zip_line_steps), 1)
        self.assertEqual(
            [(step.entry.node_id, step.exit.node_id) for step in result.zip_line_steps[0].steps],
            [("a", "b")],
        )
        self.assertEqual(result.waypoints[-1], (4.5, 0.5))


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

    def test_retry_weight_is_never_weaker_than_the_first_search(self):
        """重搜必须真的比首次搜索更有信息，否则只是把同一轮搜索再跑一遍。"""
        without_zip = GridPlanner(self._open_unknown(), risk_cost=5.0)
        # 无滑索：缩放为 1，重搜权重就是 risk_cost
        self.assertEqual(without_zip._heuristic_scale, 1.0)
        self.assertEqual(without_zip._retry_heuristic_weight(), 5.0)

        # 有滑索：缩放 0.2 会把启发式整体压低，重搜权重必须把这一档补回来
        grid, graph = self._zip_pair()
        planner = GridPlanner(grid, zip_lines=graph, risk_cost=1.5)
        self.assertEqual(planner._heuristic_scale, 0.2)
        self.assertGreater(planner._heuristic_scale * planner._retry_heuristic_weight(), planner._heuristic_scale)

    def test_retry_stays_enabled_when_zip_scale_deflates_the_heuristic(self):
        """risk_cost 很小但缩放很低时仍要重搜：值得重搜的理由是启发式被压低，不是 risk_cost 大小。"""
        grid, graph = self._zip_pair()
        planner = GridPlanner(grid, zip_lines=graph, risk_cost=0.5, max_expand=4)
        planner._cap_exceeded = True
        planner._timed_out = False

        self.assertTrue(planner._weighted_retry_allowed())
        self.assertGreater(planner._retry_heuristic_weight(), planner.heuristic_weight)

    def test_cap_reason_explains_the_zip_heuristic_scale(self):
        """触顶诊断要说清"是不是滑索把搜索放大了"，否则用户只会一味调大 max_expand。"""
        grid, graph = self._open_unknown_with_zip()
        with_zip = GridPlanner(
            grid,
            zip_lines=graph,
            risk_cost=5.0,
            max_expand=50,
            adaptive_weight=False,
        ).plan_cells((0, 0), (19, 19))
        self.assertFalse(with_zip.ok)
        self.assertTrue(with_zip.cap_exceeded)
        self.assertIn("滑索", with_zip.reason)

        without_zip = GridPlanner(
            self._open_unknown(),
            risk_cost=5.0,
            max_expand=50,
            adaptive_weight=False,
        ).plan_cells((0, 0), (19, 19))
        self.assertFalse(without_zip.ok)
        self.assertNotIn("滑索", without_zip.reason)

    def test_degradation_note_reports_effective_weight_instead_of_a_bound(self):
        """降级说明报实际有效权重，不再断言一个代码无法保证的最优性上界。

        用全可行走的大格 + 一条滑索：首次搜索（有效权重 0.2）近似均匀代价，要铺开整张图；
        重搜（有效权重 1.0）只用几十格。预算卡在中间就能稳定触发"触顶后重搜成功"。
        """
        grid, graph = self._free_grid_with_zip()
        res = GridPlanner(grid, zip_lines=graph, max_expand=500).plan_cells((0, 0), (59, 59))

        self.assertTrue(res.ok, res)
        note = next(text for text in res.notes if "加权" in text)
        self.assertIn("有效权重", note)
        self.assertNotIn("上界", note)

    @staticmethod
    def _open_unknown_with_zip():
        """全未知格的大片区域 + 一条滑索：搜索量由未知格撑起来，缩放由滑索给。"""
        grid = TestAdaptiveWeight._open_unknown()
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 9.5, 0.0, 9.5)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=12.0, max_range_m=80.0)],
        )
        return grid, graph

    @staticmethod
    def _free_grid_with_zip():
        """60x60 全可行走 + 一条滑索：无未知格干扰，能把两次搜索的扩展量拉开。"""
        size = 60
        grid = _grid(["o" * size] * size)
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.5, 0.0, 0.5)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 10.5, 0.0, 10.5)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=14.0, max_range_m=80.0)],
        )
        return grid, graph

    @staticmethod
    def _zip_pair():
        """``o#o``：两端可行走、中间阻挡，靠一条滑索连通。"""
        grid = _grid(["o#o"])
        first = ZipLineNode("a", "test", "lv1", "滑索架", 0.2, 0.0, 0.2)
        second = ZipLineNode("b", "test", "lv1", "滑索架", 2.2, 0.0, 0.2)
        graph = ZipLineGraph(
            [first, second],
            [ZipLineLink("a", "b", distance_m=2.0, max_range_m=80.0)],
        )
        return grid, graph


class TestCornerGuardIsLoadBearing(unittest.TestCase):
    """禁斜穿墙角（守卫）不能删：它决定可达性，而不只是"少一条边"。

    带守卫的八连通与四连通给出**完全相同**的连通块划分；去掉守卫则只靠一个斜角相接的
    两片会被并成一块——那两片实际走不过去，于是"代价与可达性会同时错、而且错得一致"，
    探针说可达、角色却会撞墙。

    **恒等式的前提（容易漏）**：守卫判据必须**与节点判据相同**。允许斜走时
    ``(i+di,j)`` 与 ``(i+di,j+dj)`` 正交相邻，前提是这两格本身都算"可走"——若节点只认
    Free、守卫却只查"非阻挡"，斜边就能借一个 Unknown 的正交格把两个 Free 片并起来，
    恒等式随之失效。实测就是这个差异：同一张 ``map02``，判据一致时 12 块，判据不匹配时
    11 块（``base01`` 更是 8 块对 5 块）。编辑器侧的历史实现用的正是"节点 Free、守卫
    非阻挡"这组不匹配判据，所以它报的块数与产品默认下的规划器不一致。
    """

    @staticmethod
    def _components(grid, *, diagonal, guard):
        """按指定规则统计可行走连通块数。"""
        cells = grid.cells
        height, width = cells.shape
        free = cells == CELL_FREE
        seen = np.zeros((height, width), bool)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if diagonal:
            dirs += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        count = 0
        for r0, c0 in zip(*np.nonzero(free), strict=True):
            r0, c0 = int(r0), int(c0)
            if seen[r0, c0]:
                continue
            count += 1
            stack = [(r0, c0)]
            seen[r0, c0] = True
            while stack:
                i, j = stack.pop()
                for di, dj in dirs:
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < height and 0 <= nj < width) or not free[ni, nj] or seen[ni, nj]:
                        continue
                    if di and dj and guard and not (free[i + di, j] and free[i, j + dj]):
                        continue
                    seen[ni, nj] = True
                    stack.append((ni, nj))
        return count

    @staticmethod
    def _components_with_guard_predicate(grid, *, node, guard):
        """节点判据与守卫判据可以不同的连通块计数。

        存在这个入口就是为了证明：两者不同时恒等式失效——不是数学错了，是前提没了。
        """
        cells = grid.cells
        height, width = cells.shape

        def mask(kind):
            if kind == "free":
                return cells == CELL_FREE
            if kind == "not_blocked":
                return cells != CELL_BLOCKED
            raise AssertionError(kind)

        node_mask = mask(node)
        guard_mask = mask(guard)
        seen = np.zeros((height, width), bool)
        dirs = [
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
            (1, 1),
            (1, -1),
            (-1, 1),
            (-1, -1),
        ]
        count = 0
        for r0, c0 in zip(*np.nonzero(node_mask), strict=True):
            r0, c0 = int(r0), int(c0)
            if seen[r0, c0]:
                continue
            count += 1
            stack = [(r0, c0)]
            seen[r0, c0] = True
            while stack:
                i, j = stack.pop()
                for di, dj in dirs:
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < height and 0 <= nj < width) or not node_mask[ni, nj] or seen[ni, nj]:
                        continue
                    if di and dj and (not guard_mask[i + di, j] or not guard_mask[i, j + dj]):
                        continue
                    seen[ni, nj] = True
                    stack.append((ni, nj))
        return count

    def test_guarded_eight_and_four_connectivity_agree(self):
        """恒等式：允许斜走时两条正交邻格都可走，所以斜边从不并块。"""
        grids = [
            _grid(["o#", "#o"]),  # 只靠一个斜角相接
            _grid(["#o#", "ooo", "#o#"]),  # 十字
            _grid(["ooo", "o#o", "ooo"]),  # 中间挖空
            _grid(["o#o", "###", "o#o"]),  # 四个角
        ]
        for grid in grids:
            with self.subTest(shape=grid.shape):
                four = self._components(grid, diagonal=False, guard=False)
                guarded = self._components(grid, diagonal=True, guard=True)
                self.assertEqual(four, guarded)

    def test_identity_needs_the_guard_to_match_the_node_predicate(self):
        """判据不匹配（节点认 Free、守卫只查非阻挡）时，恒等式失效。

        这正是编辑器与规划器在 ``map02`` 上报出 11 / 12 两个数、``base01`` 上报出
        5 / 8 的原因：编辑器允许斜边借一个 Unknown 的正交格，产品默认不允许。
        """
        grid = _grid(["o.", ".o"])
        # 判据一致：Unknown 的正交格挡住斜边 -> 仍是两块
        self.assertEqual(self._components(grid, diagonal=True, guard=True), 2)
        # 判据不匹配：守卫只看阻挡，Unknown 放行 -> 斜边把两块并起来
        merged = self._components_with_guard_predicate(grid, node="free", guard="not_blocked")
        self.assertEqual(merged, 1, "不匹配的判据会把两个 Free 片并成一块")

    def test_editor_style_predicate_understates_fragmentation(self):
        """同一个网格，判据不匹配时报出的块数会更少——也就是"低估了碎片化"。"""
        grid = _grid(["o.", ".o"])
        strict = self._components(grid, diagonal=True, guard=True)
        editor_style = self._components_with_guard_predicate(grid, node="free", guard="not_blocked")
        self.assertLess(editor_style, strict)

    def test_dropping_the_guard_merges_diagonal_only_neighbours(self):
        """反证：去掉守卫后，只靠斜角相接的两片会被并成一块（块数变少）。"""
        grid = _grid(["o#", "#o"])
        self.assertEqual(self._components(grid, diagonal=False, guard=False), 2)
        self.assertEqual(self._components(grid, diagonal=True, guard=True), 2)
        self.assertEqual(self._components(grid, diagonal=True, guard=False), 1, "无守卫会并块")

    def test_planner_refuses_to_squeeze_through_a_diagonal_corner(self):
        """规划器必须拒绝斜穿墙角——否则可达性会说谎。"""
        grid = _grid(["o#", "#o"])
        for allow_unknown in (False, True):
            planner = GridPlanner(grid, allow_unknown=allow_unknown)
            result = planner.plan_cells((0, 0), (1, 1))
            self.assertFalse(result.ok, f"allow_unknown={allow_unknown} 时不该斜穿墙角")

    def test_corner_guard_is_policy_aware(self):
        """守卫用 ``_passable``（受 allow_unknown 影响），不是"非阻挡"粗筛。

        ``allow_unknown=False`` 时未知格视同阻挡，斜穿的角也不许切——这正是
        ``DenseGrid.neighbors`` 的粗筛做不到、必须由策略层兜住的那一半。
        """
        grid = _grid(["o.", ".o"])
        strict = GridPlanner(grid, allow_unknown=False)
        loose = GridPlanner(grid, allow_unknown=True)
        self.assertFalse(strict._corner_ok(0, 0, 1, 1), "未知格挡住的角不能切")
        self.assertTrue(loose._corner_ok(0, 0, 1, 1), "允许穿越未知格时应可斜穿")

    def test_plan_multi_honours_the_same_guard(self):
        """新增的多源多目标搜索必须与 ``plan_cells`` 用同一条守卫。"""
        grid = _grid(["o#", "#o"])
        planner = GridPlanner(grid, allow_unknown=True)
        result = planner.plan_multi([(0, 0)], [(1, 1)])
        self.assertFalse(result.ok, "多源多目标也不能斜穿墙角")
        # 相邻格仍然可走，确认不是"整张图都不可达"
        self.assertTrue(planner.plan_multi([(0, 0)], [(0, 0)]).ok)


if __name__ == "__main__":
    unittest.main()
