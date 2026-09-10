# -*- coding: utf-8 -*-
"""导航规划：在 :class:`~src.nav.grid_io.DenseGrid` 上做 A* 寻路。

为什么未知格要"可冒险、按风险加价"
----------------------------------
网格里可行走是**已知走过**的稀疏种子（实测 ``base01_4``：可行走 8.3%、阻挡 51.9%、
未知 39.8%）。只走可行走几乎无路可走；把未知当可行走又会直接穿墙。所以：

- 可行走：代价 1（斜向 √2）
- 未知：代价 × ``risk_cost``（默认 5），即"允许冒险穿过没探过的地方，但绕路更划算"
- 阻挡：不可通行

规划结果里会给出 ``risk_cells``（路径穿过多少个未知格），调用方据此决定要不要冒险。

用法::

    from src.nav import load_grid
    from src.nav.grid_planner import GridPlanner

    grid = load_grid("assets/nav/base01_4.grid.npz")
    planner = GridPlanner(grid, margin=1)          # margin=1: 规划视图里离墙留一格
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

from src.nav.grid_io import CELL_FREE, CELL_UNKNOWN, DenseGrid

__all__ = ["GridPlanner", "PlanResult"]

#: 穿过一个未知格的代价倍数（相对可行走的 1）
DEFAULT_RISK_COST = 5.0
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
                 max_expand: int = 400_000):
        """
        Args:
            grid: 网格（不会被改动）。
            risk_cost: 穿过未知格的代价倍数（``allow_unknown=False`` 时无意义）。
            diagonal: 八连通（斜向代价 √2，且禁止斜穿墙角）还是四连通。
            allow_unknown: 是否允许走未知格。``False`` 时只在已知可行走里规划。
            margin: 规划视图里离阻挡格保留几格（见
                :meth:`DenseGrid.inflate_blocked`）；定位有 ±0.5m 量级误差、格子才 1m，
                贴墙走几乎没有余量，建议至少 1。
            max_expand: 扩展节点数上限，防止在超大图上失控。
        """
        self.source_grid = grid
        self.grid = grid.inflate_blocked(margin) if margin else grid
        self.risk_cost = float(risk_cost)
        self.diagonal = bool(diagonal)
        self.allow_unknown = bool(allow_unknown)
        self.margin = int(margin)
        self.max_expand = int(max_expand)

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #
    def plan(self, start_world, goal_world) -> PlanResult:
        """按世界坐标规划：``(x, z)`` 元组。"""
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
            return PlanResult(False, "找不到通路（可行走区域不连通，或全部需要穿过阻挡）",
                              start_cell=start_cell, goal_cell=goal_cell, notes=notes,
                              expanded=self._expanded)

        waypoints = [self.grid.world_of_index(i, j) for i, j in self._simplify(cells)]
        cost, risk = 0.0, 0
        for (ai, aj), (bi, bj) in zip(cells, cells[1:]):
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

    def _step_cost(self, ai: int, aj: int, bi: int, bj: int, state: int) -> float:
        base = _SQRT2 if (ai != bi and aj != bj) else 1.0
        if state == CELL_UNKNOWN:
            base *= self.risk_cost
        return base

    def _snap(self, cell, label: str):
        """起终点落在阻挡格（或界外）时，移到最近的可通行格；找不到就返回原因。"""
        i, j = int(cell[0]), int(cell[1])
        if self._passable(i, j):
            return (i, j), ""
        if self.allow_unknown:
            target = self._nearest_passable(i, j, max_radius=16)
        else:
            target = self.grid.nearest_free(i, j, max_radius=16)
        if target is None:
            return None, f"{label} {cell} 不可通行，附近找不到可站的格（是否传错地图/坐标？）"
        return target, f"{label}已从 {cell} 挪到最近的可通行格 {target}"

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
        """标准 A*；返回逐格路径或 None。"""
        self._expanded = 0
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
                return None

            ci, cj = current
            for ni, nj in self.grid.neighbors(ci, cj, diagonal=self.diagonal):
                if not self._passable(ni, nj):
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
            if not self._line_clear(cells[anchor], cells[idx]):
                out.append(cells[idx - 1])
                anchor = idx - 1
        out.append(cells[-1])
        return out

    def _line_clear(self, a, b) -> bool:
        """线段经过的每一格都必须可通行；斜向相邻格还要不斜穿墙角（与 A* 图的规则一致）。"""
        line = self._line_cells(a, b)
        for i, j in line:
            if not self._passable(i, j):
                return False
        for (ai, aj), (bi, bj) in zip(line, line[1:]):
            if ai != bi and aj != bj and (not self._passable(ai, bj)
                                          or not self._passable(bi, aj)):
                return False
        return True

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
            i = int(round(ai + di * t))
            j = int(round(aj + dj * t))
            if not cells or cells[-1] != (i, j):
                cells.append((i, j))
        return cells
