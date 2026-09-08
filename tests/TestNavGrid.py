# -*- coding: utf-8 -*-
"""体素网格与网格寻路单测。"""

import unittest

from src.nav.grid import GridMap, build_grid
from src.nav.grid_finder import GridFinder, GridPlan
from src.nav.storage import pick_best_2d_grid


def corridor_trails(n=11):
    return [{"x": float(i), "y": 0.0, "z": 0.0} for i in range(n)]


def l_corridor_trails():
    """L 形：先沿 +z（北）走 6 格，再沿 +x（东）走 5 格。"""
    pts = [{"x": 0.0, "y": 0.0, "z": float(i)} for i in range(7)]      # z: 0..6
    pts += [{"x": float(i), "y": 0.0, "z": 6.0} for i in range(1, 6)]  # x: 1..5
    return pts


class TestGridBuild(unittest.TestCase):
    def test_build_and_free(self):
        grid = build_grid(corridor_trails())
        # 走过格 free
        c0 = grid.cell_of(0, 0, 0)
        self.assertTrue(grid.is_free(c0))
        # 不做周围膨胀：相邻格不是 free
        self.assertFalse(grid.is_free((c0[0], c0[1], c0[2] + 1)))
        self.assertFalse(grid.is_free((c0[0] + 1, c0[1], c0[2] + 1)))
        # 垂直 +1 未走过：不可 free
        self.assertFalse(grid.is_free((c0[0], c0[1] + 1, c0[2])))

    def test_blocked_overrides_free(self):
        grid = build_grid(corridor_trails(),
                          blocked=[{"a": {"x": 5, "y": 0, "z": 0},
                                    "b": {"x": 5, "y": 0, "z": 0}}])
        self.assertTrue(grid.is_blocked(grid.cell_of(5, 0, 0)))
        self.assertFalse(grid.is_free(grid.cell_of(5, 0, 0)))

    def test_serialization_roundtrip(self):
        import tempfile
        from pathlib import Path

        grid = build_grid(corridor_trails(), teleports=[{"x": 20, "y": 0, "z": 0}],
                          zips=[{"start": {"x": 0, "y": 0, "z": 0},
                                 "end": {"x": 10, "y": 0, "z": 0}}])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.grid.json"
            grid.save(p)
            g2 = GridMap.load(p)
        self.assertEqual(g2.cells, grid.cells)
        self.assertEqual(g2.teleports, grid.teleports)
        self.assertEqual(g2.zips, grid.zips)
        self.assertTrue(g2.is_free(grid.cell_of(0, 0, 0)))


