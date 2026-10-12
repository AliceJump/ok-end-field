"""``src/nav/layered_planner.py`` 的单元测试。

分层规划的语义要点（都用合成网格钉住，不依赖本机数据）：

- 层内分段是**多源多目标**：连接端点是一个区域，取"哪一格进、哪一格出"会改变代价，
  固定一对就不再精确；
- **不可达是常态**：只有一个单向出口连通块、或两端不在同一连通区时，必须返回 ok=False
  而不是抛错或当成 0 代价；
- **单向连接**只能顺着走；
- 连续同层行走要合并成一段，且代价按合并后的真实首尾重算（区域集合不能当免费传送用）；
- 默认策略必须与产品默认一致（``allow_unknown=False``）——用错会让"隔断"被算成可达。
"""

import unittest
from pathlib import Path

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, new_grid
from src.nav.grid_planner import GridPlanner
from src.nav.layered_map import LayeredMap, layer_key, load_layered_map
from src.nav.layered_planner import LayeredPlanner

_REAL_DATA = Path(r"E:\LuQing\python\nav-grid-editor\assets\nav-grid-data")

#: 字符画：``o`` 可走、``#`` 阻挡、``.`` 未知
_CHARS = {"o": CELL_FREE, "#": CELL_BLOCKED, ".": 0}


def _grid(rows, *, tier="", map_name="map02", origin=(0.0, 0.0, 0.0), regions=()):
    dense = np.array([[_CHARS[ch] for ch in row] for row in rows], dtype=np.uint8)
    instance = new_grid(
        dense.shape,
        origin=origin,
        cell_size=1.0,
        map_name=map_name,
        zoom="4",
        tier=tier,
        floor_regions=regions,
    )
    instance.cells = dense
    return instance


def _rect(x0, z0, x1, z1):
    return {"x0": x0, "z0": z0, "x1": x1, "z1": z1}


class _Link:
    """构造 ``TierLink`` 的最小辅助（避免每个用例重复写七行）。"""

    @staticmethod
    def build(link_id, from_tier, from_rect, to_tier, to_rect, *, bidirectional=True, cost=1.0, kind="stairs"):
        from src.nav.layered_map import TierLink, TierLinkEndpoint

        return TierLink(
            link_id=link_id,
            source=TierLinkEndpoint("map02", from_tier, "4", from_rect),
            target=TierLinkEndpoint("map02", to_tier, "4", to_rect),
            bidirectional=bidirectional,
            kind=kind,
            cost=cost,
        )


def _planner(layers, links, *, allow_unknown=False, max_expand=100_000):
    """按产品默认策略装一个分层规划器（测试里显式给策略，避免依赖库默认）。"""
    return LayeredPlanner(
        LayeredMap(layers=layers, links=tuple(links), catalog_loaded=True),
        planner_factory=lambda grid: GridPlanner(
            grid,
            allow_unknown=allow_unknown,
            risk_cost=5.0,
            margin=3,
            wall_penalty=1.0,
            frontier_margin=2,
            frontier_penalty=1.0,
            max_expand=max_expand,
        ),
    )


class TestSameLayer(unittest.TestCase):
    """同层起终点退化成一次普通层内规划。"""

    def setUp(self):
        self.main = _grid(["ooooo"], tier="")
        self.layers = {layer_key("map02", ""): self.main}

    def test_no_links_plans_directly(self):
        planner = _planner(self.layers, [])
        route = planner.plan([(0, 0)], [(0, 4)])
        self.assertTrue(route.ok, route.reason)
        self.assertEqual(len(route.segments), 1)
        self.assertEqual(route.links, ())
        self.assertAlmostEqual(route.cost, 4.0)

    def test_unreachable_same_layer_reports_not_ok(self):
        """同层但被阻挡隔断：返回 ok=False，不是抛错。"""
        layers = {layer_key("map02", ""): _grid(["o#o"], tier="")}
        planner = _planner(layers, [])
        route = planner.plan([(0, 0)], [(0, 2)])
        self.assertFalse(route.ok)
        self.assertIn("不可达", route.reason)

    def test_missing_tier_grid_is_reported(self):
        planner = _planner(self.layers, [])
        route = planner.plan([(0, 0)], [(0, 4)], goal_tier="nope")
        self.assertFalse(route.ok)
        self.assertIn("没有网格文件", route.reason)


