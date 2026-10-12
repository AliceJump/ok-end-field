"""分层导航的端到端接线测试。

覆盖的是"规划器"与"任务层"之间的那层接线（这是分层功能能不能真跑起来的关键）：

- 三维目标（``goal_y``）确实传到了层判定；
- 起终点**跨层**时才走分层路径，**同层**仍走原来的单层路径（含用户滑索），零回归；
- 分层路线装配成"每段一个跟随器"，换段 = 换跟随器；
- 判定不出层 / 层图不可达时**明确失败**，而不是退回一条无视层级的单层路线。

路线能不能"走"到终点由 ``TestNavLayeredPlanner`` 负责（那边直接调规划器，不受本层
输入模拟的干扰）；这里只验证接线。
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.core.GridNavConfig import CONFIG_GRID_DIR
from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, GRID_SUFFIX, new_grid, save_grid
from src.nav.nav_state import GridRouteRequest, NavSession, NavTick
from tests.TestGridNavigationMixin import _FakeGridTask


def _row(width, z=0):
    dense = np.full((1, width), CELL_BLOCKED, dtype=np.uint8)
    dense[0, :] = CELL_FREE
    return dense


class _LayeredTask(_FakeGridTask):
    """在夹具基础上把网格目录指向一个临时目录，并允许设置高度与目标。"""

    def __init__(self, grid_dir: Path):
        self._grid_dir = grid_dir
        super().__init__(grid_dir / f"test_4{GRID_SUFFIX}")
        self.config[CONFIG_GRID_DIR] = str(grid_dir)
        self.config["网格文件(可选)"] = ""
        self.y = None

    def _grid_layered_dirs(self):  # pragma: no cover - 仅用于调试
        return str(self._grid_dir)


class LayeredNavigationWiringTest(unittest.TestCase):
    """分层导航的接线：三维目标 → 层判定 → 分层规划 → 分段跟随。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 主图：一条 12 格的走廊
        main = new_grid((1, 12), map_name="test", zoom="4")
        main.cells = _row(12)
        save_grid(self.root / f"test_4{GRID_SUFFIX}", main)
        # 分层 lv1：另一条 4 格的走廊，世界坐标错开到 x∈[10,14)，高度 100..110
        tier = new_grid(
            (1, 4),
            origin=(10.0, 0.0, 0.0),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 10.0, "x1": 14.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        tier.cells = _row(4)
        save_grid(self.root / f"test__lv1_4{GRID_SUFFIX}", tier)
        (self.root / "links.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "links": [
                        {
                            "id": "link1",
                            "from": {
                                "map": "test",
                                "tier": "lv1",
                                "zoom": "4",
                                "rect": {"x0": 13.0, "z0": 0.0, "x1": 14.0, "z1": 1.0},
                            },
                            "to": {
                                "map": "test",
                                "tier": "",
                                "zoom": "4",
                                "rect": {"x0": 0.0, "z0": 0.0, "x1": 1.0, "z1": 1.0},
                            },
                            "bidirectional": True,
                            "kind": "stairs",
                            "cost": 1.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.task = _LayeredTask(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- 辅助 ----
    def _ctx(self, *, x, z, y, goal, goal_y=None):
        self.task.x, self.task.z, self.task.y = x, z, y
        session = NavSession(
            request=GridRouteRequest(
                goal=(float(goal[0]), float(goal[1])),
                goal_y=None if goal_y is None else float(goal_y),
                map_id="test",
            ),
            deadline=self.task.active_time() + 180.0,
            limit=180.0,
            tick=0.2,
        )
        ctx = self.task._grid_nav_poll(session)
        self.assertIsInstance(ctx, NavTick)
        return session, ctx

    def _plan(self, *, x, z, y, goal, goal_y=None):
        session, ctx = self._ctx(x=x, z=z, y=y, goal=goal, goal_y=goal_y)
        result = self.task._try_layered_route_on_plan(ctx, (x, z), goal)
        return session, ctx, result

    # ---- 层判定 ----
    def test_layered_map_is_discovered(self):
        layered = self.task._grid_layered_map("test")
        self.assertIsNotNone(layered, "应当发现分层数据（有 lv1 层）")
        self.assertEqual(layered.tier_ids(), ["", "lv1"])
        self.assertEqual(len(layered.links), 1, "只保留属于本图的连接")

    def test_links_from_other_maps_are_filtered_out(self):
        """links.json 是全局表：别的地图的连接不能把"无分层的地图"误判成有分层。"""
        (self.root / "links.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "links": [
                        {
                            "id": "other",
                            "from": {
                                "map": "other",
                                "tier": "x",
                                "zoom": "4",
                                "rect": {"x0": 0.0, "z0": 0.0, "x1": 1.0, "z1": 1.0},
                            },
                            "to": {
                                "map": "other",
                                "tier": "",
                                "zoom": "4",
                                "rect": {"x0": 1.0, "z0": 0.0, "x1": 2.0, "z1": 1.0},
                            },
                            "kind": "stairs",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.task._grid_nav_layered_cache.clear()
        layered = self.task._grid_layered_map("test")
        # 本图仍有 lv1 层，所以整体仍是分层地图，但那条外图连接必须被滤掉
        self.assertIsNotNone(layered)
        self.assertEqual(layered.links, ())

    def test_y_decides_the_tier(self):
        _session, _ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True), "高度落在 lv1 区间、终点在大地图 -> 应当走分层")

    def test_ambiguous_height_refuses_to_guess(self):
        """同一高度区间被两层覆盖时明确失败——这正是会把角色送错层的场景。"""
        tier2 = new_grid(
            (1, 4),
            origin=(10.0, 0.0, 0.0),
            map_name="test",
            zoom="4",
            tier="lv2",
            floor_regions=[{"x0": 10.0, "x1": 14.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        tier2.cells = _row(4)
        save_grid(self.root / f"test__lv2_4{GRID_SUFFIX}", tier2)
        self.task._grid_nav_layered_cache.clear()
        _session, _ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, False), "判定不出层必须明确失败，不能赌一个")
        self.assertTrue(any("无法唯一确定层级" in line for line in self.task.logs), self.task.logs[-3:])

    # ---- 单层路径不受影响 ----
    def test_same_tier_falls_back_to_single_layer(self):
        """起终点同层：不接管，交回单层路径（行为与分层上线前一致）。"""
        _session, _ctx, result = self._plan(x=0.5, z=0.5, y=5.0, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (False, False))

    def test_goal_without_height_is_treated_as_overworld(self):
        """调用方只给平面坐标时按大地图处理，不因为"缺 y"而失败。"""
        _session, _ctx, result = self._plan(x=0.5, z=0.5, y=105.0, goal=(6.5, 0.5))
        self.assertEqual(result, (False, False), "两点都判成大地图 -> 同层 -> 单层路径")

    # ---- 分段驱动 ----
    def test_cross_layer_route_is_split_into_segments_per_layer(self):
        session, _ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        self.assertTrue(session.layered)
        self.assertEqual(len(session.route.segments), 2, "lv1 一段 + 大地图一段")
        self.assertEqual(list(session.route.tiers), ["lv1", ""])
        self.assertEqual(len(session.route.links), 1)
        # 第一段已装配好，且跟随器面向的是 lv1 那张网格
        follower = self.task._grid_nav_follower
        self.assertIsNotNone(follower)
        self.assertEqual(follower.grid.meta.tier, "lv1")

    def test_advancing_switches_to_the_next_layer_grid(self):
        session, ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        outcome = self.task._advance_layered_segment(ctx)
        self.assertIsNone(outcome, "还有下一段时应继续，而不是结束导航")
        self.assertEqual(session.segment_index, 1)
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "", "第二段应当在大地图上")
        self.assertTrue(any("跨层" in line for line in self.task.logs), self.task.logs[-4:])

    def test_done_handler_advances_instead_of_finishing(self):
        """跟随器报到达时，若整条分层路线还没走完，应当换段而不是结束导航。"""
        session, ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        from src.nav.route_follower import DONE, FollowerStep

        ctx.step = FollowerStep(DONE, distance_to_goal=0.1)
        outcome = self.task._grid_nav_state_done(ctx)
        self.assertIsNone(outcome, "应当换段继续，而不是返回 True 结束")
        self.assertEqual(session.segment_index, 1)

    def test_final_segment_done_finishes_navigation(self):
        session, ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        session.segment_index = len(session.route.segments) - 1
        from src.nav.route_follower import DONE, FollowerStep

        ctx.step = FollowerStep(DONE, distance_to_goal=0.1)
        self.assertIs(self.task._grid_nav_state_done(ctx), True, "最后一段走完才算到达")

    def test_link_landing_on_the_goal_finishes_the_route(self):
        """连接的落点正好是终点时，跨层之后就该结束——不能把最后一段再激活一遍。

        回归：目标压在连接端点上时，规划器会把"长度为 0 的那一段"丢掉（零长段不是一步），
        于是路线只剩 1 段 + 1 条连接。换段逻辑若不特判，就会把第 0 段重新激活，导航永远
        走不完。
        """
        session, ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(0.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        self.assertEqual(len(session.route.segments), 1, "大地图那段长度为 0，已被丢弃")
        self.assertEqual(len(session.route.links), 1)
        outcome = self.task._advance_layered_segment(ctx)
        self.assertIs(outcome, True, "落点即目标，应当完成而不是继续")
        self.assertTrue(any("落点即目标" in line for line in self.task.logs), self.task.logs[-3:])

    def test_replan_clears_the_layered_route(self):
        session, ctx, result = self._plan(x=10.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        # 起终点改为同层（都判成大地图）后重规划，应当不再走分层
        self.task.y = 5.0
        session.request = type(session.request)(goal=session.request.goal, goal_y=None, map_id=session.request.map_id)
        again = self.task._try_layered_route_on_plan(ctx, (0.5, 0.5), (6.5, 0.5))
        self.assertEqual(again, (False, False), "同层时应当交回单层路径")


if __name__ == "__main__":
    unittest.main()
