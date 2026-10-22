"""分层导航规划：在「层内精确 A*」之上组合出跨层路线。

与 :mod:`src.nav.grid_planner` 的分工是刻意的——**扁平 A\\* 的热路径一行不改**：

- 层内怎么走，仍然由 :class:`~src.nav.grid_planner.GridPlanner` 负责（含滑索、禁斜穿、
  离墙代价、可达性判定等全部既有策略）；
- 本模块只在**层图**上组合：节点是"每条连接的两侧"，边权是该层内两个格集合之间的
  最短路径代价。层图很小（实测 7 条连接 → 16 个节点），所以外层枚举/最短路都便宜。

为什么这样组合是**精确最优**：任何一条跨层路线都必然对应层图上的一条路径，而连接代价
``cost`` 与"落在区域内哪一格"无关，因此段与段之间完全解耦 —— 层图上跑一次最短路就是
全局最优，不需要假设层图无环、也不用枚举层路径。

两个必须当成**常态**而非异常的情况（都来自实测数据）：

- **连接的某一侧可能从别处根本走不到**。实测 map02 的 tips_7 分成 3 个连通块
  （829 / 364 / 39 格），而那个 39 格的块只被一条**单向**绳索的起点碰到 —— 没有任何
  连接能进到它。所以"某节点对无解"必须返回不可达，不能抛错、也不能当成 0 代价。
- **层图本身可能不连通**，Dijkstra 要能容忍未达节点。

同理，主图的可行走区也分成 12 个块（最大 31918），产品默认 ``allow_unknown=False``
时**主图上的两个连接端点可能互不可达**（实测 door 端点在 674 格的小块里，与三个楼梯
端点所在的 4757 格块不通）。这种情况必须报"这条路走不通"，并让调用方知道是**策略**
（是否允许穿越未知格）导致的，而不是网格数据坏了。
"""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field

from src.nav.grid_planner import GridPlanner, PlanResult
from src.nav.layered_map import LayeredMap, LayerKey, TierLink, endpoint_cells, layer_key

__all__ = [
    "LayeredPlanner",
    "LayeredRoute",
    "LayeredSegment",
]


@dataclass(frozen=True)
class LayeredSegment:
    """一段**层内**行走：从某层的一个格集合走到同一层另一个格集合。"""

    key: LayerKey
    start_cells: tuple[tuple[int, int], ...]
    goal_cells: tuple[tuple[int, int], ...]
    plan: PlanResult

    @property
    def tier(self) -> str:
        return self.key[1]

    @property
    def cost(self) -> float:
        return float(self.plan.cost)

    @property
    def waypoints(self) -> list[tuple[float, float]]:
        return list(self.plan.waypoints)


@dataclass
class LayeredRoute:
    """一次跨层规划的结果：``segments`` 与 ``links`` 交替出现。"""

    ok: bool
    reason: str = ""
    #: 层内行走段，按顺序
    segments: tuple[LayeredSegment, ...] = ()
    #: 路线用到的全部连接（扁平视图，只用于日志与统计）
    links: tuple[TierLink, ...] = ()
    #: **第一段之前**要跨的连接。起点本身就压在某个连接端点上时会出现：那段"步行"长度为
    #: 零被丢掉，于是连接的跨越落在所有分段之前。不带走这一段就等于漏执行一次跨层。
    prefix: tuple[TierLink, ...] = ()
    #: **与 segments 对齐**：``transitions[i]`` 是走完第 i 段之后要跨的那些连接。
    #:
    #: 为什么不能直接用扁平的 ``links[i]`` 对应第 i 段：层图上的路径是一串"步行边 + 连接边"
    #: 交替的链，而连续的同一层步行会被**合并成一段**。于是两段之间可能夹着**多条**连接
    #: （例如 tips_7 -> tips_6 -> tips_7 这种来回），扁平表的长度就不等于段数。
    #: 真实数据上第一次跑就踩到了：2 段配 3 条连接，按 ``links[段号]`` 取会取错。
    transitions: tuple[tuple[TierLink, ...], ...] = ()
    cost: float = math.inf
    #: 起终点解析结果，便于调用方回报"我在第几层"
    start_key: LayerKey | None = None
    goal_key: LayerKey | None = None
    notes: list[str] = field(default_factory=list)
    #: 层图上的节点/边规模与搜索量，排查用
    expanded: int = 0

    @property
    def waypoints(self) -> list[tuple[float, float]]:
        """把各段航点接起来；段与段之间的连接端点由连接本身跨越。"""
        out: list[tuple[float, float]] = []
        for segment in self.segments:
            points = segment.waypoints
            if out and points and out[-1] == points[0]:
                points = points[1:]
            out.extend(points)
        return out

    @property
    def tiers(self) -> list[str]:
        """路线途经的层（按顺序，含重复）。"""
        return [segment.tier for segment in self.segments]

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "tiers": self.tiers,
            "segments": len(self.segments),
            "links": len(self.links),
            "transitions": [len(group) for group in self.transitions],
            "prefix": len(self.prefix),
            "cost": None if not self.ok else round(self.cost, 2),
            "reason": self.reason,
        }


