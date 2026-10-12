"""导航网格的数据模型与 ``*.grid.npz`` 读写。

一份网格只使用稠密单值枚举，避免“可行走集合 + 阻挡集合”在覆盖顺序上产生歧义：

- ``cells``：``uint8`` 二维数组，形状 ``(H, W)``。
- ``meta``：UTF-8 JSON 字符串，保存换算参数、文件标识、schema 版本和溯源信息。

文件格式由 :func:`save_grid` 生成，并由 :func:`load_grid` 严格校验。``cells`` 的第 0 行
对应最小 world z、第 0 列对应最小 world x；取值只允许 :data:`CELL_UNKNOWN`、
:data:`CELL_FREE` 和 :data:`CELL_BLOCKED`。

坐标换算统一为::

    world = origin + (i, j) * cell_size

其中 ``i`` 是行、沿世界 z，``j`` 是列、沿世界 x，``origin`` 是 ``cells[0, 0]``
最小角的世界坐标。当前只支持二维网格，``origin[1]`` 仅作记录。

选择稠密数组的原因、完整格式说明和性能数据见
``docs/dev/网格导航.md``。
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
    "region_contains",
    "save_grid",
    "validate_floor_regions",
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

#: 一条高度区间会涉及的字段；缺一不可
_FLOOR_REGION_FIELDS = ("x0", "x1", "z0", "z1", "y_min", "y_max")


def validate_floor_regions(raw) -> tuple[dict, ...]:
    """校验并归一化 ``floor_regions``，返回元组；``None`` 归一成空元组。

    规则与 nav-grid-editor 的 ``_normalize_floor_regions`` 一致——两边对同一份数据必须
    得出同样的结论，否则我们这边"能走的层"和编辑器"标好的层"会对不上：

    - 六个范围字段缺一不可、必须是有限数，且 ``x0<=x1``、``z0<=z1``、``y_min<=y_max``；
    - ``component_id`` 可选（非空才保留）；
    - ``cells`` 可选，每项是 ``[x0,x1,z0,z1]``，同样要求最小不大于最大。
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("floor_regions 必须是数组")
    out = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"floor_regions[{index}] 必须是对象")
        try:
            values = [float(item[name]) for name in _FLOOR_REGION_FIELDS]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"floor_regions[{index}] 需要 {'/'.join(_FLOOR_REGION_FIELDS)}") from exc
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"floor_regions[{index}] 含有非有限数")
        x0, x1, z0, z1, y_min, y_max = values
        if x0 > x1 or z0 > z1 or y_min > y_max:
            raise ValueError(f"floor_regions[{index}] 的最小值不能大于最大值")
        region = dict(zip(_FLOOR_REGION_FIELDS, values, strict=True))
        component_id = str(item.get("component_id") or "").strip()
        if component_id:
            region["component_id"] = component_id
        raw_cells = item.get("cells")
        if raw_cells is not None:
            if not isinstance(raw_cells, (list, tuple)):
                raise ValueError(f"floor_regions[{index}].cells 必须是数组")
            cells = []
            for cell in raw_cells:
                if not isinstance(cell, (list, tuple)) or len(cell) != 4:
                    raise ValueError(f"floor_regions[{index}].cells 每项应为 [x0,x1,z0,z1]")
                try:
                    cell_values = [float(value) for value in cell]
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"floor_regions[{index}].cells 含有非法数字") from exc
                if not all(math.isfinite(value) for value in cell_values):
                    raise ValueError(f"floor_regions[{index}].cells 含有非有限数")
                if cell_values[0] > cell_values[1] or cell_values[2] > cell_values[3]:
                    raise ValueError(f"floor_regions[{index}].cells 的最小值不能大于最大值")
                cells.append(cell_values)
            if cells:
                region["cells"] = cells
        out.append(region)
    return tuple(out)