class TestCrossLayer(unittest.TestCase):
    """跨层：层内行走 + 连接交替。"""

    def _two_layers(self):
        # 主图 1x5、分层 1x3；用一条 stairs 把 (0,4) 与 (0,0) 连起来
        main = _grid(["ooooo"], tier="")
        upper = _grid(["ooo"], tier="lv1", origin=(4.0, 0.0, 0.0))
        layers = {layer_key("map02", ""): main, layer_key("map02", "lv1"): upper}
        links = [
            _Link.build(
                "stairs1",
                "",
                _rect(4.0, 0.0, 5.0, 1.0),
                "lv1",
                _rect(4.0, 0.0, 5.0, 1.0),
                kind="stairs",
            )
        ]
        return layers, links

    def test_cross_layer_route_has_segment_link_segment(self):
        layers, links = self._two_layers()
        planner = _planner(layers, links)
        route = planner.plan([(0, 0)], [(0, 2)], start_tier="", goal_tier="lv1")
        self.assertTrue(route.ok, route.reason)
        self.assertEqual(len(route.segments), 2)
        self.assertEqual(len(route.links), 1)
        self.assertEqual(route.links[0].kind, "stairs")
        # 主图走 4 格 + 连接 1.0 + 分层走 2 格
        self.assertAlmostEqual(route.cost, 4.0 + 1.0 + 2.0)
        self.assertEqual(route.tiers, ["", "lv1"])

    def test_one_way_link_only_traversable_forward(self):
        layers, links = self._two_layers()
        links[0] = _Link.build(
            "rope",
            "",
            _rect(4.0, 0.0, 5.0, 1.0),
            "lv1",
            _rect(4.0, 0.0, 5.0, 1.0),
            bidirectional=False,
            kind="rope",
        )
        planner = _planner(layers, links)
        forward = planner.plan([(0, 0)], [(0, 2)], start_tier="", goal_tier="lv1")
        self.assertTrue(forward.ok, forward.reason)
        planner.invalidate_cache()
        backward = planner.plan([(0, 2)], [(0, 0)], start_tier="lv1", goal_tier="")
        self.assertFalse(backward.ok, "单向连接不该能被反向使用")

    def test_detour_through_a_layer_is_found(self):
        """主图两端被隔断、但都能通到分层：应当绕分层走过去。"""
        # 主图 "o###o"：两格互不相通；分层 1x3 两端各连一格
        main = _grid(["o###o"], tier="")
        upper = _grid(["ooo"], tier="lv1", origin=(4.0, 0.0, 0.0))
        layers = {layer_key("map02", ""): main, layer_key("map02", "lv1"): upper}
        links = [
            _Link.build("a", "", _rect(0.0, 0.0, 1.0, 1.0), "lv1", _rect(4.0, 0.0, 5.0, 1.0)),
            _Link.build("b", "", _rect(4.0, 0.0, 5.0, 1.0), "lv1", _rect(6.0, 0.0, 7.0, 1.0)),
        ]
        planner = _planner(layers, links)
        direct = _planner(layers, []).plan([(0, 0)], [(0, 4)])
        self.assertFalse(direct.ok, "主图两端本来就该是隔断的")

        route = planner.plan([(0, 0)], [(0, 4)])
        self.assertTrue(route.ok, route.reason)
        self.assertEqual(len(route.links), 2)
        self.assertEqual([link.link_id for link in route.links], ["a", "b"])
        # 两端都正好落在连接端点区域内，所以只有中间那段层内行走；零长段会被丢掉
        self.assertEqual([segment.tier for segment in route.segments], ["lv1"])
        self.assertAlmostEqual(route.cost, 2.0 + 1.0 + 1.0)


class TestRegionPassThrough(unittest.TestCase):
    """连接端点区域不能被当成"免费传送"：同层两格都要合并成一段并重算代价。"""

    def test_cells_inside_one_region_do_not_cost_zero(self):
        # 主图 1x3；连接端点区域把 (0,0)(0,1)(0,2) 全包进去
        main = _grid(["ooo"], tier="")
        other = _grid(["ooo"], tier="lv1", origin=(10.0, 0.0, 0.0))
        layers = {layer_key("map02", ""): main, layer_key("map02", "lv1"): other}
        links = [
            _Link.build("big", "", _rect(0.0, 0.0, 3.0, 1.0), "lv1", _rect(10.0, 0.0, 13.0, 1.0)),
        ]
        planner = _planner(layers, links)

        # 起终点都落在同一个端点区域内部：真实走法是 1 格，不能报 0
        route = planner.plan([(0, 0)], [(0, 1)])
        self.assertTrue(route.ok, route.reason)
        self.assertEqual(len(route.segments), 1, "连续同层行走必须合并成一段")
        self.assertAlmostEqual(route.cost, 1.0, msg="区域跨度不能被当成免费传送")

    def test_route_cost_equals_sum_of_segments_and_links(self):
        main = _grid(["oooo"], tier="")
        other = _grid(["oo"], tier="lv1", origin=(10.0, 0.0, 0.0))
        layers = {layer_key("map02", ""): main, layer_key("map02", "lv1"): other}
        links = [_Link.build("s", "", _rect(3.0, 0.0, 4.0, 1.0), "lv1", _rect(10.0, 0.0, 11.0, 1.0), cost=2.5)]
        planner = _planner(layers, links)
        route = planner.plan([(0, 0)], [(0, 1)], start_tier="", goal_tier="lv1")
        self.assertTrue(route.ok, route.reason)
        expected = sum(seg.cost for seg in route.segments) + sum(link.cost for link in route.links)
        self.assertAlmostEqual(route.cost, expected)