class LayeredPlanner:
    """在 :class:`~src.nav.layered_map.LayeredMap` 上规划跨层路线。

    Args:
        layered: 分层地图（层 + 连接 + 可选目录）。
        planner_factory: 由层网格造 :class:`GridPlanner` 的工厂。**调用方应当把
            ``Nav Config`` 里的策略原样带进来**（见 :meth:`_default_planner` 的说明）；
            默认工厂只是"没有配置时"的兜底。
        max_expand: 传给层内搜索的节点上限。
    """

    def __init__(self, layered: LayeredMap, *, planner_factory=None, max_expand: int | None = None):
        self.layered = layered
        self._planner_factory = planner_factory or self._default_planner
        self._max_expand = max_expand
        self._planners: dict[LayerKey, GridPlanner] = {}
        self._segment_cache: dict[tuple, PlanResult] = {}
        self.expanded = 0

    def _default_planner(self, grid) -> GridPlanner:
        """没有注入配置时的兜底策略。

        **每一项都必须与 ``GridNavigationMixin._grid_follower_config`` 的取值一致。**
        少一项就会让"分段代价"与"跟随器实际怎么走"口径分叉，层图上的最短路也就不再是
        真实最优。最要命的一项是 ``allow_unknown``：``GridPlanner`` 的库默认是
        ``True``，而产品默认是 ``False``——用错会让分段搜索**穿过未知格**，于是"两个
        连通块之间不可达"会被算成可达（实测：map02 的 tips_7 里那个 39 格的块只有一条
        单向绳能出去，但在 allow_unknown=True 下从主图走过去会被判成可行）。

        调用方应当传 ``planner_factory`` 把 ``Nav Config`` 原样带进来；这里保持与产品
        默认相同的取值。
        """
        kwargs = {}
        if self._max_expand is not None:
            kwargs["max_expand"] = self._max_expand
        return GridPlanner(
            grid,
            risk_cost=5.0,
            diagonal=True,
            allow_unknown=False,
            margin=3,
            wall_penalty=1.0,
            frontier_margin=2,
            frontier_penalty=1.0,
            **kwargs,
        )

    def invalidate_cache(self) -> None:
        """丢弃层内分段与层内规划器的缓存。

        什么时候必须调：**网格内容变了**。实测这份数据在协作期间就被人往 tips_7 加了
        26 格、tips_6 加了 14 格、主图加了 6 格——格子数变了，旧的段内代价就失效了。
        判断依据建议用网格文件的 mtime，**不要**用 ``links.json`` 的 mtime：它每次保存
        都会盖章 ``updated_at``，但端点常常没变。
        """
        self._planners.clear()
        self._segment_cache.clear()

    def planner_for(self, key: LayerKey) -> GridPlanner | None:
        grid = self.layered.layers.get(key)
        if grid is None:
            return None
        planner = self._planners.get(key)
        if planner is None:
            planner = self._planner_factory(grid)
            self._planners[key] = planner
        return planner

    def segment(
        self,
        key: LayerKey,
        start_cells,
        goal_cells,
        *,
        time_budget_s: float | None = None,
    ) -> PlanResult:
        """层内分段代价（带缓存）。同一对格集合只算一次。"""
        cache_key = (key, tuple(sorted(start_cells)), tuple(sorted(goal_cells)))
        cached = self._segment_cache.get(cache_key)
        if cached is not None:
            return cached
        planner = self.planner_for(key)
        if planner is None:
            result = PlanResult(False, f"缺少层 {key[1] or '主图'!r} 的网格")
        else:
            result = planner.plan_multi(start_cells, goal_cells, time_budget_s=time_budget_s)
        self._segment_cache[cache_key] = result
        return result

    def plan(
        self,
        start_cells,
        goal_cells,
        *,
        start_tier: str = "",
        goal_tier: str = "",
        time_budget_s: float | None = None,
    ) -> LayeredRoute:
        """规划跨层路线。

        Args:
            start_cells: 起点格集合（``(i, j)`` 下标）。给集合而不是单格，是因为调用方
                常常只知道"我在这一片"；集合内取哪个格由搜索决定。
            goal_cells: 终点格集合。
            start_tier / goal_tier: 起点与终点所在的层（空串是主图）。由调用方用
                :func:`~src.nav.layered_map.resolve_tier_for_point` 从三维坐标解析出来——
                两层 X/Z 重叠时，光有 x/z 无法判断玩家在第几层，这件事必须在进本模块
                之前解决。
            time_budget_s: 折算给**每一次**分段搜索的时间预算。

        Returns:
            :class:`LayeredRoute`。不可达时 ``ok=False``，``reason`` 说明是"策略导致"
            （不允许穿越未知格）还是"确实隔断"。
        """
        map_name = next(iter(self.layered.layers), ("", ""))[0]
        start_key = layer_key(map_name, start_tier)
        goal_key = layer_key(map_name, goal_tier)
        for key, label in ((start_key, "起点"), (goal_key, "终点")):
            if key not in self.layered.layers:
                return LayeredRoute(
                    False,
                    f"{label}所在的层 {key[1] or '主图'!r} 没有网格文件",
                    start_key=start_key,
                    goal_key=goal_key,
                )

        nodes = self._build_nodes(start_key, goal_key, start_cells, goal_cells)
        if len(nodes) == 2:
            # 这张图没有任何连接：退化成一次普通层内规划，不经过层图
            return self._direct_route(nodes, start_key, goal_key, time_budget_s)

        index_of_goal = len(nodes) - 1
        adjacency = self._build_adjacency(nodes)
        came, cost = self._dijkstra(nodes, adjacency, index_of_goal, time_budget_s)
        if index_of_goal not in cost:
            return LayeredRoute(
                False,
                self._unreachable_hint(start_key, goal_key),
                start_key=start_key,
                goal_key=goal_key,
                expanded=self.expanded,
            )
        return self._materialize(nodes, came, cost, index_of_goal, start_key, goal_key, time_budget_s)

    def _unreachable_hint(self, start_key: LayerKey, goal_key: LayerKey) -> str:
        """区分"同一层内走不通"与"层图里凑不出组合"，并把实测到的原因说清楚。

        实测数据里最常见的原因是**同一层被涂成了多个片区**：map02 主图有 12 个可行走块，
        涂得最多的那块占 76.8%，而 7 条连接只碰到其中 2 块。片区之间往往**没有任何阻挡格**，
        只是中间 38~245 格没涂（实测块#5 与块#8 的最近点对之间 0 阻挡、36 未知）——所以
        把原因说成"被阻挡隔断"是错的，会把人引向错误的方向去查。
        """
        same_layer = start_key == goal_key
        grid = self.layered.layers.get(start_key)
        parts = []
        parts.append(
            "不可达：起点与终点不在同一块可行走区域"
            if same_layer and grid is not None
            else "不可达：层图里凑不出从起点到终点的可行组合"
        )
        if grid is not None:
            blocks, biggest, total = _walkable_blocks(grid)
            share = f"，占 {biggest / total * 100:.1f}%" if total else ""
            parts.append(f"{start_key[1] or '主图'}共 {blocks} 块可行走区，最大块 {biggest} 格{share}")
        parts.append(
            "片区之间隔着未标注的格子（不是阻挡格）：需要在编辑器里补涂，"
            "或确认这里本来就该绕行/传送；开启「允许穿越未知格」可临时穿过未探索区域"
        )
        return "；".join(parts) + "。"

    # ------------------------------------------------------------------ #
    # 层图
    # ------------------------------------------------------------------ #
    @dataclass(frozen=True)
    class _Node:
        """层图节点：起点 / 终点，或某条连接的一侧。"""

        key: LayerKey
        cells: tuple[tuple[int, int], ...]
        link_index: int = -1
        #: ``"from"`` / ``"to"``；起终点为空串
        side: str = ""

    def _build_nodes(self, start_key, goal_key, start_cells, goal_cells) -> list:
        nodes = [self._Node(start_key, tuple(start_cells))]
        for index, link in enumerate(self.layered.links):
            for side, endpoint in (("from", link.source), ("to", link.target)):
                grid = self.layered.layers.get(endpoint.key)
                if grid is None:
                    # 端点所在层没有网格 -> 该侧不可用；照旧放一个空集合节点，
                    # 后续建边时它连不上任何东西，等价于"这条连接走不了"。
                    nodes.append(self._Node(endpoint.key, (), index, side))
                    continue
                nodes.append(self._Node(endpoint.key, endpoint_cells(grid, endpoint.rect), index, side))
        nodes.append(self._Node(goal_key, tuple(goal_cells)))
        return nodes

    def _build_adjacency(self, nodes) -> dict[int, list]:
        """层图的边：同层两节点之间可层内行走，连接的两侧之间可乘索/走楼梯。"""
        adjacency: dict[int, list] = {index: [] for index in range(len(nodes))}

        # 同层内两两可达（含起点/终点）。段代价惰性求值——只有 Dijkstra 真的松弛到时才算。
        live = [index for index, node in enumerate(nodes) if node.cells]
        by_layer: dict[LayerKey, list[int]] = {}
        for index in live:
            by_layer.setdefault(nodes[index].key, []).append(index)
        for indices in by_layer.values():
            for a, b in itertools.permutations(indices, 2):
                adjacency[a].append((b, ("segment", a, b)))

        # 连接本身：两侧之间一步跨越
        for index, node in enumerate(nodes):
            if node.link_index < 0 or not node.cells:
                continue
            link = self.layered.links[node.link_index]
            other_index = 2 * node.link_index + (2 if node.side == "from" else 1)
            if not nodes[other_index].cells:
                continue
            # 方向必须随边一起记：同一个双向连接，反向跨越时执行方位是反的
            # （_cross_layered_link 按 source.center -> target.center 算方位与距离）。
            forward = node.side == "from"
            if forward or link.bidirectional:
                adjacency[index].append((other_index, ("link", node.link_index, forward)))
        return adjacency

    def _dijkstra(self, nodes, adjacency, goal_index, time_budget_s):
        """层图上的惰性 Dijkstra。未达节点不出现在返回值里（层图可能不连通）。"""
        dist: dict[int, float] = {0: 0.0}
        came: dict[int, tuple[int, object]] = {}
        heap = [(0.0, 0)]
        done: set[int] = set()
        self.expanded = 0
        while heap:
            current_cost, index = heapq.heappop(heap)
            if index in done:
                continue
            done.add(index)
            self.expanded += 1
            if index == goal_index:
                break
            for neighbor, edge in adjacency.get(index, ()):
                step = self._edge_cost(nodes, edge, time_budget_s)
                if step is None:
                    continue
                tentative = current_cost + step
                if tentative < dist.get(neighbor, math.inf) - 1e-12:
                    dist[neighbor] = tentative
                    came[neighbor] = (index, edge)
                    heapq.heappush(heap, (tentative, neighbor))
        return came, dist

    def _edge_cost(self, nodes, edge, time_budget_s) -> float | None:
        """一条层图边的代价；不可走时返回 ``None``（调用方跳过，不抛错）。"""
        if edge[0] == "link":
            return float(self.layered.links[edge[1]].cost)
        _kind, a, b = edge
        plan = self.segment(nodes[a].key, nodes[a].cells, nodes[b].cells, time_budget_s=time_budget_s)
        return float(plan.cost) if plan.ok else None

    def _direct_route(self, nodes, start_key, goal_key, time_budget_s) -> LayeredRoute:
        plan = self.segment(start_key, nodes[0].cells, nodes[-1].cells, time_budget_s=time_budget_s)
        if not plan.ok:
            # 复用同一套诊断：这里同样是"片区之间没连通"，而不该说成"被阻挡隔断"
            return LayeredRoute(
                False,
                self._unreachable_hint(start_key, goal_key),
                start_key=start_key,
                goal_key=goal_key,
                expanded=self.expanded,
            )
        segment = LayeredSegment(start_key, tuple(nodes[0].cells), tuple(nodes[-1].cells), plan)
        return LayeredRoute(
            True,
            segments=(segment,),
            cost=plan.cost,
            start_key=start_key,
            goal_key=goal_key,
            expanded=self.expanded,
        )

    def _materialize(self, nodes, came, dist, goal_index, start_key, goal_key, time_budget_s) -> LayeredRoute:
        """按 Dijkstra 前驱还原出「段 → 连接 → 段」序列。

        还原后会把**连续的同层段合并成一段**并重算代价。为什么必须合并：
        ``plan_multi`` 的量是"到集合中最近一格"的距离，它**不满足可加性**——一个连接
        端点区域覆盖多格，若起点与终点恰好都落在同一区域内，"经该节点中转"会被算成
        ``0 + 0 = 0``，而真实走法是两点之间的一步（实测 1.0）。这种中转是图上的假捷径：
        它把区域自身的跨度当成了免费传送。合并之后报出的代价是真实的（对上面的例子
        回到 1.0）。

        残余限制（已知，未解决）：Dijkstra **选路**时仍可能因为这段低估而偏好一条绕经
        某个端点区域的路线。发生条件很窄——起终点都要落在同一个连接端点区域内部——且
        合并后代价会被算回真值，所以不会把错误代价报给调用方。彻底修法是把每个连接侧
        按格展开成节点，那会让同层需比较的节点对从 ~85 涨到 ~3600，单次规划从几十毫秒
        涨到几十秒，不划算。
        """
        chain: list[object] = []
        cursor = goal_index
        while cursor in came:
            previous, edge = came[cursor]
            chain.append(edge)
            cursor = previous
        chain.reverse()

        segments: list[LayeredSegment] = []
        links: list[TierLink] = []
        transitions: list[tuple[TierLink, ...]] = []
        notes: list[str] = []
        pending: list[TierLink] = []
        prefix: list[TierLink] = []

        def oriented(edge) -> TierLink:
            """按这条边实际的跨越方向取连接（反向跨越要取 reversed 的方位）。"""
            link = self.layered.links[edge[1]]
            forward = edge[2] if len(edge) > 2 else True
            return link if forward else link.reversed

        run_start: int | None = None
        run_end: int | None = None

        def flush() -> bool:
            """把一段连续的同层行走落成单个分段；跳过没有实际行走的（同一节点）。

            同时把"这段之前攒下的连接"登记成上一段的跨层动作——``pending`` 里的连接正是
            从上一段终点走到这一段起点途中乘坐的。
            """
            nonlocal run_start, run_end
            if run_start is None or run_end is None:
                return True
            if run_start == run_end:
                run_start = run_end = None
                return True
            plan = self.segment(
                nodes[run_start].key,
                nodes[run_start].cells,
                nodes[run_end].cells,
                time_budget_s=time_budget_s,
            )
            if not plan.ok:
                notes.append(f"分段在还原阶段不可达：{plan.reason}")
                run_start = run_end = None
                return False
            if plan.cost <= 1e-9:
                # 零长段（起点已在连接端点区域内时会出现）：不是"一步"，丢掉。
                # 保留它会让路线多出"原地不动"的阶段，也会让只用于展示的 tiers 出现
                # 重复层——代价不受影响，但不该出现在结果里。
                run_start = run_end = None
                return True
            if segments:
                # 上一段与本段之间的连接（含跨多层时不走路的连续跨越）
                transitions.append(tuple(pending))
            else:
                # 还没有任何分段：这些连接在第一段之前，单独记成 prefix
                prefix.extend(pending)
            pending.clear()
            segments.append(
                LayeredSegment(
                    nodes[run_start].key,
                    tuple(nodes[run_start].cells),
                    tuple(nodes[run_end].cells),
                    plan,
                )
            )
            run_start = run_end = None
            return True

        for edge in chain:
            if edge[0] == "link":
                if not flush():
                    return LayeredRoute(False, notes[-1], start_key=start_key, goal_key=goal_key, notes=notes)
                link = oriented(edge)
                links.append(link)
                pending.append(link)
                continue
            _kind, a, b = edge
            if run_start is None or nodes[run_start].key != nodes[a].key:
                if not flush():
                    return LayeredRoute(False, notes[-1], start_key=start_key, goal_key=goal_key, notes=notes)
                run_start = a
            run_end = b
        if not flush():
            return LayeredRoute(False, notes[-1], start_key=start_key, goal_key=goal_key, notes=notes)

        if not segments:
            return LayeredRoute(
                True,
                segments=(),
                links=tuple(links),
                prefix=tuple(prefix) or tuple(pending),
                transitions=(),
                cost=float(sum(link.cost for link in links)),
                start_key=start_key,
                goal_key=goal_key,
                notes=notes,
                expanded=self.expanded,
            )
        # 代价按**合并后的真实分段**求和，而不是 Dijkstra 报的值：后者含有上面说的假捷径
        total = sum(segment.cost for segment in segments) + sum(link.cost for link in links)
        return LayeredRoute(
            True,
            segments=tuple(segments),
            links=tuple(links),
            prefix=tuple(prefix),
            # 每段一个槽位；末尾那段之后若还有连接（落点即目标），记在最后一个槽位里，
            # 由任务层判定"跨完就结束"。
            transitions=tuple(transitions) + (tuple(pending),) * (len(segments) - len(transitions)),
            cost=float(total),
            start_key=start_key,
            goal_key=goal_key,
            notes=notes,
            expanded=self.expanded,
        )