class TestGridFind(unittest.TestCase):
    def test_walked_corridor_path(self):
        grid = build_grid(corridor_trails())
        finder = GridFinder(grid)
        res = finder.find_path((0, 0, 0), (10, 0, 0), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertFalse(res.risky)
        self.assertGreater(len(res.waypoints), 1)

    def test_unknown_goal_is_risky(self):
        """未走过的目标格：冒险规划直达，标记 risky。"""
        grid = build_grid(corridor_trails())
        finder = GridFinder(grid)
        res = finder.find_path((0, 0, 0), (10, 0, 3), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertTrue(res.risky)
        self.assertGreater(res.cost, 0)

    def test_vertical_only_free(self):
        """垂直只进 free：未走过的高度层不可到达。"""
        # 走了一条 0..5 的走廊，另在 (5,0,0) 上方走过台阶 (5,1,0)
        trails = corridor_trails(6) + [{"x": 5.0, "y": 1.0, "z": 0.0}]
        grid = build_grid(trails)
        finder = GridFinder(grid)
        # 到台阶上 (5,1,0)：可达（台阶走过）
        res = finder.find_path((0, 0, 0), (5, 1, 0), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        # 到完全没走过的高度层 (0,2,0)：垂直不能冒险爬，不可达
        res2 = finder.find_path((0, 0, 0), (0, 2, 0), allow_teleport=False)
        self.assertFalse(res2.ok)

    def test_snap_height_difference_over_one_meter_rejected(self):
        """只有落差 1m 内才允许吸附到已知格，超过 1m 不能作为通行起点。"""
        grid = build_grid([{"x": 0.0, "y": 2.0, "z": 0.0}])
        cell, _ = grid.nearest_free(0.0, 0.0, 0.0, 10.0)
        self.assertIsNone(cell)

    def test_teleport_bridges_islands(self):
        trails = corridor_trails(3)  # 0..2
        teleports = [{"x": 0, "y": 0, "z": 0}, {"x": 40, "y": 0, "z": 0}]
        grid = build_grid(trails, teleports=teleports)
        finder = GridFinder(grid)
        res = finder.find_path((2, 0, 0), (40, 0, 1), allow_teleport=True)
        self.assertTrue(res.ok, res.reason)
        self.assertTrue(res.via_teleport)

    def test_zip_bridges(self):
        trails = corridor_trails(3)  # 0..2
        zips = [{"start": {"x": 0, "y": 0, "z": 0}, "end": {"x": 30, "y": 0, "z": 30}}]
        grid = build_grid(trails, zips=zips)
        finder = GridFinder(grid)
        res = finder.find_path((2, 0, 0), (30, 0, 30), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertIn("zip", res.segments)

    def test_simplify_pulls_l_corner(self):
        """L 形拐角拉直：中间 1m 格被吞掉，路径只剩 3 个拐点（起/拐角/终）。"""
        grid = build_grid(l_corridor_trails())
        finder = GridFinder(grid)
        res = finder.find_path((0, 0, 0), (5, 0, 6), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        # A* 原始路径会有 12 个格，拉直后应压缩成 3 个点（起点、拐角(0,0,6)、终(5,0,6)）
        self.assertEqual(len(res.waypoints), 3, f"拐点过多: {res.waypoints}")
        self.assertEqual(len(res.segments), 2)
        # 拐点坐标：起点 -> 拐角 (0,6) -> 终点 (5,6)（格中心 x=0.5/5.5，取整判所在格）
        self.assertEqual((int(res.waypoints[0][0]), int(res.waypoints[0][2])), (0, 0))
        self.assertEqual((int(res.waypoints[1][0]), int(res.waypoints[1][2])), (0, 6))
        self.assertEqual((int(res.waypoints[2][0]), int(res.waypoints[2][2])), (5, 6))

    def test_simplify_disabled_keeps_all_cells(self):
        """关闭拉直：保留全部 1m 格路径。"""
        grid = build_grid(l_corridor_trails())
        finder = GridFinder(grid, simplify=False)
        res = finder.find_path((0, 0, 0), (5, 0, 6), allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertGreater(len(res.waypoints), 5)


class FakeControls:
    """测试用控制注入：turn 精确生效，walk 状态可查。"""

    def __init__(self, heading: float = 0.0):
        self.yaw = heading
        self.walk_held = False
        self.turn_calls: list = []
        self.walk_calls: list = []
        self.zip_calls: list = []
        self.teleport_calls: list = []

    def heading(self):
        return self.yaw

    def turn(self, delta_deg: float) -> None:
        self.turn_calls.append(float(delta_deg))
        self.yaw = (self.yaw + float(delta_deg)) % 360.0

    def walk(self, held: bool) -> None:
        self.walk_held = bool(held)
        self.walk_calls.append(bool(held))

    def ride_zip(self, start_pos, end_pos) -> None:
        self.zip_calls.append((start_pos, end_pos))

    def teleport(self, node_pos) -> None:
        self.teleport_calls.append(node_pos)


def simulate_movement(runner, controls, pos, speed, steps, t0=0.0):
    t = t0
    for _ in range(steps):
        runner.step(tuple(pos), now=t)
        if runner.state == "moving" and controls.walk_held:
            plan = runner._plan
            if plan and runner._idx < len(plan.waypoints):
                wp = plan.waypoints[runner._idx]
                dx, dz = wp[0] - pos[0], wp[2] - pos[2]
                d = (dx * dx + dz * dz) ** 0.5
                if d > 0:
                    s = min(speed, d)
                    pos[0] += dx / d * s
                    pos[2] += dz / d * s
        t += 0.2


class TestGrid2D(unittest.TestCase):
    """二维网格（导航网格编辑器 <map>_<zoom>.grid.json，cells 为 [x,z] 二元组）。"""

    def _two_d_grid(self):
        """5 宽走廊 [0..4]×[0..5] + 墙 (1, 3) 挡住中列（原点 0 使格号即世界坐标）。"""
        data = {
            "origin": [0.0, 0.0, 0.0],
            "cell_size": 1.0,
            "cells": [[x, z] for x in range(0, 5) for z in range(0, 6)],
            "blocked": [[1, 3]],
        }
        return GridMap.from_dict(data)

    def test_load_marks_2d_and_normalizes(self):
        grid = self._two_d_grid()
        self.assertTrue(grid.is_2d)
        # [x,z] 归一化为 (ix, 0, iz)
        self.assertIn((0, 0, 0), grid.cells)
        self.assertIn((4, 0, 5), grid.cells)
        # blocked 覆盖 free（cells 集合保留原格，free 计算时剔除）
        self.assertTrue(grid.is_blocked((1, 0, 3)))
        self.assertFalse(grid.is_free((1, 0, 3)))

    def test_cell_of_ignores_y(self):
        grid = self._two_d_grid()
        # 任意高度都落到基准层 iy=0
        self.assertEqual(grid.cell_of(0.0, 100.0, 0.0), (0, 0, 0))
        self.assertEqual(grid.cell_of(0.0, 0.0, 0.0), (0, 0, 0))
        self.assertEqual(grid.cell_of(3.9, -50.0, 5.9), (3, 0, 5))

    def test_nearest_free_ignores_height(self):
        grid = self._two_d_grid()
        # Y 差 10m 也能吸附（二维模式无高度概念）
        cell, dist = grid.nearest_free(0.0, 10.0, 0.0, 4.0)
        self.assertIsNotNone(cell)
        cell2, _ = grid.nearest_free(0.0, 0.0, 0.0, 4.0)
        self.assertEqual(cell, cell2)

    def test_serialization_roundtrip_2d(self):
        grid = self._two_d_grid()
        self.assertEqual(grid.to_dict()["cells"][0], [0, 0])
        self.assertEqual(grid.to_dict()["blocked"], [[1, 3]])
        g2 = GridMap.from_dict(grid.to_dict())
        self.assertTrue(g2.is_2d)
        self.assertEqual(g2.cells, grid.cells)
        self.assertEqual(g2.blocked, grid.blocked)

    def test_find_path_2d_avoids_blocked(self):
        grid = self._two_d_grid()
        finder = GridFinder(grid)
        # 中列被墙 (1,3) 挡住，起点 (1,0) 到终点 (1,5) 必须从 -x 或 +x 侧绕行
        res = finder.find_path((1.0, 1.0, 0.0), (1.0, 1.0, 5.0),
                               snap_radius=4.0, allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertFalse(res.risky)
        # 路径不能穿越墙格
        for w in res.waypoints:
            self.assertNotEqual(grid.cell_of(*w), (1, 0, 3))
        # 终点可达，且确实绕行（直线会被拉直成 2 点，绕行至少 3 个拐点）
        self.assertEqual(grid.cell_of(*res.waypoints[-1]), (1, 0, 5))
        self.assertGreater(len(res.waypoints), 2, f"应绕墙走: {res.waypoints}")

    def test_pick_best_2d_grid(self):
        """storage.pick_best_2d_grid：取同地图多个 zoom 中 free 格最多的那份。"""
        import tempfile
        import json
        from pathlib import Path

        base = {"origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
                "cells": [], "blocked": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mapx_3_2d.grid.json").write_text(
                json.dumps({**base, "cells": [[x, 0] for x in range(5)]}),
                encoding="utf-8")
            (root / "mapx_4_2d.grid.json").write_text(
                json.dumps({**base, "cells": [[x, 0] for x in range(50)]}),
                encoding="utf-8")
            (root / "other_1_2d.grid.json").write_text(
                json.dumps({**base, "cells": [[x, 0] for x in range(99)]}),
                encoding="utf-8")
            grid, path = pick_best_2d_grid(root, "mapx")
            self.assertIsNotNone(grid)
            self.assertEqual(path.name, "mapx_4_2d.grid.json")
            self.assertTrue(grid.is_2d)
            self.assertEqual(len(grid.cells), 50)
            # 没有该地图的二维网格
            self.assertEqual(pick_best_2d_grid(root, "nodata"), (None, None))

    def test_nav_runner_2d_reaches_goal(self):
        """二维网格驱动 NavRunner 端到端走到目标（忽略 Y）。"""
        from src.nav.nav_runner import NavRunner, RunnerConfig

        grid = self._two_d_grid()
        controls = FakeControls(heading=0.0)
        runner = NavRunner(grid, controls, RunnerConfig(arrive_radius=0.6,
                                                        snap_radius=4.0))
        # 起始 (1,0.5,0) -> 目标 (3,50,5)：Y 差 49.5 米也不影响执行
        self.assertTrue(runner.navigate_to((3.0, 50.0, 5.0), start=(1.0, 0.5, 0.0)))
        pos = [1.0, 0.5, 0.0]
        simulate_movement(runner, controls, pos, speed=0.4, steps=400)
        self.assertEqual(runner.state, "done", runner.reason)
        self.assertFalse(controls.walk_held)


class TestGridSnap(unittest.TestCase):
    """吸附视线检查：risky 吸附/冒险直达不直线穿过 blocked 墙。"""

    def _grid(self):
        # 3D：free (0,0,0)、(5,0,5)、(9,0,9)；墙 (4,0,4) 挡在 (3,3)->(5,5) 直线上
        data = {
            "origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
            "cells": [[0, 0, 0], [5, 0, 5], [9, 0, 9]],
            "blocked": [[4, 0, 4]],
        }
        return GridMap.from_dict(data)

    def test_snap_picks_visible_cell_over_blocked_nearest(self):
        """最近 free (5,5) 被墙挡住视线时，吸附可见的次近格 (0,0)。"""
        grid = self._grid()
        finder = GridFinder(grid)
        res = finder.find_path((3.5, 0.5, 3.5), (9.5, 0.5, 9.5),
                               snap_radius=100.0, allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(grid.cell_of(*res.waypoints[0]), (0, 0, 0))
        # 路径全程不经过墙格
        for w in res.waypoints:
            self.assertNotEqual(grid.cell_of(*w), (4, 0, 4))

    def test_snap_visible_falls_back_when_all_blocked(self):
        """全部候选都被墙挡住时回退最近格（保持旧行为，不直接失败）。"""
        data = {
            "origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
            # 起点 (3,3) 被墙 (4,4) 与 (2,2) 两面夹住，直线都穿墙
            "cells": [[5, 0, 5], [1, 0, 1]],
            "blocked": [[4, 0, 4], [2, 0, 2]],
        }
        grid = GridMap.from_dict(data)
        finder = GridFinder(grid)
        res = finder.find_path((3.5, 0.5, 3.5), (9.5, 0.5, 9.5),
                               snap_radius=100.0, allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        # 兜底吸附最近可见/最近格，仍能规划
        self.assertIn(grid.cell_of(*res.waypoints[0]), [(5, 0, 5), (1, 0, 1)])

    def test_2d_snap_avoids_wall_in_line(self):
        """二维网格同样不直线穿墙。"""
        data = {
            "origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
            "cells": [[5, 5], [9, 9], [0, 0]],
            "blocked": [[4, 4]],
        }
        grid = GridMap.from_dict(data)
        finder = GridFinder(grid)
        res = finder.find_path((3.5, 0.5, 3.5), (9.5, 0.5, 9.5),
                               snap_radius=100.0, allow_teleport=False)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(grid.cell_of(*res.waypoints[0]), (0, 0, 0))

    def test_snap_exclude_skips_forbidden_cell(self):
        """卡住重规划排除当前格后，吸附跳到次近格。"""
        data = {
            "origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
            "cells": [[5, 0, 5], [9, 0, 9]],
            "blocked": [],
        }
        grid = GridMap.from_dict(data)
        finder = GridFinder(grid)
        res = finder.find_path((3.5, 0.5, 3.5), (9.5, 0.5, 9.5),
                               snap_radius=100.0, allow_teleport=False,
                               snap_exclude={(5, 0, 5)})
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(grid.cell_of(*res.waypoints[0]), (9, 0, 9))


class TestNavRunner(unittest.TestCase):
    def test_heading_convention_north_zero(self):
        """坐标 x=东/z=北/y=高；正北 0°，正西 90°，逆时针增大。"""
        from src.nav.nav_runner import heading_to

        self.assertAlmostEqual(heading_to(0, 0, 0, 10), 0.0)     # 向北 +z
        self.assertAlmostEqual(heading_to(0, 0, -10, 0), 90.0)   # 向西 -x
        self.assertAlmostEqual(heading_to(0, 0, 0, -10), 180.0)  # 向南 -z
        self.assertAlmostEqual(heading_to(0, 0, 10, 0), 270.0)   # 向东 +x

    def test_goal_radius_completes_within_3m(self):
        """最终目的地 3 米内即完成（goal_radius）。"""
        from src.nav.nav_runner import NavRunner, RunnerConfig

        grid = build_grid(corridor_trails())
        controls = FakeControls(heading=270.0)  # 向东
        runner = NavRunner(grid, controls, RunnerConfig(arrive_radius=1.0,
                                                        goal_radius=3.0, snap_radius=4.0))
        pos = [0.0, 0.0, 0.0]
        self.assertTrue(runner.navigate_to((10, 0, 0)))
        simulate_movement(runner, controls, pos, speed=0.4, steps=200)
        self.assertEqual(runner.state, "done", runner.reason)
        # 完成时距离目的地 ≤ 3m（宽松于中间点 1m）
        self.assertLessEqual(((pos[0] - 10) ** 2) ** 0.5, 3.0 + 0.01)

    def _corridor_runner(self, heading=270.0):
        from src.nav.nav_runner import NavRunner, RunnerConfig

        grid = build_grid(corridor_trails())
        controls = FakeControls(heading=heading)
        runner = NavRunner(grid, controls, RunnerConfig(arrive_radius=0.6,
                                                       snap_radius=4.0))
        return grid, controls, runner

    def test_straight_navigation_reaches_goal(self):
        grid, controls, runner = self._corridor_runner()
        pos = [0.0, 0.0, 0.0]
        self.assertTrue(runner.navigate_to((10, 0, 0)))
        simulate_movement(runner, controls, pos, speed=0.4, steps=200)
        self.assertEqual(runner.state, "done", runner.reason)
        self.assertFalse(controls.walk_held)

    def test_navigate_to_with_start_updates_last_pos(self):
        grid, controls, runner = self._corridor_runner()
        self.assertTrue(runner.navigate_to((10, 0, 0), start=(20, 0, 0)))
        self.assertEqual(runner._last_pos, (20.0, 0.0, 0.0))

    def test_combat_pauses_and_resumes(self):
        grid, controls, runner = self._corridor_runner()
        flags = {"combat": False}
        runner.set_combat_check(lambda: flags["combat"])
        pos = [0.0, 0.0, 0.0]
        self.assertTrue(runner.navigate_to((10, 0, 0)))
        simulate_movement(runner, controls, pos, speed=0.4, steps=8, t0=0.0)
        flags["combat"] = True
        runner.step(tuple(pos), now=2.0)
        self.assertEqual(runner.state, "paused")
        self.assertFalse(controls.walk_held)
        flags["combat"] = False
        simulate_movement(runner, controls, pos, speed=0.4, steps=200, t0=2.4)
        self.assertEqual(runner.state, "done", runner.reason)

    def test_stuck_replans_without_touching_grid(self):
        grid, controls, runner = self._corridor_runner(heading=0.0)
        pos = [0.0, 0.0, 0.0]
        self.assertTrue(runner.navigate_to((10, 0, 0)))
        t = 0.0
        while t < 60 and runner.state != "failed":
            runner.step(tuple(pos), now=t)
            t += 1.0
        # 卡住不修改网格数据（free/blocked 均保持建图时的状态）
        cell = grid.cell_of(0, 0, 0)
        self.assertFalse(grid.is_blocked(cell))
        self.assertTrue(grid.is_free(cell))
        # 连续卡住达到上限后判失败
        self.assertEqual(runner.state, "failed")

    def test_risky_start_walks_to_nearest_free(self):
        grid, controls, runner = self._corridor_runner(heading=90.0)
        pos = [20.0, 0.0, 0.0]
        self.assertTrue(runner.navigate_to((10, 0, 0), start=(20, 0, 0)))
        self.assertTrue(runner._plan.risky_start)
        self.assertEqual(runner._idx, 0)
        simulate_movement(runner, controls, pos, speed=0.4, steps=300)
        self.assertEqual(runner.state, "done", runner.reason)

    def test_on_plan_callback_receives_plan(self):
        """每次成功规划（含重规划）都触发 on_plan 回调。"""
        grid, controls, runner = self._corridor_runner()
        plans = []
        runner.on_plan = plans.append
        self.assertTrue(runner.navigate_to((10, 0, 0)))
        self.assertEqual(len(plans), 1)
        self.assertTrue(plans[0].ok)
        # 卡住重规划也触发
        runner.state = "moving"
        runner._last_pos = (20.0, 0.0, 0.0)
        runner._handle_stuck()
        self.assertGreaterEqual(len(plans), 2)

    def test_repeated_stuck_fails_after_three(self):
        """同一格连续卡住 3 次：停止无限撞墙循环并给出明确失败原因。"""
        grid, controls, runner = self._corridor_runner()
        runner._last_pos = (20.0, 0.0, 0.0)
        runner._goal = (10.0, 0.0, 0.0)
        runner._handle_stuck()  # 第 1 次：脱困/重规划
        self.assertEqual(runner.state, "moving", runner.reason)
        runner._handle_stuck()  # 第 2 次：吸附排除当前格近旁，换方向
        self.assertEqual(runner.state, "moving", runner.reason)
        self.assertGreaterEqual(runner._stuck_count, 2)
        runner._handle_stuck()  # 第 3 次：直接失败
        self.assertEqual(runner.state, "failed")
        self.assertIn("连续卡住", runner.reason)

    def test_stuck_count_resets_on_progress(self):
        """移动到新航点后连续卡住计数清零。"""
        grid, controls, runner = self._corridor_runner()
        runner._last_pos = (20.0, 0.0, 0.0)
        runner._goal = (10.0, 0.0, 0.0)
        runner._handle_stuck()
        self.assertEqual(runner._stuck_count, 1)
        runner._advance()
        self.assertEqual(runner._stuck_count, 0)
        self.assertIsNone(runner._stuck_cell)


if __name__ == "__main__":
    unittest.main()
