"""导航规划：在 :class:`~src.nav.grid_io.DenseGrid` 上做 A* 寻路。

为什么未知格要"可冒险、按风险加价"
----------------------------------
网格里可行走是**已知走过**的稀疏种子（实测 ``base01_4``：可行走 8.3%、阻挡 51.9%、
未知 39.8%）。只走可行走几乎无路可走；把未知当可行走又会直接穿墙。所以：

- 可行走：代价 1（斜向 √2）
- 未知：代价 × ``risk_cost``（默认 5），即"允许冒险穿过没探过的地方，但绕路更划算"
- 阻挡：不可通行
- 离墙不足 ``margin`` 格：每缺一格增加 ``wall_penalty``，只影响路线偏好，不封路
- 已知自由格靠近未知边缘：按缺口格数增加 ``frontier_penalty``，尽量走已探索区域内部

规划结果里会给出 ``risk_cells``（路径穿过多少个未知格），调用方据此决定要不要冒险。

大图触顶后的降级搜索
--------------------
启发式用的是普通 octile（按单步最小代价 1 估），而未知格的真实步进代价是
``risk_cost`` 倍。在一张 99.9% 都是未知的大图上，估价会低到 ``risk_cost`` 分之一，
A* 于是退化成"搜遍整片可走区域"的 Dijkstra，必然撞上 ``max_expand``。

所以 :meth:`GridPlanner.plan_cells` 先用 ``heuristic_weight``（默认 1.0，最优搜索）
搜一次；只有**因为触顶而失败**时，才自动按 ``risk_cost`` 加权重搜一次
（``f = g + w·h``，见 ``adaptive_weight``）。权重只影响扩展顺序，不改通行判定——
降级出来的路线仍然不穿墙、不切角，只是代价不再保证最优（上界约 ``risk_cost`` 倍）。
降级成功会在 ``PlanResult.notes`` 里留一条说明，不会无声发生。

用法::

    from src.nav import load_grid
    from src.nav.grid_planner import GridPlanner

    grid = load_grid("assets/nav/base01_4.grid.npz")
    planner = GridPlanner(grid, margin=2)          # 离墙不足 2 格的路优先避开，但不会封死
    res = planner.plan(start_world=(-40.0, -30.0), goal_world=(60.0, 68.0))
    if res.ok:
        for x, z in res.waypoints:                 # 世界坐标航点（已做视线简化）
            ...
        print(res.cost, res.risk_cells)

``plan`` 收发的是**世界坐标**（调用方手里就是实时定位给的世界坐标）；
要按格下标规划用 :meth:`GridPlanner.plan_cells`。
"""

from __future__ import annotations

import heapq
import itertools
import math
from array import array
from dataclasses import dataclass, field

import numpy as np

from src.nav.grid_io import (
    CELL_BLOCKED,
    CELL_FREE,
    CELL_NAMES,
    CELL_UNKNOWN,
    DIRS4,
    DIRS8,
    DenseGrid,
)

__all__ = [
    "DEFAULT_FRONTIER_PENALTY",
    "DEFAULT_RISK_COST",
    "DEFAULT_WALL_PENALTY",
    "GridPlanner",
    "PlanResult",
]

#: 穿过一个未知格的代价倍数（相对可行走的 1）
DEFAULT_RISK_COST = 5.0
#: 离墙距离每缺一格的附加代价；只影响路线偏好，不阻挡通行
DEFAULT_WALL_PENALTY = 1.0
#: 已知自由格距未知边缘每缺一格的附加代价；只影响路线偏好，不阻挡通行
DEFAULT_FRONTIER_PENALTY = 1.0
_SQRT2 = math.sqrt(2.0)


def _as_doubles(values: np.ndarray) -> array:
    """把 float64 ndarray 原样搬进 ``array('d')``。

    A* 热路径上 ``array('d')[i]`` 返回 Python float，比 numpy 标量取数快，也没有
    逐元素 Python 对象的开销（同样 500 万格，list 要 160+ MB，``array('d')`` 只要 42 MB）。
    """
    out = array("d")
    out.frombytes(np.ascontiguousarray(values, dtype=np.float64).tobytes())
    return out