class TestUnreachablePocket(unittest.TestCase):
    """进不去的连通块：只有一个单向出口，从外面到不了它。"""

    def _pocket_map(self):
        # 主图 1x3；分层 lv1 分成两块：ooo（左）与 o（右，独立一格）
        # 只给"右块 -> 主图"一条单向绳，所以右块出得来、进不去
        main = _grid(["ooo"], tier="")
        other = _grid(["oo#o"], tier="lv1", origin=(10.0, 0.0, 0.0))
        layers = {layer_key("map02", ""): main, layer_key("map02", "lv1"): other}
        links = [
            _Link.build(
                "rope",
                "lv1",
                _rect(13.0, 0.0, 14.0, 1.0),
                "",
                _rect(2.0, 0.0, 3.0, 1.0),
                bidirectional=False,
                kind="rope",
            )
        ]
        return layers, links

    def test_cannot_enter_the_pocket(self):
        layers, links = self._pocket_map()
        planner = _planner(layers, links)
        route = planner.plan([(0, 0)], [(0, 3)], start_tier="", goal_tier="lv1")
        self.assertFalse(route.ok, "只有单向出口的块不该能被进入")
        self.assertIn("不可达", route.reason)
        self.assertIn("允许穿越未知格", route.reason, "reason 应当指出策略上的解法")

    def test_can_leave_the_pocket(self):
        layers, links = self._pocket_map()
        planner = _planner(layers, links)
        route = planner.plan([(0, 3)], [(0, 0)], start_tier="lv1", goal_tier="")
        self.assertTrue(route.ok, route.reason)
        self.assertEqual([link.kind for link in route.links], ["rope"])

    def test_unreachable_reason_does_not_blame_blocked_cells(self):
        """实测里隔开片区的是**未标注**格子而不是墙，文案不能说成"被阻挡隔断"。"""
        layers = {layer_key("map02", ""): _grid(["o#o"], tier="")}
        planner = _planner(layers, [])
        route = planner.plan([(0, 0)], [(0, 2)])
        self.assertFalse(route.ok)
        self.assertIn("块可行走区", route.reason, "应当给出可行走块数")
        self.assertIn("未标注", route.reason)
        self.assertIn("允许穿越未知格", route.reason, "应当给出策略上的解法")

    def test_walkable_blocks_matches_four_connected_count(self):
        """块数用四连通：只靠斜角相接的两片不该算成一块。"""
        from src.nav.layered_planner import _walkable_blocks

        # 两片只在对角相接
        grid = _grid(["o#", "#o"], tier="")
        blocks, biggest, total = _walkable_blocks(grid)
        self.assertEqual((blocks, biggest, total), (2, 1, 2))
        # 十字相接 -> 一块
        grid2 = _grid(["#o#", "ooo", "#o#"], tier="")
        self.assertEqual(_walkable_blocks(grid2), (1, 5, 5))

    def test_disconnected_layer_graph_is_tolerated(self):
        """层图不连通时 Dijkstra 要能容忍未达节点，而不是抛错。"""
        main = _grid(["o"], tier="")
        lv1 = _grid(["o"], tier="lv1", origin=(10.0, 0.0, 0.0))
        lv2 = _grid(["o"], tier="lv2", origin=(20.0, 0.0, 0.0))
        layers = {
            layer_key("map02", ""): main,
            layer_key("map02", "lv1"): lv1,
            layer_key("map02", "lv2"): lv2,
        }
        # lv2 与谁都连不上
        links = [_Link.build("x", "", _rect(0.0, 0.0, 1.0, 1.0), "lv1", _rect(10.0, 0.0, 11.0, 1.0))]
        planner = _planner(layers, links)
        route = planner.plan([(0, 0)], [(0, 0)], start_tier="", goal_tier="lv2")
        self.assertFalse(route.ok)
        self.assertIn("不可达", route.reason)


