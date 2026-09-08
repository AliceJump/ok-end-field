# -*- coding: utf-8 -*-
"""3D 体素网格地图（1×1×1m）。

可达性模型（替换原图模型）：
- 默认所有格子不可达；
- 已知位置（走过轨迹/物品点位/传送点/滑索端点）标记为 free 种子，
  只有当前坐标处的单元格为 free（不做周围膨胀）；
- blocked（标记墙/卡住）覆盖 free，永不可达；
- 垂直方向只进 free（爬台阶走已走过的格），未知格仅水平方向可冒险（寻路时
  给风险代价）。

内存友好：只存稀疏的 free 种子集合 + blocked，寻路时邻居即时生成。

二维网格（导航网格编辑器的 <map>_<zoom>.grid.json 产物）：
- cells/blocked 以 [x, z] 二元组存储（无高度），加载时自动归一化为
  (ix, 0, iz)（Y 维度忽略），并置 is_2d=True；
- 二维模式下 cell_of 忽略传入的 y、nearest_free 忽略高度容差、
  to_dict 回写 [x, z] 格式保持往返一致。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# 6 连通方向
_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0))


@dataclass
class GridMap:
    origin: tuple = (0.0, 0.0, 0.0)
    cell_size: float = 1.0
    cells: set = field(default_factory=set)       # free 种子格 (ix,iy,iz)
    blocked: set = field(default_factory=set)     # 标记墙/卡住（覆盖 free）
    teleports: list = field(default_factory=list)  # 传送点格 [(ix,iy,iz), ...]
    zips: list = field(default_factory=list)       # 滑索 [(a_cell, b_cell), ...]
    is_2d: bool = False                            # 二维网格：忽略 Y（编辑器产物）

    # 运行时缓存（懒加载）
    _free: set | None = None
    _free_list: list | None = None
    _tree: object | None = None
    _bounds: tuple | None = None
    _zip_edges: dict | None = None
    _clearance: dict | None = None  # 每个格到最近 blocked 格的距离（多源 BFS）

    # ---------- 坐标换算 ----------

    def cell_of(self, x: float, y: float, z: float) -> tuple:
        if self.is_2d:
            # 二维网格：不区分高度，全部落在基准层 iy=0
            return (int(math.floor((x - self.origin[0]) / self.cell_size)),
                    0,
                    int(math.floor((z - self.origin[2]) / self.cell_size)))
        return (int(math.floor((x - self.origin[0]) / self.cell_size)),
                int(math.floor((y - self.origin[1]) / self.cell_size)),
                int(math.floor((z - self.origin[2]) / self.cell_size)))

    def center_of(self, cell: tuple) -> tuple:
        return (self.origin[0] + (cell[0] + 0.5) * self.cell_size,
                self.origin[1] + (cell[1] + 0.5) * self.cell_size,
                self.origin[2] + (cell[2] + 0.5) * self.cell_size)

    # ---------- 状态查询 ----------

    def is_blocked(self, cell: tuple) -> bool:
        return cell in self.blocked

    def is_free(self, cell: tuple) -> bool:
        self._ensure_free()
        return cell in self._free

    def in_bounds(self, cell: tuple) -> bool:
        b = self.bounds()
        return (b[0] <= cell[0] <= b[1] and b[2] <= cell[1] <= b[3]
                and b[4] <= cell[2] <= b[5])

    def bounds(self) -> tuple:
        if self._bounds is None:
            pts = list(self.cells) + list(self.teleports)
            for a, b in self.zips:
                pts.append(a)
                pts.append(b)
            if not pts:
                self._bounds = (0, 0, 0, 0, 0, 0)
            else:
                self._bounds = (
                    min(p[0] for p in pts), max(p[0] for p in pts),
                    min(p[1] for p in pts), max(p[1] for p in pts),
                    min(p[2] for p in pts), max(p[2] for p in pts),
                )
        return self._bounds

    def zip_edges(self) -> dict:
        """滑索邻接：cell -> [(cell, cost), ...]（双向）。"""
        if self._zip_edges is None:
            self._zip_edges = {}
            for a, b in self.zips:
                ca = self.center_of(a)
                cb = self.center_of(b)
                cost = math.dist(ca, cb)
                self._zip_edges.setdefault(a, []).append((b, cost))
                self._zip_edges.setdefault(b, []).append((a, cost))
        return self._zip_edges

    def clearance(self, max_dist: int = 64) -> dict:
        """多源 BFS：每个格到最近 blocked 格的距离（格数）。

        返回值：cell -> 距离（blocked 格=0，紧邻的 free 格=1，依此类推）。
        用于给"离墙近"的格加代价，让寻路偏向走廊中线（不贴墙）。缓存惰性计算，
        网格变化（_invalidate）后自动重建。
        max_dist: 距离上限，开阔区远离墙的格统一记为该值（避免无界增长）。
        """
        if self._clearance is not None:
            return self._clearance
        import collections

        dirs = _DIRS if not self.is_2d else [d for d in _DIRS if d[1] == 0]
        dist: dict = {}
        q = collections.deque()
        for b in self.blocked:
            dist[b] = 0
            q.append(b)

        while q:
            cell = q.popleft()
            d = dist[cell]
            if d >= max_dist:
                continue
            for dx, dy, dz in dirs:
                n = (cell[0] + dx, cell[1] + dy, cell[2] + dz)
                if n in dist:
                    continue
                if self.is_blocked(n):
                    continue  # blocked 已在 dist=0，不重复
                dist[n] = d + 1
                q.append(n)

        self._clearance = dist
        return dist

    # ---------- 吸附 ----------

    def nearest_free(self, x: float, y: float, z: float, max_dist: float,
                     y_tolerance: float = 1.0) -> tuple:
        """返回最近的 free 格 (cell, dist2d)；超出 max_dist 或高度差超过 1m 返回 (None, dist)。

        二维网格（is_2d）忽略高度容差：只按 xz 平面距离吸附。
        """
        self._ensure_free()
        if not self._free_list:
            return None, float("inf")
        k = min(8, len(self._free_list))
        dists, idxs = self._tree.query([x, z], k=k)
        dists = np.atleast_1d(dists)
        idxs = np.atleast_1d(idxs)
        best = None
        best_dist = float("inf")
        for dist, idx in zip(dists, idxs):
            cell = self._free_list[int(idx)]
            if not self.is_2d and abs(self.center_of(cell)[1] - y) > y_tolerance:
                continue
            if float(dist) <= max_dist:
                return cell, float(dist)
            if float(dist) < best_dist:
                best_dist = float(dist)
                best = cell
        return best, best_dist

    # ---------- 内部 ----------

    def _ensure_free(self):
        if self._free is None:
            self._free = self._compute_free()
            self._free_list = list(self._free)
            from scipy.spatial import cKDTree

            if self._free_list:
                xs = np.array([self.center_of(c)[0] for c in self._free_list])
                zs = np.array([self.center_of(c)[2] for c in self._free_list])
                self._tree = cKDTree(np.column_stack((xs, zs)))
            else:
                self._tree = None

    def _compute_free(self) -> set:
        # 只有当前坐标处的格 free，不做周围膨胀
        return {cell for cell in self.cells if cell not in self.blocked}

    def _invalidate(self):
        self._free = None
        self._free_list = None
        self._tree = None
        self._clearance = None
        self._bounds = None
        self._zip_edges = None

    # ---------- 序列化 ----------

    @staticmethod
    def _write_2d(cell: tuple) -> list:
        """二维网格回写 [x, z]（Y 为基准层 0，不落盘）。"""
        return [cell[0], cell[2]]

    def to_dict(self) -> dict:
        if self.is_2d:
            return {
                "origin": list(self.origin),
                "cell_size": self.cell_size,
                "cells": [self._write_2d(c) for c in sorted(self.cells)],
                "blocked": [self._write_2d(c) for c in sorted(self.blocked)],
                "teleports": [self._write_2d(c) for c in self.teleports],
                "zips": [[self._write_2d(a), self._write_2d(b)]
                         for a, b in self.zips],
            }
        return {
            "origin": list(self.origin),
            "cell_size": self.cell_size,
            "cells": [list(c) for c in sorted(self.cells)],
            "blocked": [list(c) for c in sorted(self.blocked)],
            "teleports": [list(c) for c in self.teleports],
            "zips": [[list(a), list(b)] for a, b in self.zips],
        }

    @staticmethod
    def _normalize_cell(item, is_2d: bool) -> tuple:
        """把格子条目归一化为 (ix, iy, iz)。

        二维网格（[x, z] 二元组，导航网格编辑器产物）补基准层 iy=0。
        """
        if is_2d:
            return (int(item[0]), 0, int(item[1]))
        return tuple(int(v) for v in item)

    @staticmethod
    def from_dict(data: dict) -> "GridMap":
        cells_raw = list(data.get("cells", []))
        # 二维判定：cells 条目为 [x, z] 二元组（无高度）
        is_2d = bool(cells_raw) and len(cells_raw[0]) < 3
        return GridMap(
            origin=tuple(data.get("origin", (0.0, 0.0, 0.0))),
            cell_size=float(data.get("cell_size", 1.0)),
            cells={GridMap._normalize_cell(c, is_2d) for c in cells_raw},
            blocked={GridMap._normalize_cell(c, is_2d)
                     for c in data.get("blocked", [])},
            teleports=[GridMap._normalize_cell(c, is_2d)
                       for c in data.get("teleports", [])],
            zips=[(GridMap._normalize_cell(a, is_2d),
                   GridMap._normalize_cell(b, is_2d))
                  for a, b in data.get("zips", [])],
            is_2d=is_2d,
        )

    def save(self, path: Path | str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    @staticmethod
    def load(path: Path | str) -> "GridMap":
        with open(path, encoding="utf-8") as fp:
            return GridMap.from_dict(json.load(fp))


def build_grid(
    trails: list[dict],
    waypoints: dict[str, list[dict]] | None = None,
    teleports: list[dict] | None = None,
    zips: list[dict] | None = None,
    blocked: list[dict] | None = None,
    teleport_item_names: set[str] | None = None,
    cell_size: float = 1.0,
    margin: int = 10,
) -> GridMap:
    """从采集数据建网格。

    - cells 种子 = 轨迹点 + 物品点位 + 传送点 + 滑索端点；
    - teleport_item_names 里的物品点位按传送点入 teleports；
    - blocked 记录（受阻段/标记墙点）映射成 blocked 格。
    """
    waypoints = waypoints or {}
    teleports = teleports or []
    zips = zips or []
    blocked = blocked or []
    teleport_item_names = teleport_item_names or set()

    seeds: list[tuple] = []       # (x, y, z)
    for p in trails:
        seeds.append((float(p["x"]), float(p["y"]), float(p["z"])))
    tp_items = []
    for item_name, pts in waypoints.items():
        for p in pts:
            pos = (float(p["x"]), float(p["y"]), float(p["z"]))
            if item_name in teleport_item_names:
                tp_items.append(pos)
            else:
                seeds.append(pos)

    teleport_cells_pos = [tuple(float(v) for v in p.values()) for p in teleports] + tp_items
    zip_pos_pairs = []
    for rec in zips:
        try:
            s = (float(rec["start"]["x"]), float(rec["start"]["y"]), float(rec["start"]["z"]))
            e = (float(rec["end"]["x"]), float(rec["end"]["y"]), float(rec["end"]["z"]))
            seeds.append(s)
            seeds.append(e)
            zip_pos_pairs.append((s, e))
        except (KeyError, TypeError, ValueError):
            continue

    blocked_pos = []
    for rec in blocked:
        try:
            a = (float(rec["a"]["x"]), float(rec["a"]["y"]), float(rec["a"]["z"]))
            b = (float(rec["b"]["x"]), float(rec["b"]["y"]), float(rec["b"]["z"]))
            blocked_pos.append(a)
            blocked_pos.append(b)
        except (KeyError, TypeError, ValueError):
            continue

    all_pos = list(seeds) + list(teleport_cells_pos)
    if not all_pos:
        return GridMap(origin=(0, 0, 0), cell_size=cell_size)

    origin = (
        min(p[0] for p in all_pos) - margin,
        min(p[1] for p in all_pos) - margin,
        min(p[2] for p in all_pos) - margin,
    )
    grid = GridMap(origin=origin, cell_size=cell_size)
    grid.cells = {grid.cell_of(*p) for p in seeds}
    grid.teleports = [grid.cell_of(*p) for p in teleport_cells_pos]
    grid.zips = [(grid.cell_of(*a), grid.cell_of(*b)) for a, b in zip_pos_pairs]
    grid.blocked = {grid.cell_of(*p) for p in blocked_pos}
    grid._invalidate()
    return grid