@dataclass
class PlanResult:
    """规划结果。``ok=False`` 时 ``reason`` 说明原因，其余字段为空。"""

    ok: bool
    reason: str = ""
    #: 逐格路径（含起终点），元素是 ``(i, j)`` 数组下标
    cells: list = field(default_factory=list)
    #: 世界坐标航点（含起终点），已做视线简化
    waypoints: list = field(default_factory=list)
    cost: float = 0.0
    #: 路径里有多少格是未知（冒险穿过的格数）
    risk_cells: int = 0
    #: 搜索规模，便于调参（扩展了多少节点）
    expanded: int = 0
    #: 是否因扩展节点数触顶而放弃（与"真的没通路"区分，见 reason）
    cap_exceeded: bool = False
    #: 实际使用的起/终点格（起终点被判为不可通行时会挪到最近的格）
    start_cell: tuple | None = None
    goal_cell: tuple | None = None
    #: 起/终点被挪动时的说明，交给调用方记日志
    notes: list = field(default_factory=list)

    def __repr__(self) -> str:
        if not self.ok:
            return f"PlanResult(ok=False, reason={self.reason!r})"
        return (f"PlanResult(ok=True, cells={len(self.cells)}, waypoints={len(self.waypoints)}, "
                f"cost={self.cost:.1f}, risk_cells={self.risk_cells}, expanded={self.expanded})")