class TestCaching(unittest.TestCase):
    """分段结果要缓存；网格变了由调用方 invalidate。"""

    def test_segment_is_cached(self):
        main = _grid(["ooooo"], tier="")
        layers = {layer_key("map02", ""): main}
        planner = _planner(layers, [])
        first = planner.segment(layer_key("map02", ""), [(0, 0)], [(0, 4)])
        again = planner.segment(layer_key("map02", ""), [(0, 0)], [(0, 4)])
        self.assertIs(first, again, "同一查询应当命中缓存")
        # 集合内部顺序无关：同一个集合换个顺序也是同一查询
        third = planner.segment(layer_key("map02", ""), [(0, 0), (0, 1)], [(0, 4)])
        fourth = planner.segment(layer_key("map02", ""), [(0, 1), (0, 0)], [(0, 4)])
        self.assertIs(third, fourth, "集合内顺序不该影响缓存命中")

    def test_invalidate_drops_cache(self):
        main = _grid(["ooooo"], tier="")
        layers = {layer_key("map02", ""): main}
        planner = _planner(layers, [])
        first = planner.segment(layer_key("map02", ""), [(0, 0)], [(0, 4)])
        planner.invalidate_cache()
        after = planner.segment(layer_key("map02", ""), [(0, 0)], [(0, 4)])
        self.assertIsNot(first, after)


class TestDefaultPolicy(unittest.TestCase):
    """默认策略必须与产品默认一致：allow_unknown=False。

    用错会让"被未知格隔开的两块"被算成可达——这正是实测数据里那个进不去的块。
    """

    def test_default_planner_does_not_cross_unknown(self):
        # 主图 "o.o"：两格之间是未知格；allow_unknown=False 时不通
        main = _grid(["o.o"], tier="")
        planner = LayeredPlanner(LayeredMap(layers={layer_key("map02", ""): main}))
        route = planner.plan([(0, 0)], [(0, 2)])
        self.assertFalse(route.ok, "默认策略不该穿越未知格")

    def test_explicit_allow_unknown_can_bridge_them(self):
        main = _grid(["o.o"], tier="")
        planner = LayeredPlanner(
            LayeredMap(layers={layer_key("map02", ""): main}),
            planner_factory=lambda grid: GridPlanner(grid, allow_unknown=True, margin=3),
        )
        route = planner.plan([(0, 0)], [(0, 2)])
        self.assertTrue(route.ok, route.reason)


class TestRealData(unittest.TestCase):
    """本机有 nav-grid-data 时，用真实数据做端到端校验。"""

    @classmethod
    def setUpClass(cls):
        if not (_REAL_DATA / "links.json").is_file():
            raise unittest.SkipTest("本机没有 nav-grid-data")
        cls.layered = load_layered_map(_REAL_DATA, "map02", zoom="4")
        cls.planner = LayeredPlanner(cls.layered, max_expand=5_000_000)

    def test_cross_layer_route_to_each_neighbouring_tier(self):
        main = layer_key("map02", "")
        tips7 = layer_key("map02", "scene_map02_lv005_layer_tips_7")
        start = sorted(self._cells(main))[0]
        goal = sorted(self._cells(tips7))[0]
        route = self.planner.plan([start], [goal], start_tier="", goal_tier=tips7[1])
        self.assertTrue(route.ok, route.reason)
        self.assertIn(tips7[1], route.tiers)
        self.assertTrue(route.links)

    def test_route_cost_matches_its_parts(self):
        main = layer_key("map02", "")
        tips6 = layer_key("map02", "scene_map02_lv005_layer_tips_6")
        start = sorted(self._cells(main))[0]
        goal = sorted(self._cells(tips6))[0]
        route = self.planner.plan([start], [goal], start_tier="", goal_tier=tips6[1])
        if not route.ok:
            self.skipTest(f"本机数据下不可达：{route.reason}")
        expected = sum(seg.cost for seg in route.segments) + sum(link.cost for link in route.links)
        self.assertAlmostEqual(route.cost, expected)

    def _cells(self, key):
        from src.nav.layered_map import endpoint_cells

        out = set()
        for link in self.layered.links:
            for endpoint in (link.source, link.target):
                if endpoint.key == key:
                    out.update(endpoint_cells(self.layered.layers[key], endpoint.rect))
        return out


if __name__ == "__main__":
    unittest.main()
