# -*- coding: utf-8 -*-
"""3D 体素网格 A* 寻路（6 连通）。

- free 格代价 1（每米）；未知格（包围盒内、非 free 非 blocked）代价 risk_cost
  表示「冒险尝试」；
- 垂直方向只进 free 格（爬台阶走已走过的格，不冒险爬空气）；
- 传送零成本（多源 A*：起点 + 各传送点）；滑索为双向特殊边；
- 起点/终点先吸附最近 free 格，超半径可冒险就近格。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from src.nav.grid import GridMap, _DIRS


@dataclass
class GridPlan:
    ok: bool = False
    waypoints: list = field(default_factory=list)   # [(x, y, z)] 格中心
    segments: list = field(default_factory=list)    # 每段类型 ["walk"|"zip"|"teleport"]
    cost: float = 0.0
    risky: bool = False
    risky_start: bool = False
    via_teleport: bool = False
    reason: str = ""

    def __bool__(self):
        return self.ok


class GridFinder:
    def __init__(self, grid: GridMap, risk_cost: float = 5.0,
                 y_tolerance: float = 1.0, risk_margin: int = 30,
                 simplify: bool = True, pull_step: float = 0.5,
                 max_pull_span: int = 64,
                 wall_margin: int = 0, wall_penalty: float = 0.0):
        self.grid = grid
        self.risk_cost = float(risk_cost)
        self.y_tolerance = float(y_tolerance)
        self.risk_margin = int(risk_margin)
        self.simplify = bool(simplify)
        self.pull_step = float(pull_step)
        self.max_pull_span = int(max_pull_span)
        # 离墙代价：free 格离墙 < wall_margin 格时按缺额加代价，使寻路偏向走廊中线。
        # wall_margin<=0 或 wall_penalty<=0 时关闭（默认关闭，保持与旧行为一致）。
        self.wall_margin = int(wall_margin)
        self.wall_penalty = float(wall_penalty)

    def _risk_bounds(self) -> tuple:
        b = self.grid.bounds()
        m = self.risk_margin
        return (b[0] - m, b[1] + m, b[2] - m, b[3] + m, b[4] - m, b[5] + m)

    def _in_risk_bounds(self, cell: tuple) -> bool:
        b = self._risk_bounds()
        return (b[0] <= cell[0] <= b[1] and b[2] <= cell[1] <= b[3]
                and b[4] <= cell[2] <= b[5])

    def find_path(self, start, goal, snap_radius: float | None = None,
                  allow_risk: bool = True, allow_teleport: bool = True,
                  teleport_cost: float = 0.0,
                  snap_exclude: set | frozenset | None = None) -> GridPlan:
        snap = snap_radius if snap_radius is not None else 4.0
        start_cell, start_dist = self._snap(start, snap, allow_risk, snap_exclude)
        if start_cell is None:
            return GridPlan(reason=f"起点附近没有可走格（最近 {start_dist:.1f}m）")

        # 终点：free 直接用；blocked 失败；未知且在风险范围内 → 冒险直达该格（尝试走到）
        gx, gy, gz = (float(v) for v in goal)
        goal_cell = self.grid.cell_of(gx, gy, gz)
        risky_goal = False
        if self.grid.is_free(goal_cell):
            goal_dist = 0.0
        elif self.grid.is_blocked(goal_cell):
            return GridPlan(reason="终点是墙（blocked），不可达")
        elif self._in_risk_bounds(goal_cell):
            goal_dist = snap + 1.0
            risky_goal = True
        else:
            return GridPlan(reason="终点超出地图范围")

        risky_start = start_dist > snap
        risky = risky_start or risky_goal

        if start_cell == goal_cell:
            return GridPlan(True, [self.grid.center_of(start_cell)], [], 0.0,
                            risky, risky_start, False, "")

        sources = [(start_cell, 0.0)]
        via_teleport = False
        if allow_teleport and self.grid.teleports:
            for t in self.grid.teleports:
                if t == start_cell:
                    continue
                sources.append((t, teleport_cost))

        path, cost = self._astar(sources, goal_cell)
        if path is None:
            return GridPlan(reason="A* 未找到路径（不可达）")
        if allow_teleport and path and path[0] != start_cell:
            via_teleport = True
        # 取拐点：仅在已走过区（free）作贪心拉直，zip/teleport 段与冒险段保持原样
        if self.simplify:
            cells, segments = self._simplify(path, via_teleport)
        else:
            cells, segments = path, self._segments(path, via_teleport)
        waypoints = [self.grid.center_of(c) for c in cells]
        return GridPlan(True, waypoints, segments, cost, risky, risky_start,
                        via_teleport, "")

    # ---------- 路径拉直（greedy string pull，参考 MaaEnd） ----------

    def _is_clear_walk(self, a: tuple, b: tuple, min_clr: int = 0) -> bool:
        """从格 a 到格 b 的直线是否适合拉直。

        要求采样点全程落在 free 格上；若 min_clr>0，还要求**free 格**的离墙距
        不小于 min_clr（不贴墙），避免把"高质量中线路径"又拉直成贴墙直线。
        未知(冒险)格不检查墙距（本来就是试探）。

        Args:
            min_clr: 允许拉直的最小离墙距（格）；0=不检查墙距（旧行为）。
        """
        ax, ay, az = self.grid.center_of(a)
        bx, by, bz = self.grid.center_of(b)
        d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
        if d < 1e-9:
            return True
        clr = self.grid.clearance() if min_clr > 0 else None
        n = max(1, int(math.ceil(d / self.pull_step)))
        for k in range(1, n):
            t = k / n
            cell = self.grid.cell_of(ax + (bx - ax) * t,
                                     ay + (by - ay) * t,
                                     az + (bz - az) * t)
            if not self.grid.is_free(cell):
                return False
            if clr is not None and clr.get(cell, min_clr) < min_clr:
                return False
        return True

    def _simplify(self, path, via_teleport) -> tuple:
        """贪心拉直：按 zip/teleport 段切分，每段连续 walk 格内尽量拉成拐点。

        返回 (cells, segments)。
        """
        if len(path) <= 2:
            return path, self._segments(path, via_teleport)

        # 逐段划分：zip 跳转（格间不是 6 邻接）与 teleport 首跳都作为硬断点
        zips = self.grid.zip_edges()
        raw_segs = self._segments(path, via_teleport)

        new_cells = []
        new_segs = []
        # 切出每段连续 walk 格（段内相邻格必须 6 邻接且非 zip）
        i = 0
        run = [path[0]]
        while i < len(raw_segs):
            a, b = path[i], path[i + 1]
            is_zip = any(n == b for n, _ in zips.get(a, ()))
            if is_zip or raw_segs[i] in ("zip", "teleport"):
                # 结束当前 walk 段（拉直），再补这条特殊边
                cells, segs = self._pull_run(run)
                new_cells.extend(cells)
                new_segs.extend(segs)
                # 特殊边：原样保留
                new_cells.append(b)
                new_segs.append(raw_segs[i])
                run = [b]
            else:
                run.append(b)
            i += 1
        # 收尾
        cells, segs = self._pull_run(run)
        new_cells.extend(cells)
        new_segs.extend(segs)
        # 去重：相邻重复格（段边界/拉直退化）删掉，segments 同步
        dedup_cells = []
        dedup_segs = []
        for idx, c in enumerate(new_cells):
            if dedup_cells and dedup_cells[-1] == c:
                # 丢弃该重复点及其「入边」，但保留此前已有的出边？
                # 相邻重复点 shared 同一边，直接跳过该点即可
                continue
            dedup_cells.append(c)
            if idx > 0:
                dedup_segs.append(new_segs[idx - 1])
        return dedup_cells, dedup_segs

    def _pull_once(self, cells, min_clr: int = 0) -> list:
        """单次贪心 string pull：从左到右，尽量把可达（直线无遮挡）的格并入一条线。

        Returns:
            list: 拉直后的格序列（起点 + 各拐点 + 终点）。
        """
        if len(cells) <= 2:
            return list(cells)
        corners = [cells[0]]
        cursor = 0
        n = len(cells)
        while cursor < n - 1:
            limit = min(n - 1, cursor + self.max_pull_span)
            best = cursor + 1
            reach = cursor + 1
            while reach <= limit:
                # 从当前拐点直线可达 reach 才继续往前推（可达终点 n-1）
                if not self._is_clear_walk(cells[cursor], cells[reach], min_clr):
                    break
                best = reach
                reach += 1
            corners.append(cells[best])
            cursor = best
        return corners

    def _run_min_clearance(self, run) -> int:
        """返回整段 run 里能保证的最大离墙距（用于确定允许拉直的最小墙距）。

        整段都是单格宽走廊（所有格离墙=1）时返回 1（放宽，允许拉直）；
        否则返回 wall_margin（严格，不允许拉直成贴墙线段）。
        """
        if self.wall_margin <= 0 or self.wall_penalty <= 0:
            return 0
        clr = self.grid.clearance()
        run_max = max((clr.get(c, self.wall_margin) for c in run), default=0)
        return self.wall_margin if run_max >= self.wall_margin else 1

    def _pull_run(self, run) -> tuple:
        """对一段连续 6 邻接 walk 格做**迭代** string pull，直到稳定。

        反复拉直直到拐点数不再减少：能直接连成直线的中间拐点（含因
        max_pull_span 或采样边界残留的共线点）会被合并掉，大幅减少冗余拐点。
        只合并「直线全程 free、无遮挡」的拐点，绝不切墙。

        若开启了离墙代价：只有「沿途 free 格离墙足够远」的直线才允许拉直，
        避免把离墙优先的高质量路径又拉直成贴墙直线；单格宽走廊自动放宽。

        Returns:
            (cells, segments)；segments 全为 "walk"，长度 = len(cells)-1。
        """
        if len(run) <= 2:
            return list(run), ["walk"] * (len(run) - 1)
        min_clr = self._run_min_clearance(run)
        current = list(run)
        for _ in range(8):  # 迭代到稳定（最多 8 次）
            nxt = self._pull_once(current, min_clr)
            if len(nxt) >= len(current):
                current = nxt
                break
            current = nxt
        return current, ["walk"] * (len(current) - 1)

    def _line_clear(self, x: float, y: float, z: float, target_cell: tuple) -> bool:
        """从世界点 (x,y,z) 到目标格中心的直线是否不穿过任何 blocked 格。

        端点不算（起点可能在墙上/未知区），只查中间采样点（pull_step 间距）；
        未知格允许（冒险语义），只有标定的墙会挡视线。
        """
        g = self.grid
        tx, ty, tz = g.center_of(target_cell)
        d = math.sqrt((x - tx) ** 2 + (y - ty) ** 2 + (z - tz) ** 2)
        if d < 1e-9:
            return True
        n = max(1, int(math.ceil(d / self.pull_step)))
        for kk in range(1, n):
            t = kk / n
            cell = g.cell_of(x + (tx - x) * t, y + (ty - y) * t, z + (tz - z) * t)
            if g.is_blocked(cell):
                return False
        return True

    def _snap(self, pos, snap_radius, allow_risk,
              snap_exclude: set | frozenset | None = None) -> tuple:
        """吸附最近 free 格，并做直线视线校验。

        - 当前位置格本身就是 free：直接返回；
        - 否则按距离找 free 候选（snap_exclude 里的格跳过）；
        - 优先返回「直线不穿墙」的最近候选；都穿墙时回落最近候选（保持旧行为）。

        返回 (cell, dist2d)。
        """
        x, y, z = (float(v) for v in pos)
        cell = self.grid.cell_of(x, y, z)
        if self.grid.is_free(cell):
            return cell, 0.0
        cell, dist = self._snap_nearest(x, y, z, snap_radius, snap_exclude)
        if cell is None and allow_risk:
            cell, dist = self._snap_nearest(x, y, z, float("inf"), snap_exclude)
        return cell, dist

    def _snap_nearest(self, x: float, y: float, z: float, max_dist: float,
                      snap_exclude: set | frozenset | None = None) -> tuple:
        """在 free 格里找 (距离, 视线) 都满足的最佳吸附格。

        max_dist=inf 表示不限距离（冒险吸附）。返回 (cell, dist2d)。
        """
        g = self.grid
        snap_exclude = snap_exclude or frozenset()
        g._ensure_free()
        if not g._free_list:
            return None, float("inf")
        k = min(64, len(g._free_list))
        dists, idxs = g._tree.query([x, z], k=k)
        dists = np.atleast_1d(dists)
        idxs = np.atleast_1d(idxs)
        fallback = None
        fallback_dist = float("inf")
        for dist, idx in zip(dists, idxs):
            cell = g._free_list[int(idx)]
            if cell in snap_exclude:
                continue
            if not g.is_2d and abs(g.center_of(cell)[1] - y) > self.y_tolerance:
                continue
            dist = float(dist)
            # dists 升序：超过 max_dist 后更远的候选都不满足距离要求
            if max_dist < math.inf and dist > max_dist:
                break
            if dist < fallback_dist:
                fallback, fallback_dist = cell, dist
            if self._line_clear(x, y, z, cell):
                return cell, dist
        return fallback, fallback_dist

    def _segments(self, path, via_teleport) -> list:
        zips = self.grid.zip_edges()
        segs = []
        prev = path[0]
        for i in range(1, len(path)):
            cur = path[i]
            if any(n == cur for n, _ in zips.get(prev, ())):
                segs.append("zip")
            else:
                segs.append("walk")
            prev = cur
        return segs

    def _h(self, cell, goal_cell) -> float:
        a = self.grid.center_of(cell)
        b = self.grid.center_of(goal_cell)
        return math.sqrt(sum((p - q) ** 2 for p, q in zip(a, b)))

    def _neighbors(self, cell):
        """生成 (nxt_cell, cost, kind)。"""
        g = self.grid
        free_nxt = []
        ix, iy, iz = cell
        # 二维网格只走水平 4 邻接：跳过垂直方向，防止从基准层上下绕墙/垂直冒险
        dirs = _DIRS if not g.is_2d else (d for d in _DIRS if d[1] == 0)
        for dx, dy, dz in dirs:
            nxt = (ix + dx, iy + dy, iz + dz)
            if g.is_blocked(nxt):
                continue
            if g.is_free(nxt):
                cost = 1.0
                if self.wall_margin > 0 and self.wall_penalty > 0:
                    # 离墙越近代价越高：clr < wall_margin 的格按缺额加代价，
                    # 使 A* 偏向走廊中线（远离墙）。单格宽走廊各格同值，只能走。
                    clr = g.clearance().get(nxt, self.wall_margin)
                    if clr < self.wall_margin:
                        cost += self.wall_penalty * (self.wall_margin - clr)
                yield nxt, cost, "walk"
                continue
            # 非 free：仅水平方向允许冒险进未知格（在风险范围内）；
            # 垂直方向只允许落差 1m 的已知 free 格
            if dy == 0 and self._in_risk_bounds(nxt):
                yield nxt, self.risk_cost, "walk"
        for nxt, cost in g.zip_edges().get(cell, ()):
            yield nxt, cost, "zip"

    def _astar(self, sources, goal_cell):
        g_score = {}
        came_from = {}
        open_heap = []
        for cell, cost in sources:
            g_score[cell] = cost
            heapq.heappush(open_heap, (cost + self._h(cell, goal_cell), cost, cell))
        closed = set()
        while open_heap:
            f, g, cell = heapq.heappop(open_heap)
            if cell in closed:
                continue
            if cell == goal_cell:
                return self._reconstruct(came_from, sources, cell), g
            closed.add(cell)
            for nxt, step_cost, _kind in self._neighbors(cell):
                if nxt in closed:
                    continue
                cand = g + step_cost
                if nxt not in g_score or cand < g_score[nxt]:
                    g_score[nxt] = cand
                    came_from[nxt] = cell
                    heapq.heappush(open_heap, (cand + self._h(nxt, goal_cell), cand, nxt))
        return None, float("inf")

    @staticmethod
    def _reconstruct(came_from, sources, goal):
        path = [goal]
        node = goal
        source_set = {s[0] for s in sources}
        while node not in source_set:
            node = came_from[node]
            path.append(node)
        path.reverse()
        return path