class GridPlanner:
    """在 :class:`DenseGrid` 上做 A*。网格与参数都不可变，可复用同一个实例多次规划。"""

    def __init__(self, grid: DenseGrid, *, risk_cost: float = DEFAULT_RISK_COST,
                 diagonal: bool = True, allow_unknown: bool = True, margin: int = 0,
                 wall_penalty: float = DEFAULT_WALL_PENALTY,
                 frontier_margin: int = 0,
                 frontier_penalty: float = DEFAULT_FRONTIER_PENALTY,
                 waypoint_tolerance: float = 2.0,
                 max_expand: int = 400_000,
                 heuristic_weight: float = 1.0,
                 adaptive_weight: bool = True):
        """
        Args:
            grid: 网格（不会被改动）。
            risk_cost: 穿过未知格的代价倍数（``allow_unknown=False`` 时无意义）。
            diagonal: 八连通（斜向代价 √2，且禁止斜穿墙角）还是四连通。
            allow_unknown: 是否允许走未知格。``False`` 时只在已知可行走里规划。
            margin: 期望的离墙安全距离（格）。离墙不足的格子仍可通行，但会按缺口
                格数增加代价，因此 A* 会尽量避开；没有替代路线时仍允许规划通过。
            wall_penalty: 离墙距离每缺一格的附加代价；``0`` 表示不启用该偏好。
            frontier_margin: 已知自由格期望远离未知边缘的距离（格）。
            frontier_penalty: 距未知边缘每缺一格的附加代价。
            waypoint_tolerance: 规划后允许用视线替换折线的最大横向误差（米）。
            max_expand: 扩展节点数上限，防止在超大图上失控。触顶时
                :meth:`plan_cells` 会给出与"真的没通路"不同的理由。
            heuristic_weight: A* 启发式的放大系数（``f = g + w·h``）。``1.0`` 是最优搜索；
                大于 1 会更快但代价不再最优（上界 w 倍）。
            adaptive_weight: :meth:`plan_cells` 里 ``heuristic_weight`` 那次搜索触顶时，
                是否自动按 ``risk_cost`` 加权重搜一次（见类文档"大图触顶后的降级搜索"）。
        """
        self.grid = grid
        self.risk_cost = float(risk_cost)
        self.diagonal = bool(diagonal)
        self.allow_unknown = bool(allow_unknown)
        self.margin = max(0, int(margin))
        self.wall_penalty = max(0.0, float(wall_penalty))
        # 只需要 min(距离, margin)，所以波前扩散到 margin 层就够——大图上这是秒级 vs 毫秒级的差别
        self._clearance = (
            grid.clearance(max_dist=self.margin)
            if self.margin > 0 and self.wall_penalty > 0
            else None
        )
        self.frontier_margin = max(0, int(frontier_margin))
        self.frontier_penalty = max(0.0, float(frontier_penalty))
        self._frontier_clearance = (
            grid.frontier_clearance(max_dist=self.frontier_margin)
            if self.frontier_margin > 0 and self.frontier_penalty > 0
            else None
        )
        self.waypoint_tolerance = max(0.0, float(waypoint_tolerance))
        self.max_expand = int(max_expand)
        self.heuristic_weight = max(1.0, float(heuristic_weight))
        self.adaptive_weight = bool(adaptive_weight)
        self._expanded = 0
        self._cap_exceeded = False
        self._tables = None

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def plan(self, start_world, goal_world) -> PlanResult:
        """按世界坐标规划：``(x, z)`` 元组。坐标非有限时直接返回失败，不抛异常。"""
        for label, point in (("起点", start_world), ("终点", goal_world)):
            if not (math.isfinite(float(point[0])) and math.isfinite(float(point[1]))):
                return PlanResult(False, f"{label}坐标非有限: {tuple(point)!r}")
        start = self.grid.index_of_world(start_world[0], start_world[1])
        goal = self.grid.index_of_world(goal_world[0], goal_world[1])
        return self.plan_cells(start, goal)

    def plan_cells(self, start, goal) -> PlanResult:
        """按数组下标 ``(i, j)`` 规划。"""
        start_cell, note_start = self._snap(start, "起点")
        if start_cell is None:
            return PlanResult(False, note_start)
        goal_cell, note_goal = self._snap(goal, "终点")
        if goal_cell is None:
            return PlanResult(False, note_goal)
        notes = [n for n in (note_start, note_goal) if n]

        cells = self._astar(start_cell, goal_cell, self.heuristic_weight)
        if cells is None:
            first_expanded, first_cap = self._expanded, self._cap_exceeded
            if self._weighted_retry_allowed():
                cells = self._astar(start_cell, goal_cell, self.risk_cost)
                if cells is not None:
                    notes.append(
                        f"精确搜索触顶（扩展 {first_expanded} 格）后改用加权启发 "
                        f"w={self.risk_cost:g} 重搜成功：路线仍不穿墙、不切角，"
                        f"但代价不再保证最优（上界约 {self.risk_cost:g} 倍）"
                    )
                else:
                    # 加权重搜也没找到：以首次精确搜索的结论为准——它才是"要不要调大
                    # max_expand"的依据，重搜的扩展数只会把诊断带偏。
                    self._expanded, self._cap_exceeded = first_expanded, first_cap
        if cells is None:
            # 三种"没找到路"必须区分开，否则调用方无法判断该改什么：
            #   1) 触顶            -> 调大 max_expand
            #   2) 关了穿越未知格  -> 该图未探索部分走不了（策略性不可达，不是数据坏了）
            #   3) 可达区域穷尽    -> 真的被阻挡隔断
            if self._cap_exceeded:
                reason = (f"搜索规模超限：扩展节点超过 max_expand={self.max_expand} 仍未找到通路，"
                          "可能是目标确实不可达，或网格过大/未探索部分太多；可调大 max_expand")
            elif not self.allow_unknown:
                reason = (f"找不到通路（起点可达区域已穷尽，共 {self._expanded} 格）："
                          "当前「允许穿越未知格」关闭，只在已知可行走区内搜索，"
                          "起点与终点不在同一个已知可行走区；该图未探索的部分无法通行。"
                          "若这张图还没充分探索，需要先补网格，或打开「允许穿越未知格」")
            else:
                reason = (f"找不到通路（起点可达区域已穷尽，共 {self._expanded} 格）："
                          "可行走区域不连通，或全部需要穿过阻挡")
            return PlanResult(False, reason,
                              start_cell=start_cell, goal_cell=goal_cell, notes=notes,
                              expanded=self._expanded, cap_exceeded=self._cap_exceeded)

        waypoints = [self.grid.world_of_index(i, j) for i, j in self._simplify(cells)]
        cost, risk = 0.0, 0
        for (ai, aj), (bi, bj) in itertools.pairwise(cells):
            state = self.grid.state(bi, bj)
            cost += self._step_cost(ai, aj, bi, bj, state)
            risk += 1 if state == CELL_UNKNOWN else 0
        return PlanResult(True, "", cells=cells, waypoints=waypoints, cost=cost,
                          risk_cells=risk, expanded=self._expanded,
                          start_cell=start_cell, goal_cell=goal_cell, notes=notes)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _passable(self, i: int, j: int) -> bool:
        state = self.grid.state(i, j)
        if state == CELL_FREE:
            return True
        return self.allow_unknown and state == CELL_UNKNOWN

    def passable(self, i: int, j: int) -> bool:
        """``_passable`` 的公开包装，供航点跟随器复用同一通行语义。"""
        return self._passable(i, j)

    def line_clear(self, a, b) -> bool:
        """``_line_clear`` 的公开包装：两格之间可否安全直连。"""
        return self._line_clear(a, b)

    def cells_risk(self, cells) -> int:
        """一串格里有多少个未知格。直线风险与被替换路径段的风险共用同一口径。"""
        return sum(1 for i, j in cells if self.grid.state(i, j) == CELL_UNKNOWN)

    def line_risk(self, a, b) -> int:
        """两格之间直线采样经过的未知格数。"""
        return self.cells_risk(self._line_cells(a, b))

    def _corner_ok(self, i: int, j: int, di: int, dj: int) -> bool:
        """斜向一步 ``(di, dj)`` 的两条正交邻格是否都允许通行（禁斜穿墙角）。

        **策略层的唯一权威实现**。``DenseGrid.neighbors`` 的禁斜穿只按"阻挡"粗筛
        （它不知道 ``allow_unknown`` 策略）；``allow_unknown=False`` 时未知格视同
        阻挡格，也**不许切角**，所以这里必须用 :meth:`_passable` 而不是"非阻挡"，
        否则 A* 会斜着挤过未知格的角。
        """
        return self._passable(i + di, j) and self._passable(i, j + dj)

    def _step_cost(self, ai: int, aj: int, bi: int, bj: int, state: int) -> float:
        base = _SQRT2 if (ai != bi and aj != bj) else 1.0
        if state == CELL_UNKNOWN:
            base *= self.risk_cost
        return base + self._wall_cost(bi, bj) + self._frontier_cost(bi, bj, state)

    def _wall_cost(self, i: int, j: int) -> float:
        """Soft preference for staying away from blocked cells."""
        if self._clearance is None:
            return 0.0
        clearance = int(self._clearance[i, j])
        if clearance < 0:
            return 0.0
        missing = max(0, self.margin - clearance)
        return missing * self.wall_penalty

    def _frontier_cost(self, i: int, j: int, state: int) -> float:
        """Soft preference for interior known-free cells away from unknown edges."""
        if self._frontier_clearance is None or state != CELL_FREE:
            return 0.0
        clearance = int(self._frontier_clearance[i, j])
        if clearance < 0:
            return 0.0
        missing = max(0, self.frontier_margin - clearance)
        return missing * self.frontier_penalty

    def _snap(self, cell, label: str):
        """起终点落在不可通行格（未知且不允许穿越 / 阻挡 / 界外）时，移到最近的可用格。

        挪动不无声：note 带上原格状态与偏移格数交给调用方记日志。起点被悄悄挪到十几格
        之外，等于从另一个位置开始规划，这是排查"规划失败"时最容易漏掉的信息。
        """
        i, j = int(cell[0]), int(cell[1])
        if self._passable(i, j):
            return (i, j), ""
        state_name = (
            CELL_NAMES.get(self.grid.state(i, j), "未知")
            if self.grid.in_bounds(i, j) else "界外"
        )
        if self.allow_unknown:
            target = self._nearest_passable(i, j, max_radius=16)
        else:
            target = self.grid.nearest_free(i, j, max_radius=16)
        if target is None:
            return None, (f"{label} {cell} 是{state_name}格、不可通行，"
                          "附近 16 格内找不到可站的格（是否传错地图/坐标？）")
        offset = math.hypot(target[0] - i, target[1] - j)
        return target, (f"{label} {cell} 是{state_name}格，"
                        f"已挪到最近的可通行格 {target}（偏移 {offset:.0f} 格）")

    def _nearest_passable(self, i: int, j: int, max_radius: int = 16):
        for radius in range(1, max_radius + 1):
            best = None
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    if max(abs(di), abs(dj)) != radius:
                        continue
                    ni, nj = i + di, j + dj
                    if self._passable(ni, nj):
                        d2 = di * di + dj * dj
                        if best is None or d2 < best[0]:
                            best = (d2, ni, nj)
            if best is not None:
                return (best[1], best[2])
        return None

    @staticmethod
    def _heuristic(ai: int, aj: int, bi: int, bj: int, diagonal: bool,
                   weight: float = 1.0) -> float:
        di, dj = abs(bi - ai), abs(bj - aj)
        if diagonal:
            # 八连通的可采纳启发：octile 距离（单步最小代价为 1）
            return ((di + dj) + (_SQRT2 - 2.0) * min(di, dj)) * weight
        return float(di + dj) * weight

    def _weighted_retry_allowed(self) -> bool:
        """触顶失败后是否值得按 ``risk_cost`` 加权重搜一次。

        只在"精确搜索确实因为规模触顶"、"允许走未知格"、"风险代价真的大于 1"、
        且"当前权重还没到 risk_cost"时才重搜：其余情况重搜要么结论相同（真不可达），
        要么等于没加权（w 已经是 1 或已超过 risk_cost）。
        """
        return (
            self._cap_exceeded
            and self.adaptive_weight
            and self.allow_unknown
            and self.risk_cost > 1.0
            and self.heuristic_weight < self.risk_cost
        )

    def _search_tables(self):
        """惰性构建并缓存 A* 热路径的扁平查表；网格不可变，重规划直接复用。

        全部按"四周补一圈阻挡"的索引排布，越界自然落到阻挡格：

        - ``passable``：bytearray，语义与 :meth:`_passable` 逐格一致；
        - ``cost_orth`` / ``cost_diag``：进入该格的代价，与 :meth:`_step_cost` 同序同值
          （``base [+wall] [+frontier]``，斜向只把 base 乘 √2），只是让热路径一次下标取到；
        - ``directions``：``(补齐索引偏移, 是否斜向, 正交邻偏移a, 正交邻偏移b, 代价下标增量)``，
          顺序与 ``DIRS4``/``DIRS8`` 一致，tie-break 与逐邻居版本保持一致。
        """
        cached = self._tables
        if cached is not None:
            return cached

        cells = self.grid.cells
        height, width = cells.shape
        pad_w = width + 2
        padded = np.full((height + 2, pad_w), CELL_BLOCKED, dtype=np.uint8)
        padded[1:-1, 1:-1] = cells
        walkable = padded != CELL_BLOCKED if self.allow_unknown else padded == CELL_FREE
        passable = bytearray(walkable.astype(np.uint8).tobytes())

        base = np.where(cells == CELL_UNKNOWN, self.risk_cost, 1.0)
        base = np.where(cells == CELL_BLOCKED, 0.0, base)
        wall = np.zeros(cells.shape, dtype=np.float64)
        if self._clearance is not None:
            clearance = self._clearance.astype(np.int64)
            missing = np.where(clearance >= 0, np.maximum(0, self.margin - clearance), 0)
            wall = missing.astype(np.float64) * self.wall_penalty
        frontier = np.zeros(cells.shape, dtype=np.float64)
        if self._frontier_clearance is not None:
            fringe = self._frontier_clearance.astype(np.int64)
            missing = np.where(fringe >= 0, np.maximum(0, self.frontier_margin - fringe), 0)
            frontier = (np.where(cells == CELL_FREE, missing, 0).astype(np.float64)
                        * self.frontier_penalty)

        cost_orth = _as_doubles(base + wall + frontier)
        cost_diag = _as_doubles(base * _SQRT2 + wall + frontier)

        directions = []
        for di, dj in (DIRS8 if self.diagonal else DIRS4):
            directions.append((
                di * pad_w + dj,        # 进入该邻居的补齐索引偏移
                bool(di and dj),        # 是否斜向（斜向要查切角、用 diag 代价表）
                di * pad_w,             # 斜向时正交邻格 a 的补齐偏移
                dj,                     # 斜向时正交邻格 b 的补齐偏移
                di * width + dj,        # 代价表下标增量（原图扁平索引）
            ))
        cached = (pad_w, passable, cost_orth, cost_diag, directions)
        self._tables = cached
        return cached

    def _astar(self, start, goal, weight: float = 1.0):
        """标准 A*；路径输出后再用任意角度的视线优化减少航点。

        热路径走 :meth:`_search_tables` 的扁平查表：补齐索引 + 整数偏移，替代逐邻居的
        ``DenseGrid.neighbors``/``state`` 方法调用与 numpy 标量取数。通行与禁斜穿的
        判定与 ``_passable``/``_corner_ok`` 逐条对应，只是换了实现。

        ``weight`` 是启发式放大系数（``f = g + w·h``）：只改扩展顺序，不碰通行判定，
        所以加权只影响是否最优，不会让路径穿墙或切角。
        """
        self._expanded = 0
        self._cap_exceeded = False
        pad_w, passable, cost_orth, cost_diag, directions = self._search_tables()
        height, width = self.grid.shape
        size = (height + 2) * pad_w

        goal_i, goal_j = int(goal[0]), int(goal[1])
        start_p = (int(start[0]) + 1) * pad_w + int(start[1]) + 1
        goal_p = (goal_i + 1) * pad_w + goal_j + 1

        g_score = np.full(size, math.inf, dtype=np.float64)
        g_score[start_p] = 0.0
        # 补齐索引最大也就几百万，int32 足够；用 int64 会在 500 万格图上白占一倍内存
        came_from = np.full(size, -1, dtype=np.int32)
        closed = bytearray(size)

        counter = itertools.count()
        open_heap = [(0.0, next(counter), start_p)]
        expanded = 0
        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == goal_p:
                self._expanded = expanded
                return self._rebuild_flat(came_from, current, pad_w)
            if closed[current]:
                continue
            closed[current] = 1
            expanded += 1
            if expanded > self.max_expand:
                self._expanded = expanded
                self._cap_exceeded = True
                return None

            current_cost = g_score[current]
            ci, cj = divmod(current, pad_w)
            row = (ci - 1) * width + (cj - 1)
            for offset, is_diagonal, orth_a, orth_b, cost_delta in directions:
                neighbor = current + offset
                if not passable[neighbor]:
                    continue
                if is_diagonal and (not passable[current + orth_a]
                                    or not passable[current + orth_b]):
                    continue
                step = cost_diag[row + cost_delta] if is_diagonal else cost_orth[row + cost_delta]
                tentative = current_cost + step
                if tentative < g_score[neighbor]:
                    g_score[neighbor] = tentative
                    came_from[neighbor] = current
                    ni, nj = divmod(neighbor, pad_w)
                    f = tentative + self._heuristic(
                        ni - 1, nj - 1, goal_i, goal_j, self.diagonal, weight)
                    heapq.heappush(open_heap, (f, next(counter), neighbor))
        self._expanded = expanded
        return None

    @staticmethod
    def _rebuild_flat(came_from, current: int, pad_w: int) -> list:
        """补齐索引路径 → ``(i, j)`` 数组下标路径（含起终点）。"""
        path = []
        while current >= 0:
            path.append((current // pad_w - 1, current % pad_w - 1))
            current = int(came_from[current])
        path.reverse()
        return path

    def _simplify(self, cells) -> list:
        """视线简化：把能直连的连续格合并成航点，减少无谓的拐点。

        用保守的"超覆盖"直线判断（线段经过的每一格都必须可通行），
        避免在斜穿墙角时把路径压到墙里。

        原有的"锚点到当前格"逐格扫描是 O(n²)，配上每格一次 numpy 标量取数后，
        3000 格的长直线要 9 秒。这里改成两个等价但更省的写法：线段的可通行性与
        代价由 :meth:`_fast_line` 一次走线给出（不建中间列表），候选片段代价用
        前缀和 O(1) 取（不再每次 `_path_cost` 重算整段）。
        """
        if len(cells) <= 2:
            return list(cells)
        width = self.grid.shape[1]
        _, _, cost_orth, cost_diag, _ = self._search_tables()
        prefix = [0.0] * len(cells)
        for k in range(1, len(cells)):
            pi, pj = cells[k - 1]
            ci, cj = cells[k]
            index = ci * width + cj
            prefix[k] = prefix[k - 1] + (
                cost_diag[index] if (pi != ci and pj != cj) else cost_orth[index])

        out = [cells[0]]
        anchor = 0
        for idx in range(2, len(cells)):
            clear, line_cost = self._fast_line(cells[anchor], cells[idx])
            if not clear or line_cost > prefix[idx] - prefix[anchor] + 1e-9:
                out.append(cells[idx - 1])
                anchor = idx - 1
        out.append(cells[-1])
        return self._rdp_simplify(out, cells)

    def _fast_line(self, a, b) -> tuple[bool, float]:
        """一次走线同时给出"能否直连"和"沿线步进代价"。

        等价于 ``_line_clear(a, b)`` 与 ``_path_cost(_line_cells(a, b))``，但不建中间
        列表：简化里这两个结果本来就是同一次走线得到的，分开算会把长直线变成两次 O(n²)。
        采样顺序与 :meth:`_line_cells` 逐字一致（同样的 ``k/steps`` 取整），保证结果不变。
        """
        pad_w, passable, cost_orth, cost_diag, _ = self._search_tables()
        width = self.grid.shape[1]
        ai, aj = int(a[0]), int(a[1])
        bi, bj = int(b[0]), int(b[1])
        di, dj = bi - ai, bj - aj
        steps = max(abs(di), abs(dj))
        if not passable[(ai + 1) * pad_w + aj + 1]:
            return False, 0.0
        if steps == 0:
            return True, 0.0
        prev_i, prev_j = ai, aj
        cost = 0.0
        for k in range(1, steps + 1):
            t = k / steps
            ti = round(ai + di * t)
            tj = round(aj + dj * t)
            if ti == prev_i and tj == prev_j:
                continue
            if not passable[(ti + 1) * pad_w + tj + 1]:
                return False, 0.0
            diagonal = ti != prev_i and tj != prev_j
            if diagonal and (not passable[(ti + 1) * pad_w + prev_j + 1]
                             or not passable[(prev_i + 1) * pad_w + tj + 1]):
                return False, 0.0
            index = ti * width + tj
            cost += cost_diag[index] if diagonal else cost_orth[index]
            prev_i, prev_j = ti, tj
        return True, cost

    def _path_cost(self, cells) -> float:
        cost = 0.0
        for (ai, aj), (bi, bj) in itertools.pairwise(cells):
            cost += self._step_cost(ai, aj, bi, bj, self.grid.state(bi, bj))
        return cost

    def _line_clear(self, a, b) -> bool:
        """线段经过的每一格都必须可通行；斜向相邻格还要不斜穿墙角。

        禁斜穿用 :meth:`_corner_ok`（策略层权威实现），与 A* 的规则完全一致。
        """
        line = self._line_cells(a, b)
        if not all(self._passable(i, j) for i, j in line):
            return False
        return all(
            self._corner_ok(ai, aj, bi - ai, bj - aj)
            for (ai, aj), (bi, bj) in itertools.pairwise(line)
            if ai != bi and aj != bj
        )

    @staticmethod
    def _line_cells(a, b):
        """两格之间的直线采样：``max(|di|,|dj|)+1`` 个点四舍五入到格，结果天然八连通。"""
        ai, aj = a
        bi, bj = b
        di, dj = bi - ai, bj - aj
        steps = max(abs(di), abs(dj))
        if steps == 0:
            return [(ai, aj)]
        cells = []
        for k in range(steps + 1):
            t = k / steps
            i = round(ai + di * t)
            j = round(aj + dj * t)
            if not cells or cells[-1] != (i, j):
                cells.append((i, j))
        return cells

    def _rdp_simplify(self, points, cells) -> list:
        """Further reduce near-collinear waypoints without increasing risk."""
        if len(points) <= 2 or self.waypoint_tolerance <= 0:
            return list(points)
        cell_index = {cell: index for index, cell in enumerate(cells)}
        return self._rdp_segment(points, cells, cell_index)

    def _rdp_segment(self, points, cells, cell_index) -> list:
        if len(points) <= 2:
            return list(points)
        start, end = points[0], points[-1]
        max_distance = -1.0
        split = 0
        for index, point in enumerate(points[1:-1], start=1):
            distance = self._perpendicular_distance(point, start, end)
            if distance > max_distance:
                max_distance = distance
                split = index
        if max_distance > self.waypoint_tolerance:
            left = self._rdp_segment(points[:split + 1], cells, cell_index)
            right = self._rdp_segment(points[split:], cells, cell_index)
            return left + right[1:]
        if self._replacement_ok(start, end, cells, cell_index):
            return [start, end]
        left = self._rdp_segment(points[:split + 1], cells, cell_index)
        right = self._rdp_segment(points[split:], cells, cell_index)
        return left + right[1:]

    def _replacement_ok(self, start, end, cells, cell_index) -> bool:
        if not self._line_clear(start, end):
            return False
        line = self._line_cells(start, end)
        original = cells[cell_index[start]:cell_index[end] + 1]
        if self.cells_risk(line) > self.cells_risk(original):
            return False
        return self._path_cost(line) <= self._path_cost(original) * 1.25 + 1e-9

    def _perpendicular_distance(self, point, start, end) -> float:
        px, pz = self.grid.world_of_index(*point)
        ax, az = self.grid.world_of_index(*start)
        bx, bz = self.grid.world_of_index(*end)
        dx, dz = bx - ax, bz - az
        length_sq = dx * dx + dz * dz
        if length_sq <= 1e-12:
            return math.hypot(px - ax, pz - az)
        ratio = ((px - ax) * dx + (pz - az) * dz) / length_sq
        ratio = min(1.0, max(0.0, ratio))
        return math.hypot(px - (ax + ratio * dx), pz - (az + ratio * dz))