def _walkable_blocks(grid) -> tuple[int, int, int]:
    """返回 ``(可行走连通块数, 最大块格数, 可行走总格数)``；只用于诊断文案，不参与规划。

    口径与编辑器的保存告警一致（"可行走分成 N 块，最大块 X%"），这样用户在两处看到的
    数字与措辞是同一套，不会以为是两个不同的问题。

    这里用**四连通**，而规划器用带拐角守卫的八连通——两者给出**完全相同**的连通块划分
    （证明见 :meth:`~src.nav.grid_planner.GridPlanner._corner_ok`），所以这个计数与
    规划器的可达性不会有口径分歧；四连通连守卫都不需要，作为诊断更省心。

    用四连通计数（与编辑器 ``walkableComponents`` 的口径一致）：八连通会把只靠一个
    斜角相接的两片算成一块，而那两片之间其实走不过去。
    """
    import numpy as np

    cells = grid.cells
    free = cells == 1
    total = int(free.sum())
    if total == 0:
        return 0, 0, 0
    seen = np.zeros(cells.shape, dtype=bool)
    blocks = 0
    biggest = 0
    rows, cols = np.nonzero(free)
    for r0, c0 in zip(rows.tolist(), cols.tolist(), strict=True):
        if seen[r0, c0]:
            continue
        blocks += 1
        size = 0
        stack = [(r0, c0)]
        seen[r0, c0] = True
        while stack:
            i, j = stack.pop()
            size += 1
            if i > 0 and free[i - 1, j] and not seen[i - 1, j]:
                seen[i - 1, j] = True
                stack.append((i - 1, j))
            if i + 1 < cells.shape[0] and free[i + 1, j] and not seen[i + 1, j]:
                seen[i + 1, j] = True
                stack.append((i + 1, j))
            if j > 0 and free[i, j - 1] and not seen[i, j - 1]:
                seen[i, j - 1] = True
                stack.append((i, j - 1))
            if j + 1 < cells.shape[1] and free[i, j + 1] and not seen[i, j + 1]:
                seen[i, j + 1] = True
                stack.append((i, j + 1))
        biggest = max(biggest, size)
    return blocks, biggest, total
