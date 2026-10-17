"""分层导航的端到端接线测试。

覆盖的是"规划器"与"任务层"之间的那层接线（这是分层功能能不能真跑起来的关键）：

- 三维目标（``goal_y``）确实传到了层判定；
- 起终点**跨层**时才走分层路径，**同层**仍走原来的单层路径（含用户滑索），零回归；
- 分层路线装配成"每段一个跟随器"，换段 = 换跟随器；
- 判定不出层 / 层图不可达时**明确失败**，而不是退回一条无视层级的单层路线。
- 绳索跨越**不复用滑索**：下行走过去、上行点 ``fL.gather_icon2``（见 ``RopeCrossingTest``）。

路线能不能"走"到终点由 ``TestNavLayeredPlanner`` 负责（那边直接调规划器，不受本层
输入模拟的干扰）；这里只验证接线。
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.core.GridNavConfig import CONFIG_GRID_DIR
from src.data.FeatureList import FeatureList as fL
from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, CELL_UNKNOWN, GRID_SUFFIX, new_grid, save_grid
from src.nav.nav_state import GridRouteRequest, NavSession, NavState, NavTick
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


class _HeadingWalkingTask(_LayeredTask):
    """按**朝向**移动的测试任务。

    基类的 ``sleep`` 无条件 ``x += 1.0``，只能模拟"一直向东走"。分层路线经常要往回走
    （跨层之后目标可能在另一侧），几何就不自洽了——跑完整主循环时会出现"朝目标反方向
    一直走然后判偏航"的假失败。这里改成沿当前罗盘方位前进，几何才和真实一致。
    """

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))
        if not self.w_down:
            return
        step = 0.5
        radians = math.radians(float(self.heading))
        self.x += math.sin(radians) * step
        self.z += math.cos(radians) * step


class LayeredNavigationWiringTest(unittest.TestCase):
    """分层导航的接线：三维目标 → 层判定 → 分层规划 → 分段跟随。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 主图：一条 12 格的走廊
        main = new_grid((1, 12), map_name="test", zoom="4")
        main.cells = _row(12)
        save_grid(self.root / f"test_4{GRID_SUFFIX}", main)
        # 分层 lv1：另一条 4 格的走廊。**与主图共用同一原点**——连接的两端必须落在同一处
        # X/Z（只差一层），这是数据模型的约定：实测 7 条连接两端水平距离全是 1.0~2.5m。
        # 早先这里把 lv1 错开到 x=10，等于让"楼梯"连接相隔 13m 的两个点，夹具违背了模型。
        tier = new_grid(
            (1, 4),
            origin=(0.0, 0.0, 0.0),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 0.0, "x1": 4.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
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
                            # 两端落在**同一个 X/Z**：这就是"只差一层"的连接
                            "from": {
                                "map": "test",
                                "tier": "lv1",
                                "zoom": "4",
                                "rect": {"x0": 0.0, "z0": 0.0, "x1": 1.0, "z1": 1.0},
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
        self.task = _HeadingWalkingTask(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- 辅助 ----
    def _ctx(self, *, x, z, y, goal, goal_y=None, stop_distance=None):
        self.task.x, self.task.z, self.task.y = x, z, y
        session = NavSession(
            request=GridRouteRequest(
                goal=(float(goal[0]), float(goal[1])),
                goal_y=None if goal_y is None else float(goal_y),
                map_id="test",
                stop_distance=None if stop_distance is None else float(stop_distance),
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
        _session, _ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True), "高度落在 lv1 区间、终点在大地图 -> 应当走分层")

    def test_ambiguous_height_refuses_to_guess(self):
        """同一高度区间被两层覆盖时明确失败——这正是会把角色送错层的场景。"""
        tier2 = new_grid(
            (1, 4),
            origin=(0.0, 0.0, 0.0),
            map_name="test",
            zoom="4",
            tier="lv2",
            floor_regions=[{"x0": 0.0, "x1": 4.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        tier2.cells = _row(4)
        save_grid(self.root / f"test__lv2_4{GRID_SUFFIX}", tier2)
        self.task._grid_nav_layered_cache.clear()
        _session, _ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, False), "判定不出层必须明确失败，不能赌一个")
        self.assertTrue(any("无法唯一确定层级" in line for line in self.task.logs), self.task.logs[-3:])

    # ---- 单层路径不受影响 ----
    def test_same_tier_falls_back_to_single_layer(self):
        """起终点都在大地图：不接管，交回单层路径（行为与分层上线前一致）。"""
        _session, _ctx, result = self._plan(x=0.5, z=0.5, y=5.0, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (False, False))

    def test_same_tier_on_a_non_main_layer_keeps_that_layer_grid(self):
        """起终点都在 lv1 时必须继续走分层：单层回退加载的是主图网格，会忽略本层墙体。"""
        session, _ctx, result = self._plan(x=0.5, z=0.5, y=105.0, goal=(3.5, 0.5), goal_y=105.0)

        self.assertEqual(result, (True, True))
        self.assertEqual(list(session.route.tiers), ["lv1"])
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "lv1")

    def test_goal_without_height_is_treated_as_overworld(self):
        """调用方只给平面坐标时按大地图处理，不因为"缺 y"而失败。

        起点特意取在**分层区间之外**（x=6.5 不在 lv1 的 x∈[0,4) 里），这样两边都判成
        大地图，验证的是"缺 y 不报错"而不是"跨层能不能走"。
        """
        _session, _ctx, result = self._plan(x=6.5, z=0.5, y=105.0, goal=(9.5, 0.5))
        self.assertEqual(result, (False, False), "两点都判成大地图 -> 同层 -> 单层路径")

    # ---- 终点接近判定 ----
    def test_stop_distance_does_not_shortcut_across_layers(self):
        """目标在另一层且恰好同一 XZ：5m 停车距离不能直接判成功。"""
        _session, ctx = self._ctx(
            x=3.5,
            z=0.5,
            y=105.0,
            goal=(3.5, 0.5),
            goal_y=5.0,
            stop_distance=5.0,
        )

        self.assertIsNone(self.task._grid_nav_classify(ctx), "跨层不能触发 STOP_REACHED")

    def test_stop_distance_still_applies_on_the_goal_layer(self):
        """已经在目标层且进入停车距离：保持原来的提前结束行为。"""
        _session, ctx = self._ctx(
            x=3.5,
            z=0.5,
            y=105.0,
            goal=(3.2, 0.5),
            goal_y=105.0,
            stop_distance=5.0,
        )

        self.assertEqual(self.task._grid_nav_classify(ctx), NavState.STOP_REACHED)

    # ---- 分段驱动 ----
    def test_cross_layer_route_is_split_into_segments_per_layer(self):
        session, _ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
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
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        outcome = self.task._advance_layered_segment(ctx)
        self.assertIsNone(outcome, "还有下一段时应继续，而不是结束导航")
        self.assertEqual(session.segment_index, 1)
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "", "第二段应当在大地图上")
        self.assertTrue(any("跨层" in line for line in self.task.logs), self.task.logs[-4:])

    def test_done_handler_advances_instead_of_finishing(self):
        """跟随器报到达时，若整条分层路线还没走完，应当换段而不是结束导航。"""
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        from src.nav.route_follower import DONE, FollowerStep

        ctx.step = FollowerStep(DONE, distance_to_goal=0.1)
        outcome = self.task._grid_nav_state_done(ctx)
        self.assertIsNone(outcome, "应当换段继续，而不是返回 True 结束")
        self.assertEqual(session.segment_index, 1)

    def test_final_segment_done_finishes_navigation(self):
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
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
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(0.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        self.assertEqual(len(session.route.segments), 1, "大地图那段长度为 0，已被丢弃")
        self.assertEqual(len(session.route.links), 1)
        outcome = self.task._advance_layered_segment(ctx)
        self.assertIs(outcome, True, "落点即目标，应当完成而不是继续")
        self.assertTrue(any("落点即目标" in line for line in self.task.logs), self.task.logs[-3:])

    def test_done_handler_executes_the_trailing_link(self):
        """**走主循环入口**：最后一段之后的跨层也必须执行，不能直接报到达。

        上面的用例直接调 ``_advance_layered_segment``，绕开了 ``_grid_nav_state_done``
        的入口条件；而真实的接线正是靠这个条件决定要不要跨最后那条连接。
        """
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(0.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        self.assertEqual(len(session.route.segments), 1)
        self.assertEqual(len(session.route.transitions[0]), 1, "末尾仍挂着 1 条连接")
        from src.nav.route_follower import DONE, FollowerStep

        ctx.step = FollowerStep(DONE, distance_to_goal=0.1)
        outcome = self.task._grid_nav_state_done(ctx)

        self.assertIs(outcome, True, "跨完末尾连接才算到达")
        self.assertTrue(any("落点即目标" in line for line in self.task.logs), self.task.logs[-3:])

    def test_full_loop_drives_a_cross_layer_route_to_completion(self):
        """跑**完整的** ``navigate_grid_to`` 主循环，而不只是调辅助函数。

        这是本文件里唯一覆盖"接线接对了没"的测试：主循环是
        ``取快照 → 判定 → 派发``，分层接管后要能顺着"分段 → 跨层 → 下一段"一路走到终点。
        只测辅助函数会漏掉主循环里的装配错误（例如 DONE 时没换段、规划后没接着跟随）。

        模拟的一处必要约定：跨层**会改变所在高度**。游戏里走过楼梯/门时 y 会变，而
        ``_cross_layered_link`` 的"走过去"分支本来不做物理动作，所以这里在测试任务里
        把 y 跟着改掉——不改的话人还在旧层的高度上，"中途换层"的守护会（正确地）判定
        与当前分段不符并重规划，测试就测不到正常路径了。
        """
        task = self.task
        original_cross = task._cross_layered_link

        def crossing(ctx, link):
            outcome = original_cross(ctx, link)
            if outcome is not False:
                task.y = 105.0 if link.target.tier else 5.0
            return outcome

        task._cross_layered_link = crossing  # type: ignore[method-assign]
        task.x, task.z, task.y = 3.5, 0.5, 105.0

        result = task.navigate_grid_to((6.5, 0.5), goal_y=5.0, map_id="test", timeout=60.0)

        self.assertTrue(result, f"完整主循环没能走完跨层路线；日志尾部={task.logs[-6:]}")
        self.assertLess(abs(task.x - 6.5), 2.5, f"最终位置 x={task.x}")
        self.assertTrue(any("分层路线" in line for line in task.logs), "应当走了分层路径")
        self.assertTrue(any("跨层" in line for line in task.logs), "应当执行过跨层")
        self.assertFalse(task.w_down, "结束时必须松开 W")

    def test_full_loop_fails_cleanly_when_the_route_is_unreachable(self):
        """分层路线走不通时，完整主循环应当**以失败结束**，而不是卡到超时或走单层。"""
        task = self.task
        # 拆掉分层那一侧的可走格：连接端点全部落在阻挡上 -> 层图不可达
        broken = new_grid(
            (1, 4),
            origin=(0.0, 0.0, 0.0),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 0.0, "x1": 4.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        broken.cells = np.full((1, 4), CELL_BLOCKED, dtype=np.uint8)
        save_grid(self.root / f"test__lv1_4{GRID_SUFFIX}", broken)
        task._grid_nav_layered_cache.clear()
        task.x, task.z, task.y = 3.5, 0.5, 105.0

        result = task.navigate_grid_to((6.5, 0.5), goal_y=5.0, map_id="test", timeout=60.0)

        self.assertFalse(result, "不可达必须以失败结束")
        self.assertFalse(task.w_down, "结束时必须松开 W")

    def test_floor_change_mid_route_triggers_replan(self):
        """玩家在路线中间换到另一层时，必须重新规划——否则跟随器还在拿旧那层的网格走。

        对应对方在前端踩到的同类问题：按"同一个 levelId 就不重算"去重，会把换层整条丢掉。
        """
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        self.assertEqual(session.route.segments[0].tier, "lv1")
        # 人还在原处，但高度掉到 lv1 区间之外 -> 判不出层（main 兜底）
        self.task.y = 5.0
        changed = self.task._grid_nav_segment_tier_changed(ctx)
        self.assertFalse(changed, "兜底到大地图不算确定换层，避免稀疏覆盖下每拍重规划")

    def test_unique_tier_mismatch_is_detected(self):
        """判到唯一确定、且与当前段不同的层 -> 判定为换层。"""
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        # 把当前段换成主图那一段，而位置仍落在 lv1 区间内 -> 唯一命中 lv1 != 主图
        session.segment_index = len(session.route.segments) - 1
        self.assertEqual(session.route.segments[-1].tier, "")
        ctx.x, ctx.z, self.task.y = 3.5, 0.5, 105.0
        self.assertTrue(self.task._grid_nav_segment_tier_changed(ctx))
        self.assertTrue(any("重新规划" in line for line in self.task.logs), self.task.logs[-2:])

    def test_replan_clears_the_layered_route(self):
        session, ctx, result = self._plan(x=3.5, z=0.5, y=105, goal=(6.5, 0.5), goal_y=5.0)
        self.assertEqual(result, (True, True))
        # 起终点改为同层（都判成大地图）后重规划，应当不再走分层
        self.task.y = 5.0
        session.request = type(session.request)(goal=session.request.goal, goal_y=None, map_id=session.request.map_id)
        # 起点同样取在分层区间之外，此时两边都是大地图 -> 不该再走分层
        again = self.task._try_layered_route_on_plan(ctx, (6.5, 0.5), (9.5, 0.5))
        self.assertEqual(again, (False, False), "同层时应当交回单层路径")


class _RopeTask(_LayeredTask):
    """绳索用例的夹具：按住 W 会真的下落差，点交互图标会被送上落差。"""

    def __init__(self, grid_dir: Path):
        super().__init__(grid_dir)
        self.upper_y = 105.0
        self.lower_y = 5.0
        self.y = self.upper_y
        self.click_calls: list[dict] = []
        #: 交互图标找得到吗（``False`` = 屏幕上没有这个图标）
        self.icon_found = True
        #: 点了图标会被送上高端吗（``False`` = 点击没生效）
        self.ride_up = True
        #: 按 W 走起来会落到低端吗（``False`` = 走了但高度不变）
        self.drop_on_walk = True
        #: 按 W 会真的移动吗（``False`` = 键按下了但角色不动）
        self.walk_moves = True

    def wait_click_feature(self, **kwargs):  # type: ignore[override]
        self.click_calls.append(kwargs)
        if self.icon_found and self.ride_up:
            self.y = self.upper_y
        return self.icon_found

    def sleep(self, seconds):
        if not self.walk_moves:
            self.t += max(0.0, float(seconds))
            return
        super().sleep(seconds)
        if self.w_down and self.drop_on_walk:
            self.y = self.lower_y


class RopeCrossingTest(unittest.TestCase):
    """绳索跨越：**不是滑索**——下行走过去，上行点 ``fL.gather_icon2``。

    两条路径都靠"高度真的换层了吗"收尾，所以夹具必须能给出高度：走起来 y 落到低层、
    点完图标 y 升到高层。滑索那两个接口则故意装上，用来证明绳索一次都不碰它们。
    """

    def setUp(self):
        self.task, self.layered, self.ctx = self._make_task()
        #: 起点在上层 lv1、落点在主图：原方向就是"下行"
        self.link = self.layered.links[0]

    def _make_task(self, *, main_regions=True):
        """独立夹具：返回 ``(任务, 分层地图, ctx)``；每份夹具自带临时目录。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._write_layers(Path(tmp.name), main_regions=main_regions)
        task = _RopeTask(Path(tmp.name))
        layered = task._grid_layered_map("test")
        self.assertIsNotNone(layered, "应当发现分层数据（有 lv1 层）")
        # 跨越时 ``_layer_y_range`` 读的是"当前生效的分层地图"，直接调用就得先装上
        task._grid_nav_layered_active = layered
        session = NavSession(
            request=GridRouteRequest(goal=(2.5, 0.5), map_id="test"),
            deadline=task.active_time() + 180.0,
            limit=180.0,
            tick=0.2,
        )
        ctx = task._grid_nav_poll(session)
        self.assertIsInstance(ctx, NavTick)
        return task, layered, ctx

    def _write_layers(self, root: Path, *, main_regions=True):
        main = new_grid(
            (1, 12),
            map_name="test",
            zoom="4",
            floor_regions=(
                [{"x0": 0.0, "x1": 12.0, "z0": 0.0, "z1": 1.0, "y_min": 0.0, "y_max": 10.0}] if main_regions else None
            ),
        )
        main.cells = _row(12)
        save_grid(root / f"test_4{GRID_SUFFIX}", main)
        upper = new_grid(
            (1, 12),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 0.0, "x1": 12.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        upper.cells = _row(12)
        save_grid(root / f"test__lv1_4{GRID_SUFFIX}", upper)
        (root / "links.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "links": [
                        {
                            "id": "rope1",
                            "from": {
                                "map": "test",
                                "tier": "lv1",
                                "zoom": "4",
                                "rect": {"x0": 3.0, "z0": 0.0, "x1": 4.0, "z1": 1.0},
                            },
                            "to": {
                                "map": "test",
                                "tier": "",
                                "zoom": "4",
                                "rect": {"x0": 2.0, "z0": 0.0, "x1": 3.0, "z1": 1.0},
                            },
                            "bidirectional": False,
                            "kind": "rope",
                            "cost": 1.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _cross(self, link):
        """执行一次跨越。``ctx`` 目前只是历史参数，仍按真实入口传进去。"""
        return self.task._cross_layered_link(self.ctx, link)

    def test_descent_walks_instead_of_riding_a_zip_line(self):
        self.task.zip_line_list_go = lambda *args, **kwargs: self.fail("绳索下行不该复用滑索执行")
        self.task.board_zip_line = lambda *args, **kwargs: self.fail("绳索下行不该登索")

        self.assertTrue(self._cross(self.link))

        self.assertEqual(self.task.y, self.task.lower_y, "应当真的走到了下一层")
        self.assertFalse(self.task.w_down, "结束时必须松开 W")
        self.assertFalse(self.task.click_calls, "下行不需要找交互图标")
        self.assertTrue(any("绳索下行" in line for line in self.task.logs), self.task.logs[-4:])

    def test_descent_aims_from_the_current_position(self):
        """对准方向按"当前位置 -> 落点中心"算，不是两个矩形中心的连线。

        角色站在源矩形里的哪一处由上一段决定，用矩形中心连线会朝错的方向走——这正是
        现场那次"按住 W 走了 6s 高度没变"的成因之一。
        """
        self.task.x, self.task.z = 3.5, 1.5  # 落点中心在 (2.5, 0.5)

        self.assertTrue(self._cross(self.link))

        expected = math.degrees(math.atan2(2.5 - 3.5, 0.5 - 1.5)) % 360.0
        rect_centers = math.degrees(math.atan2(2.5 - 3.5, 0.5 - 0.5)) % 360.0
        self.assertEqual(len(self.task.aims), 1, self.task.aims)
        self.assertAlmostEqual(self.task.aims[0], expected, places=3)
        self.assertNotAlmostEqual(self.task.aims[0], rect_centers, places=3)

    def test_descent_reports_a_stall_when_w_does_not_move_the_character(self):
        """按住 W 却完全没有位移时，单独报这个故障，而不是只说"高度没变"。"""
        self.task.walk_moves = False

        self.assertFalse(self._cross(self.link))

        self.assertFalse(self.task.w_down, "失败路径也要松开 W")
        self.assertTrue(any("没有任何位移" in line for line in self.task.logs), self.task.logs[-3:])

    def test_ascent_clicks_the_gather_icon(self):
        self.task.y = self.task.lower_y
        upwards = self.link.reversed

        self.assertTrue(self._cross(upwards))

        self.assertEqual(len(self.task.click_calls), 1, "上行应当点一次交互图标")
        click = self.task.click_calls[0]
        self.assertEqual(click["feature"], fL.gather_icon2)
        self.assertFalse(click["raise_if_not_found"], "找不到图标由本层报错，不该抛异常")
        self.assertEqual(self.task.y, self.task.upper_y, "点完图标应当被送上高端")
        self.assertFalse(self.task.w_down, "上行不该压着 W")
        self.assertTrue(any("绳索上行" in line for line in self.task.logs), self.task.logs[-4:])

    def test_ascent_fails_clearly_when_the_icon_is_missing(self):
        self.task.y = self.task.lower_y
        self.task.icon_found = False

        self.assertFalse(self._cross(self.link.reversed))

        self.assertTrue(any("没找到绳索交互图标" in line for line in self.task.logs), self.task.logs[-3:])

    def test_ascent_times_out_when_the_click_does_not_take(self):
        """点了图标但没被送上去：必须判失败，而不是把上不去当成到达。"""
        self.task.y = self.task.lower_y
        self.task.ride_up = False

        self.assertFalse(self._cross(self.link.reversed))

        self.assertTrue(any("绳索上行超时" in line for line in self.task.logs), self.task.logs[-3:])

    def test_descent_times_out_when_the_height_never_drops(self):
        """走了但没落到下一层（例如两端高度区间重叠、其实是上行）：判失败并说明。"""
        self.task.drop_on_walk = False

        self.assertFalse(self._cross(self.link))

        self.assertFalse(self.task.w_down, "失败路径也要松开 W")
        self.assertTrue(any("绳索下行超时" in line for line in self.task.logs), self.task.logs[-3:])

    def test_descent_without_a_height_range_falls_back_to_walking_distance(self):
        """目标层没标高度区间时验不了高度，只能按走过的水平距离收尾，并记日志说明。"""
        task, layered, ctx = self._make_task(main_regions=False)

        self.assertTrue(task._cross_layered_link(ctx, layered.links[0]))

        self.assertTrue(
            any("没有标定高度区间" in line for line in task.logs),
            task.logs[-4:],
        )


class LayeredCurrentGridTest(unittest.TestCase):
    """起点的"当前网格"：首次靠高度 / X-Z 判层，之后靠拓扑记录（跨过连接点就换新网格）。

    现场那次失败正是卡在这里：角色人在 tips_6 上，但 ``floor_regions`` 没覆盖那一处，
    只按高度判就兜底成大地图；主图在那一格是未知格，于是重规划直接失败。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # 主图：只有右侧 4 格是可走的已知格，左侧一片未知（模拟"人其实在分层场景里"）
        main = new_grid((1, 12), map_name="test", zoom="4")
        main.cells = np.full((1, 12), CELL_UNKNOWN, dtype=np.uint8)
        main.cells[0, 8:] = CELL_FREE
        save_grid(self.root / f"test_4{GRID_SUFFIX}", main)
        # 分层 lv1：整条都可走，但高度区间只标在最右端一小块（模拟稀疏标注）
        upper = new_grid(
            (1, 12),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 10.0, "x1": 12.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        upper.cells = _row(12)
        save_grid(self.root / f"test__lv1_4{GRID_SUFFIX}", upper)
        (self.root / "links.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "links": [
                        {
                            "id": "stairs1",
                            "from": {
                                "map": "test",
                                "tier": "lv1",
                                "zoom": "4",
                                "rect": {"x0": 8.0, "z0": 0.0, "x1": 9.0, "z1": 1.0},
                            },
                            "to": {
                                "map": "test",
                                "tier": "",
                                "zoom": "4",
                                "rect": {"x0": 8.0, "z0": 0.0, "x1": 9.0, "z1": 1.0},
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
        self.task.y = 105.0
        self.session = NavSession(
            request=GridRouteRequest(goal=(10.5, 0.5), map_id="test"),
            deadline=self.task.active_time() + 180.0,
            limit=180.0,
            tick=0.2,
        )

    def _plan(self, start, goal=(10.5, 0.5)):
        ctx = self.task._grid_nav_poll(self.session)
        self.assertIsInstance(ctx, NavTick)
        return self.task._try_layered_route_on_plan(ctx, start, goal)

    def test_start_falls_back_to_the_only_layer_with_a_walkable_cell(self):
        """高度落在所有标定区间之外时，按 X/Z 上"哪一层有可走格"判层。"""
        # 起点 (0.5, 0.5)：主图那一格是未知格，只有 lv1 标了可走格
        self.assertEqual(self._plan((0.5, 0.5)), (True, True), self.task.logs[-4:])

        self.assertEqual(self.session.start_tier, "lv1")
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "lv1")
        self.assertTrue(
            any("只有 lv1 标了可走格" in line for line in self.task.logs),
            self.task.logs[-4:],
        )

    def test_replan_keeps_the_current_grid_recorded_by_topology(self):
        """跨过连接点后当前网格就是新的那一层；重规划不再被稀疏高度标注打回大地图。"""
        self.assertEqual(self._plan((0.5, 0.5)), (True, True), self.task.logs[-4:])
        self.assertEqual(self.session.active_tier, "lv1", "切到分段后应当记下当前网格")

        # 挪到"主图也有可走格、高度又落在所有区间之外"的位置：X/Z 判不出唯一层，
        # 只按高度会兜底成大地图；这时必须靠拓扑记录继续用 lv1。
        self.task.x, self.task.z, self.task.y = 10.5, 0.5, 120.0
        self.assertEqual(self._plan((10.5, 0.5)), (True, True), self.task.logs[-4:])

        self.assertEqual(self.session.start_tier, "lv1")
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "lv1")
        self.assertTrue(
            any("沿用当前正在使用的网格" in line for line in self.task.logs),
            self.task.logs[-4:],
        )

    def test_topology_record_is_ignored_outside_that_grid(self):
        """记录的那一层网格里根本没有这个位置时不能照用，否则等于把人钉在错层上。"""
        self.session.active_tier = "lv1"
        self.task.y = 120.0

        # x=40 超出 12 格宽的网格：lv1 与主图都判不出，只能回大地图兜底
        self.assertEqual(self._plan((40.0, 0.5)), (False, False))

        self.assertTrue(any("按大地图处理" in line for line in self.task.logs), self.task.logs[-4:])


class LayeredSameTierDetourTest(unittest.TestCase):
    """起终点**都在大地图**、但主图被切成互不相通的块时，改用分层绕行。

    现场那条 (-262,-386.4) → (-438,-390) 就是这个形状：起点在 674 格的块里、终点在
    4757 格的块里（两块最近差 38.1m）。"进一层再绕出来"只有分层图能表达，单层网格不行；
    所以单层规划失败后要给分层第二次机会。单层能走时仍按单层走（零回归）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # 主图：左侧 674 格那种小块（0~3）+ 中间未知 + 右侧另一块（8~11）
        main = new_grid((1, 12), map_name="test", zoom="4")
        main.cells = np.full((1, 12), CELL_UNKNOWN, dtype=np.uint8)
        main.cells[0, :4] = CELL_FREE
        main.cells[0, 8:] = CELL_FREE
        save_grid(self.root / f"test_4{GRID_SUFFIX}", main)
        # 分层 lv1：整条可走，把主图两块桥起来
        bridge = new_grid(
            (1, 12),
            map_name="test",
            zoom="4",
            tier="lv1",
            floor_regions=[{"x0": 11.0, "x1": 12.0, "z0": 0.0, "z1": 1.0, "y_min": 100.0, "y_max": 110.0}],
        )
        bridge.cells = _row(12)
        save_grid(self.root / f"test__lv1_4{GRID_SUFFIX}", bridge)
        (self.root / "links.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "links": [
                        self._link("a", "", 0.0, "lv1", 0.0),
                        self._link("b", "lv1", 11.0, "", 11.0),
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.task = _LayeredTask(self.root)
        self.task.y = 5.0  # 主图没标过高度区间 -> 起点判成大地图

    @staticmethod
    def _link(link_id, from_tier, from_x, to_tier, to_x):
        def side(tier, x):
            return {
                "map": "test",
                "tier": tier,
                "zoom": "4",
                "rect": {"x0": x, "z0": 0.0, "x1": x + 1.0, "z1": 1.0},
            }

        return {
            "id": link_id,
            "from": side(from_tier, from_x),
            "to": side(to_tier, to_x),
            "bidirectional": True,
            "kind": "stairs",
            "cost": 1.0,
        }

    def _run_plan(self, goal):
        session = NavSession(
            request=GridRouteRequest(goal=goal, map_id="test"),
            deadline=self.task.active_time() + 180.0,
            limit=180.0,
            tick=0.2,
        )
        ctx = self.task._grid_nav_poll(session)
        self.assertIsInstance(ctx, NavTick)
        return session, self.task._grid_nav_state_plan(ctx)

    def test_flat_route_is_preferred_when_it_works(self):
        """单层能走到时仍旧走单层：同层行为不能被分层代价模型改写。"""
        session, _outcome = self._run_plan((2.5, 0.5))  # 与起点同块

        self.assertIsNone(session.route, "单层可走时不该接管分层")
        self.assertEqual(self.task._grid_nav_follower.grid.meta.tier, "")

    def test_flat_failure_falls_back_to_a_layer_detour(self):
        """单层走不通（两块不连通）时，改用分层绕行而不是直接报失败。"""
        session, _outcome = self._run_plan((10.5, 0.5))  # 终点在另一块

        self.assertIsNotNone(session.route, self.task.logs[-6:])
        self.assertTrue(any("改用分层绕行" in line for line in self.task.logs), self.task.logs[-8:])
        self.assertIn("lv1", list(session.route.tiers), "绕行必须真的借了 lv1 那一层")

    def test_same_tier_detour_is_not_planned_by_default(self):
        """默认策略下起终点都在大地图仍旧交回单层——分层绕行只在单层失败后才启用。"""
        session = NavSession(
            request=GridRouteRequest(goal=(10.5, 0.5), map_id="test"),
            deadline=self.task.active_time() + 180.0,
            limit=180.0,
            tick=0.2,
        )
        ctx = self.task._grid_nav_poll(session)

        self.assertEqual(
            self.task._try_layered_route_on_plan(ctx, (0.5, 0.5), (10.5, 0.5)),
            (False, False),
        )


if __name__ == "__main__":
    unittest.main()