def region_contains(region: dict, x: float, y: float, z: float) -> bool:
    """判断三维点是否落在一条高度区间内（闭区间）。

    ``cells`` 非空时**先要求 X/Z 命中其中某一格**再判外框——只判外框会把两格之间的
    空隙也算进去，那正是这些逐格矩形存在的意义。语义与 nav-grid-editor 的
    ``_region_contains`` 逐条对应。
    """
    cells = region.get("cells")
    if cells and not any(cell[0] <= x <= cell[1] and cell[2] <= z <= cell[3] for cell in cells):
        return False
    return (
        region["x0"] <= x <= region["x1"]
        and region["z0"] <= z <= region["z1"]
        and region["y_min"] <= y <= region["y_max"]
    )


@dataclass
class GridMeta:
    """网格元数据。``origin`` / ``cell_size`` 决定 world↔下标 换算，其余是溯源信息。"""

    map_name: str = ""
    zoom: str = ""
    #: 数组下标 (0,0) 那格最小角的世界坐标
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cell_size: float = 1.0
    #: 分层 id；空串表示主图（大地图）。同一 ``map_name`` 下主图与各分层是各自独立的网格，
    #: 相邻关系只在层内成立，跨层只能走 ``links.json`` 的显式边（见 ``docs/dev/分层导航.md``）。
    tier: str = ""
    #: 该层的高度区间（消歧用）：``{x0,x1,z0,z1,y_min,y_max}``，可带 ``component_id`` 与
    #: ``cells``。**只用于**把三维坐标解析到层，不参与逐格可走判定——它是稀疏标注，
    #: 拿它过滤会让整张图变不可走。
    floor_regions: tuple[dict, ...] = ()
    axis_convention: str = AXIS_CONVENTION
    source: str = ""
    created: str = ""
    #: ``floor_regions`` 非法时的原因；为空表示没出错。非法区间被丢弃而不是让加载失败。
    floor_regions_error: str = ""
    #: 本类不认识的 meta 键，原样保留以便回写（见 :meth:`to_dict`）
    extra: dict | None = None
    #: 由读写层维护，调用方不用管
    magic: str = GRID_MAGIC
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        # 归一化后再校验：调用方传 list / 带空 cells 的区间时，存下来的也是规范形状，
        # 不必让下游各自再处理一遍。
        #
        # 非法区间**不让整个网格加载失败**：``floor_regions`` 是可选元数据，一段写坏的
        # 区间只应让该层按"没有区间"处理（层判定随之退化），不该把整张网格也一起废掉。
        # 契约见 nav-grid-editor 的 ``tests/data/floor_rule_vectors.json`` 用例
        # 「某层区间非法时按『没有区间』处理」。原因记在 :attr:`floor_regions_error`。
        try:
            self.floor_regions = validate_floor_regions(self.floor_regions)
            if not self.floor_regions_error:
                self.floor_regions_error = ""
        except ValueError as exc:
            self.floor_regions = ()
            self.floor_regions_error = str(exc)
        self.validate()

    def validate(self) -> None:
        """校验换算参数。``cell_size`` 必须有限且为正，否则 ``index_of_world`` 会除零或映射错乱。

        ``from_json`` 是在构造后才逐字段赋值，所以它必须显式再调一次本方法。
        """
        if not math.isfinite(float(self.cell_size)) or float(self.cell_size) <= 0:
            raise ValueError(f"cell_size 必须为有限正数，当前 {self.cell_size!r}")

    def to_dict(self) -> dict:
        """返回可直接 JSON 序列化的元数据副本。

        先铺**未知键**再覆盖已知字段：这份 meta 是编辑器与消费方共用的，编辑器将来
        新增的键不能因为我们只认识自己的字段就在回写时被悄悄丢掉——``floor_regions``
        一旦丢掉，层判定会静默失效（变成"所有层都没标过高度"），而且不报错。
        """
        data = dict(self.extra or {})
        data.update(
            {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("extra", "floor_regions_error")}
        )
        return data

    def to_json(self) -> str:
        """序列化为 UTF-8 JSON；中文溯源字段不转义。"""
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
            raise ValueError(f"不是本项目的网格文件（magic={data.get('magic')!r}，应为 {GRID_MAGIC!r}）")
        version = int(data.get("schema_version") or 0)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"网格结构版本不匹配（文件 {version}，当前 {SCHEMA_VERSION}）：请用配套版本的编辑器重新导出"
            )
        meta = cls()
        known = {f.name for f in fields(cls)} - {"extra"}
        meta.extra = {str(key): value for key, value in data.items() if key not in known}
        try:
            meta.map_name = str(data.get("map_name") or "")
            meta.zoom = str(data.get("zoom") or "")
            origin = data.get("origin") or (0.0, 0.0, 0.0)
            meta.origin = (float(origin[0]), float(origin[1]), float(origin[2]))
            raw_cell = data.get("cell_size")
            # 注意别用 `or` 兜底：cell_size=0 是非法值，要被下面的校验挡下，不能被换成 1.0
            meta.cell_size = 1.0 if raw_cell is None else float(raw_cell)
            # tier / floor_regions 是分层地图新增的可选字段：旧文件没有，读进来就是
            # 主图 + 无高度区间，行为与分层功能上线前完全一致。
            meta.tier = str(data.get("tier") or "")
            meta.floor_regions = validate_floor_regions(data.get("floor_regions"))
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
            raise ValueError(f"网格里有非法状态 {sorted(bad)}；只允许 {sorted(_VALID_STATES)}（{CELL_NAMES}）")
        arr = raw if raw.dtype == np.uint8 else raw.astype(np.uint8)
        self.cells = arr
        self.meta = meta if isinstance(meta, GridMeta) else GridMeta()
        self._padded_cache: np.ndarray | None = None

    # ------------------------------------------------------------------ #
    # 形状 / 统计
    # ------------------------------------------------------------------ #
    @property
    def shape(self) -> tuple[int, int]:
        """返回 ``(行数, 列数)``，即 ``(world_z 方向长度, world_x 方向长度)``。"""
        return int(self.cells.shape[0]), int(self.cells.shape[1])

    def counts(self) -> dict:
        """返回三态计数，键是中文状态名，可直接用于诊断日志。"""
        return {CELL_NAMES[s]: int((self.cells == s).sum()) for s in (CELL_UNKNOWN, CELL_FREE, CELL_BLOCKED)}

    def extent(self) -> tuple[float, float, float, float]:
        """世界坐标范围 ``(x_min, z_min, x_max, z_max)``。"""
        h, w = self.shape
        x0, z0 = self.meta.origin[0], self.meta.origin[2]
        cs = self.meta.cell_size
        return (x0, z0, x0 + w * cs, z0 + h * cs)

    def __repr__(self) -> str:
        return (
            f"DenseGrid(shape={self.shape}, cell_size={self.meta.cell_size}, "
            f"map={self.meta.map_name!r}, counts={self.counts()})"
        )

    # ------------------------------------------------------------------ #
    # 坐标换算
    # ------------------------------------------------------------------ #
    def in_bounds(self, i: int, j: int) -> bool:
        """判断数组下标 ``(i, j)`` 是否位于网格内部。"""
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
        return (self.meta.origin[0] + (int(j) + 0.5) * cs, self.meta.origin[2] + (int(i) + 0.5) * cs)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def state(self, i: int, j: int) -> int:
        """格状态；越界返回 ``CELL_BLOCKED``。"""
        if not self.in_bounds(i, j):
            return CELL_BLOCKED
        return int(self.cells[int(i), int(j)])

    def is_free(self, i: int, j: int) -> bool:
        """判断是否为已知可行走格；越界按阻挡处理。"""
        return self.state(i, j) == CELL_FREE

    def is_blocked(self, i: int, j: int) -> bool:
        """判断是否为阻挡格；越界也视为阻挡。"""
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
        for di, dj in DIRS8 if diagonal else DIRS4:
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
    @staticmethod
    def _wavefront(seed: np.ndarray, max_dist: int | None, allowed: np.ndarray | None = None) -> np.ndarray:
        """从 ``seed`` 逐层四连通扩散，返回每格到种子的层数；种子自身为 0。

        ``allowed`` 非空时只扩散到这些格子，其余保持 ``-1``。

        ``max_dist`` 给了就只扩散这么多层，未到达的格子保持 ``-1``——调用方按
        "距离 ≥ max_dist" 解释即可。层数 = 最大距离，所以**不设上限时层数就是主要开销**：
        521 万格的大图要扩散上千层、每层几次全数组运算，是秒级的。
        """
        dist = np.where(seed, 0, -1).astype(np.int32)
        if max_dist is not None and int(max_dist) <= 0:
            return dist
        if not seed.any():
            return dist
        frontier = seed
        step = 0
        while True:
            step += 1
            grown = np.zeros_like(seed)
            grown[1:, :] |= frontier[:-1, :]
            grown[:-1, :] |= frontier[1:, :]
            grown[:, 1:] |= frontier[:, :-1]
            grown[:, :-1] |= frontier[:, 1:]
            grown &= dist < 0
            if allowed is not None:
                grown &= allowed
            if not grown.any():
                return dist
            dist[grown] = step
            if max_dist is not None and step >= int(max_dist):
                return dist
            frontier = grown

    def clearance(self, max_dist: int | None = None) -> np.ndarray:
        """每格到最近 ``CELL_BLOCKED`` 的 BFS 距离（单位：格）；阻挡格自身为 0。

        ``int32`` 数组；``-1`` 表示该格与任何阻挡格都不连通（例如全图没有阻挡格），
        或（给了 ``max_dist`` 时）距离已经超过 ``max_dist``。

        ``max_dist`` 是给大图准备的：调用方
        （:class:`~src.nav.grid_planner.GridPlanner`）只用得到 ``min(距离, margin)``，
        而"未到达"恰好等价于"距离 ≥ max_dist"，按缺 0 格处理即可，所以截断到
        ``margin`` 层不损失任何精度，500 万格的图能从秒级降到毫秒级。
        """
        return self._wavefront(self.cells == CELL_BLOCKED, max_dist)

    def frontier_clearance(self, max_dist: int | None = None) -> np.ndarray:
        """每格到最近 ``CELL_UNKNOWN`` 的 BFS 距离（单位：格）。

        ``-1`` 表示该格无法在不穿过阻挡格的情况下到达未知边缘；
        没有未知格时全图返回 ``-1``。规划器用它偏好已知自由区域内部。

        ``max_dist`` 的含义与 :meth:`clearance` 相同（截断到该层，其余留 ``-1``）。
        """
        return self._wavefront(self.cells == CELL_UNKNOWN, max_dist, allowed=self.cells != CELL_BLOCKED)

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
def new_grid(
    shape,
    *,
    origin=(0.0, 0.0, 0.0),
    cell_size: float = 1.0,
    map_name: str = "",
    zoom: str = "",
    tier: str = "",
    floor_regions=(),
    fill: int = CELL_UNKNOWN,
) -> DenseGrid:
    """建一张全 ``fill`` 的空网格。``tier`` 留空即主图。"""
    if int(fill) not in _VALID_STATES:
        raise ValueError(f"fill 必须是 {sorted(_VALID_STATES)}")
    height, width = int(shape[0]), int(shape[1])
    meta = GridMeta(
        map_name=map_name,
        zoom=zoom,
        origin=(float(origin[0]), float(origin[1]), float(origin[2])),
        cell_size=float(cell_size),
        tier=str(tier or ""),
        floor_regions=floor_regions,
        source="new",
    )
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
    meta = replace(
        grid.meta,
        magic=GRID_MAGIC,
        schema_version=SCHEMA_VERSION,
        created=grid.meta.created or datetime.now().isoformat(timespec="seconds"),
    )
    if not meta.source:
        meta = replace(meta, source=f"npz:{p.name}")
    # meta 用 numpy 字符串数组存，读的时候不必开 allow_pickle
    np.savez_compressed(p, cells=grid.cells, meta=np.array(meta.to_json()))
    return p
