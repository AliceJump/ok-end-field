"""导航网格的表示与读写。

存储只有一个格式：``<map>_<zoom>.grid.npz``
-------------------------------------------
``cells``
    ``uint8`` 稠密数组，``0=未知 / 1=可行走 / 2=阻挡``——单值枚举，不存在
    "两个集合 + 谁覆盖谁"的歧义。
``meta``
    UTF-8 JSON 字符串：``origin`` / ``cell_size`` / ``map_name`` / ``zoom`` /
    ``axis_convention`` / ``magic`` / ``schema_version`` / 溯源字段。

**格式规范（编辑器按此生成）**

``numpy.savez_compressed`` 写出的 npz，恰好两个成员：

- ``cells``：``uint8`` 数组，形状 ``(H, W)``；第 0 行 = 最小的 z、第 0 列 = 最小的 x
  （都正相关），取值只允许 0/1/2。

- ``meta``：**numpy 字符串数组**（``np.array(json.dumps(meta_dict))``，0 维），
  不能是 object 数组——否则读的时候必须开 ``allow_pickle``。内容：

  ``{"magic": "okef-nav-grid", "schema_version": 2, "map_name": "base01",
  "zoom": "4", "origin": [x, y, z], "cell_size": 1.0,
  "axis_convention": "<见 AXIS_CONVENTION>", "source": "editor",
  "created": "2026-09-10T20:00:00"}``

  其中 ``origin`` 是 ``cells[0,0]`` **最小角**的世界坐标（不再是"格 (0,0) 的坐标"）；
  ``cell_size`` 必须 > 0。``magic`` 与 ``schema_version`` 读时校验，不匹配直接报错，
  不静默当作网格读进来。

- 二维：不带高度，``origin[1]`` 只作记录、不参与换算。

实测同一份网格（``base01_4``，33,364 个有效格）：

===============  ==========  ========
形态             大小        说明
===============  ==========  ========
编辑器的 JSON    1205 KB     平均 37 B/格
本格式 ``.npz``  2.2 KB      **547×**
运行期集合表示   约 4.3 MB   每次邻居判定一次哈希查表
运行期稠密数组   54 KB       一次下标
===============  ==========  ========

为什么要稠密数组而不是"可行走集合 + 阻挡集合"
--------------------------------------------
- **内存差 20~30 倍**：实测 100 万格，稠密 ``uint8`` 是 1.0 MB，等量的
  ``set[tuple]`` 光元组本身就 21~30 MB（还没算 set 容器开销）。
- **贴墙安全距离只有数组能实用地算**：``clearance`` 用 numpy 波前逐层扩散，
  100 万格 24 ms；集合表示得逐格 Python 展开。这一项很关键——定位有 ±0.5m
  量级误差、格子又只有 1m，贴墙走的判定几乎没余量。
- **逐格邻居生成两者基本持平**（实测同一份 100 万格网格，20 万次查询
  集合 0.60 s / 稠密 0.68 s）。换表示**不会**让 A* 变快——A* 的开销主要在
  Python 侧的堆操作与循环，所以真要做大规模寻路还得靠算法侧优化
  （邻居偏移量内联、必要时 numpy 批量扩展），别指望这里。

坐标约定（单一坐标空间，写进 meta 并参与校验）
----------------------------------------------
``world = origin + (i, j) * cell_size``，``i`` = 行 = 世界 z 方向，
``j`` = 列 = 世界 x 方向，两者都正相关；``origin`` 是**数组下标 (0,0) 那格的
最小角**的世界坐标。二维网格忽略高度 y。

只做二维：编辑器给三维体素（``cells: [[ix,iy,iz]]``）时明确报错，不静默降维。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path

import numpy as np

__all__ = [
    "AXIS_CONVENTION",
    "CELL_BLOCKED",
    "CELL_FREE",
    "CELL_NAMES",
    "CELL_UNKNOWN",
    "GRID_MAGIC",
    "GRID_SUFFIX",
    "SCHEMA_VERSION",
    "DenseGrid",
    "GridMeta",
    "load_grid",
    "new_grid",
    "save_grid",
]

#: 单值枚举：一格只有一个状态
CELL_UNKNOWN = 0
CELL_FREE = 1
CELL_BLOCKED = 2
CELL_NAMES = {CELL_UNKNOWN: "未知", CELL_FREE: "可行走", CELL_BLOCKED: "阻挡"}
_VALID_STATES = frozenset(CELL_NAMES)

#: 文件标识与版本；读写都校验，避免把别的文件/旧结构当成网格静默读进来
GRID_MAGIC = "okef-nav-grid"
SCHEMA_VERSION = 2
GRID_SUFFIX = ".grid.npz"

AXIS_CONVENTION = (
    "world = origin + (i,j)*cell_size; i=row=z, j=col=x, both positive; "
    "origin is the min corner of cells[0,0]; 2D ignores y"
)

#: 邻居方向（模块级常量，避免每次调用重建 / 逐方向走方法调用）
DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIRS8 = (*DIRS4, (1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class GridMeta:
    """网格元数据。``origin`` / ``cell_size`` 决定 world↔下标 换算，其余是溯源信息。"""

    map_name: str = ""
    zoom: str = ""
    #: 数组下标 (0,0) 那格最小角的世界坐标
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cell_size: float = 1.0
    axis_convention: str = AXIS_CONVENTION
    source: str = ""
    created: str = ""
    #: 由读写层维护，调用方不用管
    magic: str = GRID_MAGIC
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        """校验换算参数。``cell_size`` 必须有限且为正，否则 ``index_of_world`` 会除零或映射错乱。

        ``from_json`` 是在构造后才逐字段赋值，所以它必须显式再调一次本方法。
        """
        if not math.isfinite(float(self.cell_size)) or float(self.cell_size) <= 0:
            raise ValueError(f"cell_size 必须为有限正数，当前 {self.cell_size!r}")

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> GridMeta:
        """严格解析：标识/版本不对直接报错，缺字段才用默认值补齐。"""
        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"网格元数据不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("网格元数据必须是 JSON 对象")
        if str(data.get("magic", "")) != GRID_MAGIC:
            raise ValueError(
                f"不是本项目的网格文件（magic={data.get('magic')!r}，应为 {GRID_MAGIC!r}）")
        version = int(data.get("schema_version") or 0)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"网格结构版本不匹配（文件 {version}，当前 {SCHEMA_VERSION}）："
                "请用配套版本的编辑器重新导出")
        meta = cls()
        try:
            meta.map_name = str(data.get("map_name") or "")
            meta.zoom = str(data.get("zoom") or "")
            origin = data.get("origin") or (0.0, 0.0, 0.0)
            meta.origin = (float(origin[0]), float(origin[1]), float(origin[2]))
            raw_cell = data.get("cell_size")
            # 注意别用 `or` 兜底：cell_size=0 是非法值，要被下面的校验挡下，不能被换成 1.0
            meta.cell_size = 1.0 if raw_cell is None else float(raw_cell)
            meta.axis_convention = str(data.get("axis_convention") or AXIS_CONVENTION)
            meta.source = str(data.get("source") or "")
            meta.created = str(data.get("created") or "")
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"网格元数据字段格式不对: {exc}") from exc
        meta.validate()
        return meta


class DenseGrid:
    """稠密单值枚举网格：``cells[i, j]``（i=行=世界 z，j=列=世界 x）。

    越界一律按 ``CELL_BLOCKED`` 处理（保守：界外不可走）；需要区分时用 :meth:`in_bounds`。
    """

    def __init__(self, cells, meta: GridMeta | None = None):
        raw = np.asarray(cells)
        if raw.ndim != 2:
            raise ValueError(f"只支持二维网格，收到 ndim={raw.ndim}")
        if raw.size == 0:
            raise ValueError("网格是空的")
        # 按**原始值**校验再转 uint8：先 astype 会把越界值静默回绕（256→0、258→2），
        # 正好放过这里要拦的输入。浮点 1.0 与整数 1 在这里相等，仍算合法。
        bad = set(np.unique(raw).tolist()) - _VALID_STATES
        if bad:
            raise ValueError(
                f"网格里有非法状态 {sorted(bad)}；只允许 {sorted(_VALID_STATES)}"
                f"（{CELL_NAMES}）")
        arr = raw if raw.dtype == np.uint8 else raw.astype(np.uint8)
        self.cells = arr
        self.meta = meta if isinstance(meta, GridMeta) else GridMeta()
        self._padded_cache: np.ndarray | None = None

    # ------------------------------------------------------------------ #
    # 形状 / 统计
    # ------------------------------------------------------------------ #
    @property
    def shape(self) -> tuple[int, int]:
        return int(self.cells.shape[0]), int(self.cells.shape[1])

    def counts(self) -> dict:
        return {CELL_NAMES[s]: int((self.cells == s).sum()) for s in
                (CELL_UNKNOWN, CELL_FREE, CELL_BLOCKED)}

    def extent(self) -> tuple[float, float, float, float]:
        """世界坐标范围 ``(x_min, z_min, x_max, z_max)``。"""
        h, w = self.shape
        x0, z0 = self.meta.origin[0], self.meta.origin[2]
        cs = self.meta.cell_size
        return (x0, z0, x0 + w * cs, z0 + h * cs)

    def __repr__(self) -> str:
        return (f"DenseGrid(shape={self.shape}, cell_size={self.meta.cell_size}, "
                f"map={self.meta.map_name!r}, counts={self.counts()})")

    # ------------------------------------------------------------------ #
    # 坐标换算
    # ------------------------------------------------------------------ #
    def in_bounds(self, i: int, j: int) -> bool:
        h, w = self.shape
        return 0 <= int(i) < h and 0 <= int(j) < w

    def index_of_world(self, x: float, z: float) -> tuple[int, int]:
        """世界 (x, z) → 数组下标 ``(i, j)``；可能越界，用 :meth:`in_bounds` 判。"""
        # math.floor(NaN/inf) 会抛 ValueError/OverflowError；坐标来自定位或用户输入，
        # 在边界处给出明确报错，而不是让它在规划深处炸栈。
        if not (math.isfinite(float(x)) and math.isfinite(float(z))):
            raise ValueError(f"世界坐标必须有限，收到 ({x!r}, {z!r})")
        cs = self.meta.cell_size
        j = math.floor((float(x) - self.meta.origin[0]) / cs)
        i = math.floor((float(z) - self.meta.origin[2]) / cs)
        return (int(i), int(j))

    def world_of_index(self, i: int, j: int) -> tuple[float, float]:
        """数组下标 → 该格**中心**的世界 (x, z)。"""
        cs = self.meta.cell_size
        return (self.meta.origin[0] + (int(j) + 0.5) * cs,
                self.meta.origin[2] + (int(i) + 0.5) * cs)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def state(self, i: int, j: int) -> int:
        """格状态；越界返回 ``CELL_BLOCKED``。"""
        if not self.in_bounds(i, j):
            return CELL_BLOCKED
        return int(self.cells[int(i), int(j)])

    def is_free(self, i: int, j: int) -> bool:
        return self.state(i, j) == CELL_FREE

    def is_blocked(self, i: int, j: int) -> bool:
        return self.state(i, j) == CELL_BLOCKED

    # ------------------------------------------------------------------ #
    # 邻域
    # ------------------------------------------------------------------ #
    def neighbors(self, i: int, j: int, *, diagonal: bool = True) -> list[tuple[int, int]]:
        """相邻的**非阻挡**格（可行走与未知都返回）。

        「是否允许走未知格」以及 ``allow_unknown=False`` 时「未知格视同阻挡」的禁斜穿
        判定，都由调用方 :class:`~src.nav.grid_planner.GridPlanner` 按策略决定；
        这里只按**阻挡**粗筛。

        ``diagonal=True`` 时八连通，且禁止斜穿**阻挡**格的角（两条正交邻格都必须非阻挡）。
        调用方保证 ``(i, j)`` 在界内（A* 只在已入图的格上扩展）。

        这是寻路的热路径，所以走缓存好的"四周补一圈阻挡"数组直接下标，
        不做逐方向的方法调用与边界判断。
        """
        p = self._padded()
        i, j = int(i) + 1, int(j) + 1
        blk = CELL_BLOCKED
        out = []
        for di, dj in (DIRS8 if diagonal else DIRS4):
            if p[i + di, j + dj] == blk:
                continue
            if di and dj and self._padded_corner_cut(p, i, j, di, dj):
                continue
            out.append((i + di - 1, j + dj - 1))
        return out

    @staticmethod
    def _padded_corner_cut(p: np.ndarray, i: int, j: int, di: int, dj: int) -> bool:
        """斜向一步是否斜穿**阻挡**格的角；``p`` 是补过一圈阻挡的数组，``i``/``j`` 已 +1。

        这是邻域生成用的**廉价粗筛**，只看阻挡。策略层的最终判定在
        :meth:`GridPlanner._corner_ok`——``allow_unknown=False``（未知格视同阻挡）时
        未知格的角也不许切，那一层说了算。
        """
        blk = CELL_BLOCKED
        return p[i + di, j] == blk or p[i, j + dj] == blk

    def _padded(self) -> np.ndarray:
        """四周补一圈 ``CELL_BLOCKED`` 的副本，使越界查询自然落到阻挡格。

        惰性构建并缓存；``cells`` 视为只读（要改就换一张新网格）。
        """
        cached = self._padded_cache
        if cached is None or cached.shape != (self.shape[0] + 2, self.shape[1] + 2):
            cached = np.full((self.shape[0] + 2, self.shape[1] + 2), CELL_BLOCKED, dtype=np.uint8)
            cached[1:-1, 1:-1] = self.cells
            self._padded_cache = cached
        return cached

    # ------------------------------------------------------------------ #
    # 贴墙安全距离
    # ------------------------------------------------------------------ #
    def clearance(self) -> np.ndarray:
        """每格到最近 ``CELL_BLOCKED`` 的 BFS 距离（单位：格）；阻挡格自身为 0。

        ``int32`` 数组；``-1`` 表示该格与任何阻挡格都不连通（例如全图没有阻挡格）。
        用 numpy 波前逐层扩散（每层几次数组移位），比逐格 Python BFS 快两个量级；
        代价是层数 = 最大距离，所以**每张图算一次即可**，不要每帧调用。
        """
        blocked = self.cells == CELL_BLOCKED
        dist = np.where(blocked, 0, -1).astype(np.int32)
        if not blocked.any():
            return dist
        frontier = blocked
        step = 0
        while True:
            step += 1
            grown = np.zeros_like(blocked)
            grown[1:, :] |= frontier[:-1, :]
            grown[:-1, :] |= frontier[1:, :]
            grown[:, 1:] |= frontier[:, :-1]
            grown[:, :-1] |= frontier[:, 1:]
            grown &= dist < 0
            if not grown.any():
                return dist
            dist[grown] = step
            frontier = grown

    def frontier_clearance(self) -> np.ndarray:
        """每格到最近 ``CELL_UNKNOWN`` 的 BFS 距离（单位：格）。

        ``-1`` 表示该格无法在不穿过阻挡格的情况下到达未知边缘；
        没有未知格时全图返回 ``-1``。规划器用它偏好已知自由区域内部。
        """
        unknown = self.cells == CELL_UNKNOWN
        dist = np.where(unknown, 0, -1).astype(np.int32)
        if not unknown.any():
            return dist
        passable = self.cells != CELL_BLOCKED
        frontier = unknown.copy()
        step = 0
        while True:
            step += 1
            grown = np.zeros_like(frontier)
            grown[1:, :] |= frontier[:-1, :]
            grown[:-1, :] |= frontier[1:, :]
            grown[:, 1:] |= frontier[:, :-1]
            grown[:, :-1] |= frontier[:, 1:]
            grown &= passable & (dist < 0)
            if not grown.any():
                return dist
            dist[grown] = step
            frontier = grown

    def nearest_free(self, i: int, j: int, max_radius: int = 8) -> tuple[int, int] | None:
        """最近的可行走格（落点修正用）；``max_radius`` 内找不到返回 None。"""
        if self.is_free(i, j):
            return (int(i), int(j))
        for radius in range(1, max(0, int(max_radius)) + 1):
            best = None
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    if max(abs(di), abs(dj)) != radius:
                        continue
                    ni, nj = int(i) + di, int(j) + dj
                    if not self.is_free(ni, nj):
                        continue
                    d2 = di * di + dj * dj
                    if best is None or d2 < best[0]:
                        best = (d2, ni, nj)
            if best is not None:
                return (best[1], best[2])
        return None


# ---------------------------------------------------------------------- #
# 构造 / 读写
# ---------------------------------------------------------------------- #
def new_grid(shape, *, origin=(0.0, 0.0, 0.0), cell_size: float = 1.0,
             map_name: str = "", zoom: str = "", fill: int = CELL_UNKNOWN) -> DenseGrid:
    """建一张全 ``fill`` 的空网格。"""
    if int(fill) not in _VALID_STATES:
        raise ValueError(f"fill 必须是 {sorted(_VALID_STATES)}")
    height, width = int(shape[0]), int(shape[1])
    meta = GridMeta(map_name=map_name, zoom=zoom,
                    origin=(float(origin[0]), float(origin[1]), float(origin[2])),
                    cell_size=float(cell_size), source="new")
    return DenseGrid(np.full((height, width), int(fill), dtype=np.uint8), meta)


def load_grid(path) -> DenseGrid:
    """读 ``*.grid.npz``。文件标识/版本/数组形状都会校验，不对就报错。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"网格文件不存在: {p}")
    if not p.name.endswith(GRID_SUFFIX):
        raise ValueError(f"网格文件名必须是 *{GRID_SUFFIX}，收到 {p.name}")
    with np.load(p, allow_pickle=False) as data:
        if "cells" not in data.files or "meta" not in data.files:
            raise ValueError(f"{p.name}: 缺少 cells/meta，不是网格文件")
        arr = np.array(data["cells"])
        meta = GridMeta.from_json(str(data["meta"].item()))
    if not meta.source:
        meta.source = f"npz:{p.name}"
    return DenseGrid(arr, meta)


def save_grid(path, grid: DenseGrid) -> Path:
    """写 ``*.grid.npz``；元数据内嵌在同一个文件里。不改动传入的 grid。"""
    if not isinstance(grid, DenseGrid):
        raise TypeError("save_grid 需要 DenseGrid")
    p = Path(path)
    if not p.name.endswith(GRID_SUFFIX):
        stem = p.name[:-4] if p.name.lower().endswith(".npz") else p.name
        p = p.with_name(stem + GRID_SUFFIX)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = replace(grid.meta, magic=GRID_MAGIC, schema_version=SCHEMA_VERSION,
                   created=grid.meta.created or datetime.now().isoformat(timespec="seconds"))
    if not meta.source:
        meta = replace(meta, source=f"npz:{p.name}")
    # meta 用 numpy 字符串数组存，读的时候不必开 allow_pickle
    np.savez_compressed(p, cells=grid.cells, meta=np.array(meta.to_json()))
    return p
