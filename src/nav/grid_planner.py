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
from dataclasses import dataclass, field

from src.nav.grid_io import CELL_FREE, CELL_NAMES, CELL_UNKNOWN, DenseGrid

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
                 max_expand: int = 400_000):
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
        """
        self.grid = grid
        self.risk_cost = float(risk_cost)
        self.diagonal = bool(diagonal)
        self.allow_unknown = bool(allow_unknown)
        self.margin = max(0, int(margin))
        self.wall_penalty = max(0.0, float(wall_penalty))
        self._clearance = (
            grid.clearance()
            if self.margin > 0 and self.wall_penalty > 0
            else None
        )
        self.frontier_margin = max(0, int(frontier_margin))
        self.frontier_penalty = max(0.0, float(frontier_penalty))
        self.waypoint_tolerance = max(0.0, float(waypoint_tolerance))
        self._frontier_clearance = (
            grid.frontier_clearance()
            if self.frontier_margin > 0 and self.frontier_penalty > 0
            else None
        )
        self.max_expand = int(max_expand)
        self._expanded = 0
        self._cap_exceeded = False

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

        cells = self._astar(start_cell, goal_cell)
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
    def _heuristic(ai: int, aj: int, bi: int, bj: int, diagonal: bool) -> float:
        di, dj = abs(bi - ai), abs(bj - aj)
        if not diagonal:
            return float(di + dj)
        # 八连通的可采纳启发：octile 距离（单步最小代价为 1）
        return (di + dj) + (_SQRT2 - 2.0) * min(di, dj)

    def _astar(self, start, goal):
        """标准 A*；路径输出后再用任意角度的视线优化减少航点。"""
        self._expanded = 0
        self._cap_exceeded = False
        counter = itertools.count()
        open_heap = [(0.0, next(counter), start)]
        came_from = {}
        g_score = {start: 0.0}
        closed = set()

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == goal:
                return self._rebuild(came_from, current)
            if current in closed:
                continue
            closed.add(current)
            self._expanded += 1
            if self._expanded > self.max_expand:
                self._cap_exceeded = True
                return None

            ci, cj = current
            for ni, nj in self.grid.neighbors(ci, cj, diagonal=self.diagonal):
                if not self._passable(ni, nj):
                    continue
                di, dj = ni - ci, nj - cj
                if di and dj and not self._corner_ok(ci, cj, di, dj):
                    continue
                neighbor = (ni, nj)
                tentative = g_score[current] + self._step_cost(
                    ci, cj, ni, nj, self.grid.state(ni, nj))
                if tentative < g_score.get(neighbor, math.inf):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative
                    f = tentative + self._heuristic(ni, nj, goal[0], goal[1], self.diagonal)
                    heapq.heappush(open_heap, (f, next(counter), neighbor))
        return None

    @staticmethod
    def _rebuild(came_from, current):
        path = [current]
        while current in came_from:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return path

    def _simplify(self, cells) -> list:
        """视线简化：把能直连的连续格合并成航点，减少无谓的拐点。

        用保守的"超覆盖"直线判断（线段经过的每一格都必须可通行），
        避免在斜穿墙角时把路径压到墙里。
        """
        if len(cells) <= 2:
            return list(cells)
        out = [cells[0]]
        anchor = 0
        for idx in range(2, len(cells)):
            if (
                not self._line_clear(cells[anchor], cells[idx])
                or self._path_cost(self._line_cells(cells[anchor], cells[idx]))
                > self._path_cost(cells[anchor:idx + 1]) + 1e-9
            ):
                out.append(cells[idx - 1])
                anchor = idx - 1
        out.append(cells[-1])
        return self._rdp_simplify(out, cells)

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
