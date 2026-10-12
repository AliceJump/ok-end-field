"""分层地图：层发现、跨层连接与「三维坐标 → 层」判定。

数据由 nav-grid-editor 产出，格式契约两边**同级校验**（见 ``docs/dev/分层导航.md``）。
本模块只做读取与归一化，不参与路径搜索。

三条不可动摇的约定（编辑器负责保证产出满足它，这里负责不违反）：

1. **相邻关系只在同一层内成立**——每层的稠密网格就是该层的可走图；
2. **跨层一律走 ``links.json`` 的显式边**——绝不把两层按 X/Z 叠起来当邻接。同一个
   X/Z 上地表和地下都可能是可走的，叠加等于凭空连边（实测分层网格的可走格里有
   2%~5% 与主图同 (x,z) 也可走）；
3. **主图不挖洞**——地表那格是真实可走的，玩家在哪一层由 ``floor_regions`` 的 Y 区间
   判定，而不是靠把主图挖成阻挡。

``floor_regions`` 只用于第 3 条（消歧），**绝不参与逐格可走判定**：它是稀疏标注
（实测整张 map02 只有 1 段 39 格），拿它过滤会让整张图变不可走。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.nav.grid_io import (
    CELL_FREE,
    GRID_SUFFIX,
    DenseGrid,
    load_grid,
    region_contains,
)

__all__ = [
    "LINK_KINDS",
    "MAIN_TIER",
    "MAIN_TIER_ALIASES",
    "LayerKey",
    "LayeredMap",
    "TierLink",
    "TierLinkEndpoint",
    "discover_layer_paths",
    "discover_layers",
    "endpoint_cells",
    "load_catalog",
    "load_layered_map",
    "load_links",
    "normalize_tier",
    "resolve_tier_by_height",
    "resolve_tier_for_point",
    "valid_map_name",
]

#: 主图在 API 侧写作 ``__main__``，在文件与端点里写作空串。两种拼写都归一成空串，
#: 否则消费方会在这里静默错配——这是分层数据里最容易踩的一个坑。
MAIN_TIER = "__main__"
MAIN_TIER_ALIASES = frozenset({"", MAIN_TIER})

#: 地图名白名单，与编辑器同规则：既挡路径分隔符/相对路径，也挡 Windows 保留设备名。
_MAP_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")
_WIN_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"COM{index}" for index in range(1, 10)] + [f"LPT{index}" for index in range(1, 10)]
)


def valid_map_name(map_name: object) -> bool:
    """地图名是否合法（与编辑器 ``valid_map_name`` 同规则）。"""
    text = str(map_name or "")
    if not _MAP_NAME_RE.fullmatch(text):
        return False
    return text.upper() not in _WIN_RESERVED


#: ``links.json`` 允许的连接类型
LINK_KINDS = frozenset({"stairs", "elevator", "portal", "door", "rope", "other"})

LayerKey = tuple[str, str]


def normalize_tier(tier: object) -> str:
    """把层的两种拼写归一成内部键：主图一律空串。"""
    text = str(tier or "").strip()
    return "" if text in MAIN_TIER_ALIASES else text


def layer_key(map_name: str, tier: object) -> LayerKey:
    """层的唯一键。

    只按 ``map`` 取键是不够的：独立地图（``scene_indie_*``）的 ``map_name`` 也是
    ``"map02"``，它和主图是两个互不相干的场景，必须靠 tier 区分。
    """
    return (str(map_name or "").strip(), normalize_tier(tier))


@dataclass(frozen=True)
class TierLinkEndpoint:
    """连接的一端：某个层上的一个世界坐标区域。"""

    map_name: str
    tier: str
    zoom: str
    #: ``{x0, z0, x1, z1}``；编辑器保证对齐到格且 ``x1>x0``、``z1>z0``
    rect: dict

    @property
    def key(self) -> LayerKey:
        return layer_key(self.map_name, self.tier)

    @property
    def center(self) -> tuple[float, float]:
        """区域中心的世界 (x, z)，仅用于日志与最近邻匹配。"""
        return (
            (float(self.rect["x0"]) + float(self.rect["x1"])) / 2.0,
            (float(self.rect["z0"]) + float(self.rect["z1"])) / 2.0,
        )


@dataclass(frozen=True)
class TierLink:
    """一条跨层连接。

    ``bidirectional`` 为假时只能从 ``from`` 走到 ``to``（实测数据里有单向的 ``rope``）。
    """

    link_id: str
    source: TierLinkEndpoint
    target: TierLinkEndpoint
    bidirectional: bool
    kind: str
    cost: float
    label: str = ""

    @property
    def reversed(self) -> TierLink:
        """反向走这条连接（``from``/``to`` 互换）；单向边反向仍为单向。"""
        return TierLink(
            link_id=self.link_id,
            source=self.target,
            target=self.source,
            bidirectional=self.bidirectional,
            kind=self.kind,
            cost=self.cost,
            label=self.label,
        )


def _rect(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("连接区域必须是对象")
    try:
        x0, z0 = float(raw["x0"]), float(raw["z0"])
        x1, z1 = float(raw["x1"]), float(raw["z1"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("连接区域 x0/z0/x1/z1 必须是数字") from exc
    if not all(math.isfinite(value) for value in (x0, z0, x1, z1)):
        raise ValueError("连接区域必须是有限数")
    if x1 <= x0 or z1 <= z0:
        raise ValueError("连接区域必须满足 x1>x0 且 z1>z0")
    return {"x0": x0, "z0": z0, "x1": x1, "z1": z1}


def _endpoint(raw) -> TierLinkEndpoint:
    if not isinstance(raw, dict):
        raise ValueError("连接端点必须是对象")
    map_name = str(raw.get("map") or "").strip()
    if not map_name:
        raise ValueError("连接端点缺少 map")
    return TierLinkEndpoint(
        map_name=map_name,
        tier=normalize_tier(raw.get("tier")),
        #: 空串表示「按查询上下文的 zoom 解析」（见 :func:`discover_layer_paths`）
        zoom=str(raw.get("zoom") or "").strip(),
        rect=_rect(raw.get("rect")),
    )


def load_links(path) -> list[TierLink]:
    """读 ``links.json``；文件不存在时返回空列表（等价于"这张图没有跨层连接"）。"""
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError(f"links.json 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("links"), list):
        raise ValueError("links.json 顶层必须是 {schema_version, links}")
    links: list[TierLink] = []
    for index, raw in enumerate(data["links"]):
        if not isinstance(raw, dict):
            raise ValueError(f"links[{index}] 必须是对象")
        source = _endpoint(raw.get("from"))
        target = _endpoint(raw.get("to"))
        if source == target:
            raise ValueError(f"links[{index}] 的起点和终点不能完全相同")
        kind = str(raw.get("kind") or "stairs")
        if kind not in LINK_KINDS:
            raise ValueError(f"links[{index}] 的连接类型必须是 {sorted(LINK_KINDS)}")
        try:
            cost = float(raw.get("cost", 1.0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"links[{index}] 的连接代价必须是正数") from exc
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError(f"links[{index}] 的连接代价必须是正数")
        links.append(
            TierLink(
                link_id=str(raw.get("id") or f"link{index}"),
                source=source,
                target=target,
                bidirectional=bool(raw.get("bidirectional", True)),
                kind=kind,
                cost=cost,
                label=str(raw.get("label") or ""),
            )
        )
    return links


def discover_layer_paths(layer_dir, map_name: str, tier: object, zoom: str = "") -> list[Path]:
    """列出某层的候选网格文件，按文件名倒序（最新在前）。

    与编辑器的 ``_tier_grid_candidates`` 同口径：主图匹配 ``{map}_*.grid.npz`` 且
    **排除文件名含 ``__`` 的**，分层匹配 ``{map}__{tier}_*.grid.npz``。

    ``zoom`` 为空表示「按上下文解析」：该层只有一个候选时才成立，多个候选必须由调用方
    指定 zoom——否则 zoom=3 的连接会在 zoom=4 的查询里被当成有效。调用方用
    :func:`discover_layers` 时不必自己处理这个分支。
    """
    root = Path(layer_dir)
    if not root.is_dir():
        return []
    name = str(map_name or "").strip()
    wanted = normalize_tier(tier)
    if wanted:
        candidates = sorted(root.glob(f"{name}__{wanted}_*{GRID_SUFFIX}"), reverse=True)
    else:
        candidates = sorted(
            (path for path in root.glob(f"{name}_*{GRID_SUFFIX}") if "__" not in path.name),
            reverse=True,
        )
    wanted_zoom = str(zoom or "").strip()
    if not wanted_zoom:
        return candidates
    return [path for path in candidates if _filename_zoom(path) == wanted_zoom]


def _filename_zoom(path: Path) -> str:
    """从 ``<地图>__<tier>_<zoom>.grid.npz`` / ``<地图>_<zoom>.grid.npz`` 里取 zoom 段。

    只用于**预筛**候选：真正的 zoom 以文件内的 ``meta.zoom`` 为准（文件名可能是别的
    工具写的）。取不到就返回空串，交由 meta 校验兜底。
    """
    stem = path.name[: -len(GRID_SUFFIX)] if path.name.endswith(GRID_SUFFIX) else path.name
    head, _, tail = stem.rpartition("_")
    return tail if head else ""


def discover_layers(
    layer_dir,
    map_name: str,
    *,
    zoom: str = "",
    tiers: list[str] | None = None,
) -> dict[LayerKey, DenseGrid]:
    """发现并加载某张地图的全部层。

    Args:
        layer_dir: 网格目录（含主图与 ``__<tier>`` 分层文件）。
        map_name: 地图名，例如 ``map02``。
        zoom: 指定档次；留空表示「不限定，但每层必须唯一」。
        tiers: 只加载这些层（``None`` 表示目录里能发现的全都要）。主图始终尝试加载。

    Returns:
        ``{(map_name, tier): DenseGrid}``；tier 已归一化（主图是空串）。

    Raises:
        ValueError: 某一层有多个候选而没指定 ``zoom``，或多个候选里无法判定用哪个。
    """
    root = Path(layer_dir)
    name = str(map_name or "").strip()
    wanted_tiers = [normalize_tier(tier) for tier in tiers] if tiers is not None else None
    wanted_zoom = str(zoom or "").strip()

    if wanted_tiers is None:
        wanted_tiers = ["", *_discover_tier_ids(root, name)]
    elif "" not in wanted_tiers:
        wanted_tiers = ["", *wanted_tiers]

    layers: dict[LayerKey, DenseGrid] = {}
    for tier in dict.fromkeys(wanted_tiers):
        paths = discover_layer_paths(root, name, tier, wanted_zoom)
        if not paths:
            continue
        grid = _load_matching(root, paths, name, tier, wanted_zoom)
        if grid is not None:
            layers[layer_key(name, tier)] = grid
    return layers


def _discover_tier_ids(root: Path, map_name: str) -> list[str]:
    """从文件名里枚举这张地图出现过的 tier（不读文件内容，只用来决定"要加载哪些"）。"""
    if not root.is_dir():
        return []
    found: list[str] = []
    for path in sorted(root.glob(f"{map_name}__*{GRID_SUFFIX}")):
        stem = path.name[: -len(GRID_SUFFIX)]
        body = stem[len(map_name) + 2 :]
        head, _, _tail = body.rpartition("_")
        tier = head if head else body
        if tier and tier not in found:
            found.append(tier)
    return found


def _load_matching(
    root: Path,
    paths: list[Path],
    map_name: str,
    tier: str,
    wanted_zoom: str,
) -> DenseGrid | None:
    """在候选里挑出真正属于该层的文件并加载。

    以文件内的 ``meta`` 为准做校验（``map_name`` 相等、``tier`` 相等、指定了 zoom 时
    ``meta.zoom`` 也要相等），文件名只用来预筛——别的工具写出的文件名不一定规范。
    """
    fallback: DenseGrid | None = None
    for path in paths:
        try:
            grid = load_grid(path)
        except (OSError, TypeError, ValueError):
            continue
        if grid.meta.map_name and grid.meta.map_name != map_name:
            continue
        if normalize_tier(grid.meta.tier) != tier:
            continue
        if wanted_zoom and str(grid.meta.zoom) != wanted_zoom:
            continue
        if wanted_zoom:
            return grid
        if fallback is not None:
            raise ValueError(
                f"层 {map_name!r}/{tier or '主图'!r} 有多个候选网格且未指定 zoom："
                f"{[p.name for p in paths]}；请显式指定 zoom"
            )
        fallback = grid
    return fallback


@dataclass
class LayeredMap:
    """一张地图的全部分层 + 跨层连接。

    ``layers`` 的键是 :data:`LayerKey`；``main_key`` 是主图那一层（可能不存在，
    例如只发布了分层而没发布主图的场景）。

    ``declared_tiers`` 是分层目录（``catalog.json``）里**声明过**的层，按 ``level_id``
    分组——它包含"已声明但还没有网格文件"的层。这个区别只影响一种判定：某 ``level_id``
    下声明了层、但一个网格都没有时应当报 ``no_grid``（而不是退化成 ``main``）。没有
    catalog 时这个信息无从得知，那种情况会退化成 ``main``。
    """

    layers: dict[LayerKey, DenseGrid] = field(default_factory=dict)
    links: tuple[TierLink, ...] = ()
    #: ``level_id -> (tier, ...)``，来自分层目录；缺省表示没有目录信息
    declared_tiers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 是否真的读到了分层目录。为假时 :func:`resolve_tier_for_point` 只能拿"磁盘上发现
    #: 的层"当候选，而那会把**别的 level_id** 的层混进来（map02 的目录里有 9 个 level_id，
    #: 目前只有 lv005 有网格，所以现在"恰好"不出错）。此时判定结果是**降级**的，调用方
    #: 应当提示用户而不是照用。
    catalog_loaded: bool = False
    #: 编辑器算出的"这张图能不能导航"判定结果（纯数据质量信号，见模块文档）
    ready: bool = True
    blockers: tuple[str, ...] = ()

    @property
    def main_key(self) -> LayerKey | None:
        for key in self.layers:
            if key[1] == "":
                return key
        return None

    def grid(self, map_name: str, tier: object) -> DenseGrid | None:
        return self.layers.get(layer_key(map_name, tier))

    def tier_ids(self) -> list[str]:
        return sorted(tier for _map, tier in self.layers)

    def links_of_layer(self, key: LayerKey) -> list[TierLink]:
        """所有与该层相接的连接（方向已归一成"从该层出去"）。"""
        out = []
        for link in self.links:
            if link.source.key == key:
                out.append(link)
            elif link.bidirectional and link.target.key == key:
                out.append(link.reversed)
        return out

    def summary(self) -> dict:
        """可直接写日志的简要统计。"""
        return {
            "layers": len(self.layers),
            "tiers": self.tier_ids(),
            "links": len(self.links),
            "ready": self.ready,
            "blockers": len(self.blockers),
        }


def load_catalog(path) -> dict[str, tuple[str, ...]]:
    """读分层目录 ``catalog.json``，返回 ``level_id -> (tier, ...)``。

    只用来回答"这个 ``level_id`` 下声明了哪些层"——它包含**还没有网格文件**的层，
    这是"某层没网格"与"根本没有分层"唯一能区分开的依据。文件不存在或形状不认识时
    返回空字典（等价于没有目录信息），不抛错：目录是可选增强，缺了不该让导航起不来。
    """
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (TypeError, ValueError, OSError):
        return {}
    entries = data.get("layers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return {}
    grouped: dict[str, list[str]] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        tier = normalize_tier(item.get("id") or item.get("tier"))
        level_id = str(item.get("level_id") or "").strip()
        if not tier or not level_id:
            continue
        grouped.setdefault(level_id, [])
        if tier not in grouped[level_id]:
            grouped[level_id].append(tier)
    return {level: tuple(tiers) for level, tiers in grouped.items()}


def load_layered_map(
    layer_dir,
    map_name: str,
    *,
    zoom: str = "",
    tiers: list[str] | None = None,
) -> LayeredMap:
    """装配一张分层地图：层 + 跨层连接 + 分层目录（有就带上）。

    这是给调用方的**首选入口**——单独调 :func:`discover_layers` 会丢掉声明过但还没有
    网格的层，层判定的 ``no_grid`` 分支就退化成 ``main``。

    ``links.json`` 与 ``catalog.json`` 都在同一个目录里找；都缺时返回"只有层"的地图，
    此时跨层不可达（而不是报错）——数据没准备好是常态，调用方按 ``ready``/``blockers``
    决定要不要提示用户。
    """
    root = Path(layer_dir)
    layers = discover_layers(root, map_name, zoom=zoom, tiers=tiers)
    catalog = load_catalog(root / "catalog.json")
    # 只留**本图**的连接：links.json 是一份全局表，可能含别的地图的连接。不过滤的话
    # 一张没有分层、也没有本图连接的普通地图会被判定成"有分层数据"，进而走进分层路径。
    name = str(map_name or "").strip()
    links = tuple(
        link for link in load_links(root / "links.json") if link.source.map_name == name or link.target.map_name == name
    )
    return LayeredMap(
        layers=layers,
        links=links,
        declared_tiers=catalog,
        # 目录没读到不是错误（它是编辑器的本地缓存，不进版本库），但层判定会降级到
        # "按文件发现"，调用方需要据此提示，而不是当成正常结果。
        catalog_loaded=bool(catalog),
    )


def endpoint_cells(grid: DenseGrid, rect: dict) -> tuple[tuple[int, int], ...]:
    """连接端点区域在该层网格上覆盖到的**可走格**。

    编辑器写矩形时会对齐到格（``[origin+ix*cs, origin+(ix+1)*cs)``），所以用
    ``floor``/``ceil`` 取下标区间正好落在被覆盖的格上；非对齐矩形也能正确处理
    （只取真正有重叠的格）。

    规划器把两端区域的可走格**全连接**（二分团）而不是取"区域中心最近的一格"：
    端点语义上就是一个区域，实测每个矩形含 2~14 格，全连接的代价可以忽略，而
    中心最近在 2 格矩形上选谁都是任意的。
    """
    cs = float(grid.meta.cell_size)
    ox, oz = float(grid.meta.origin[0]), float(grid.meta.origin[2])
    j_lo = math.floor((float(rect["x0"]) - ox) / cs)
    j_hi = math.ceil((float(rect["x1"]) - ox) / cs) - 1
    i_lo = math.floor((float(rect["z0"]) - oz) / cs)
    i_hi = math.ceil((float(rect["z1"]) - oz) / cs) - 1
    height, width = grid.shape
    cells = []
    for i in range(max(0, i_lo), min(height - 1, i_hi) + 1):
        for j in range(max(0, j_lo), min(width - 1, j_hi) + 1):
            if int(grid.cells[i, j]) == CELL_FREE:
                cells.append((i, j))
    return tuple(cells)


def resolve_tier_for_point(
    layered: LayeredMap,
    *,
    map_name: str,
    x: float,
    y: float,
    z: float,
    level_id: str = "",
    candidates: list[str] | None = None,
) -> dict:
    """把三维坐标解析到层，返回 ``{status, tier, matched}``。

    语义**逐条对齐** nav-grid-editor 的 ``resolve_position_floor``：同一份
    ``tests/data/floor_rule_vectors.json`` 两边各跑一遍，任何一侧改了行为另一侧就红。
    两边结论不一致会出现"编辑器说在 7 层、规划器按主图算"的错位。

    判定顺序：

    1. 地图名非法或坐标非有限数 → ``no_match``；
    2. 候选层 = 该 ``level_id`` 名下的分层；**另外只要主图网格存在，主图也是一个候选**；
    3. ``level_id`` 为空、等于地图名、或没有候选层 → ``main``（``tier`` 为空串）；
    4. 逐候选取网格的 ``floor_regions``（**没有网格文件的候选直接跳过**），点落在任一
       区间内即 ``matched``；
    5. 没有任何候选有网格 → ``no_grid``；
    6. **只有一个候选有网格 → 无条件 ``unique``**，即使该点不在那层的区间里；
    7. 否则按 ``matched``：恰好一个 → ``unique``；多个 → ``ambiguous``；零个 → ``no_match``。

    Args:
        candidates: 该 ``level_id`` 名下的分层 id 列表。**给不出来就得留 None**——
            留 None 时退化成"目录里发现的所有分层都是候选"。这会让"有分层但都没网格"
            与"压根没有分层"无法区分（前者本该报 ``no_grid``，后者报 ``main``），
            也会把别的 ``level_id`` 的分层混进候选。

    Returns:
        ``tier`` / ``matched`` 里的主图用 API 侧的拼写 ``"__main__"``（内部键是空串，
        用 :func:`normalize_tier` 归一）；``status == "main"`` 时 ``tier`` 是空串。
    """
    if not valid_map_name(map_name):
        return {"status": "no_match", "tier": "", "matched": [], "tier_source": "none"}
    try:
        x, y, z = float(x), float(y), float(z)
    except (TypeError, ValueError, OverflowError):
        return {"status": "no_match", "tier": "", "matched": [], "tier_source": "none"}
    if not all(math.isfinite(value) for value in (x, y, z)):
        return {"status": "no_match", "tier": "", "matched": [], "tier_source": "none"}

    wanted_level = str(level_id or "").strip()
    if wanted_level == str(map_name).strip() or not wanted_level:
        # level_id 为空 / 等于地图名：官方语义是"不在任何一个分层场景里"，与目录无关
        return {"status": "main", "tier": "", "matched": [], "tier_source": "none"}

    tier_ids = [tier for _map, tier in layered.layers if tier]
    if candidates is not None:
        wanted = [normalize_tier(tier) for tier in candidates]
        tier_source = "explicit"
    elif wanted_level in layered.declared_tiers:
        # 有分层目录时以目录为准：它包含"已声明但还没网格文件"的层，而只按文件发现
        # 会把那种情况误判成 main。
        wanted = [normalize_tier(tier) for tier in layered.declared_tiers[wanted_level]]
        tier_source = "catalog"
    else:
        # 没有目录信息：只能拿磁盘上发现的层当候选。**这是降级结果**——同地图别的
        # level_id 的层会被混进来（见 LayeredMap.catalog_loaded 的说明），查询 lv005 时
        # 可能命中 lv002 的层。用 tier_source 把它暴露出来，别静默照用。
        wanted = [normalize_tier(tier) for tier in tier_ids]
        tier_source = "files"
    candidate_tiers = list(dict.fromkeys(tier for tier in wanted if tier))
    keyed: list[tuple[str, DenseGrid | None]] = [(tier, layered.grid(map_name, tier)) for tier in candidate_tiers]
    # 主图只要存在网格就是候选（编辑器内部用 __main__ 称呼它）
    main_grid = layered.layers.get(layer_key(map_name, ""))
    if main_grid is not None:
        keyed.append((MAIN_TIER, main_grid))

    if not keyed:
        return {"status": "main", "tier": "", "matched": [], "tier_source": tier_source}

    entries = []
    for tier, grid in keyed:
        if grid is None:
            continue
        matched = any(region_contains(region, x, y, z) for region in grid.meta.floor_regions)
        entries.append((tier, matched))

    if not entries:
        return {"status": "no_grid", "tier": "", "matched": [], "tier_source": tier_source}
    if len(entries) == 1:
        return {
            "status": "unique",
            "tier": entries[0][0],
            "matched": [entries[0][0]],
            "tier_source": tier_source,
        }
    matched = [tier for tier, hit in entries if hit]
    if len(matched) == 1:
        return {"status": "unique", "tier": matched[0], "matched": matched, "tier_source": tier_source}
    if len(matched) > 1:
        return {"status": "ambiguous", "tier": "", "matched": matched, "tier_source": tier_source}
    return {"status": "no_match", "tier": "", "matched": [], "tier_source": tier_source}


def resolve_tier_by_height(
    layered: LayeredMap,
    *,
    map_name: str,
    x: float,
    y: float,
    z: float,
) -> dict:
    """**只用高度（Y）判定玩家在哪一层**，不依赖官方 WS 的 ``levelId``。

    这是产品侧采用的判据（用户决定）：``levelId`` 不可信——它来自 WS 消息，既可能缺失、
    也可能与实际所在层不一致；而 ``floor_regions`` 的 Y 区间是编辑器让用户逐处标定、并
    落到网格文件里的，是唯一能自证的数据。

    **与 :func:`resolve_tier_for_point` 的关系**：那个函数复刻的是 nav-grid-editor
    ``resolve_position_floor`` 的语义（用 ``level_id`` 选候选层、单候选无条件 ``unique``），
    用来跑共享测试向量、盯住编辑器的行为；**它不是生产路径**。生产路径是这个函数。

    判定规则：

    1. 候选 = 该地图**所有**声明了 ``floor_regions`` 的层，**主图同样是候选**
       （「主图不挖洞」：地表格也是真实可走的，玩家在地表就应当由主图的区间命中）；
    2. 命中 = 某个区间包含 ``(x, y, z)``（``cells`` 非空时先要求 X/Z 命中某格）；
    3. 恰好一层命中 → ``unique``；
    4. 多层命中 → ``ambiguous``，**列出全部候选，绝不挑一个**——两层高度区间重叠时挑错
       就是把角色送错层；
    5. 没有任何区间命中 → ``main``，``reason`` 说明是兜底而不是真的判定出主图。

    Args:
        layered: 已装配的分层地图（需要网格文件已加载，区间在 ``meta.floor_regions`` 里）。
        map_name / x / y / z: 地图名与世界坐标；``y`` 是判定依据，必传且必须有限。

    Returns:
        ``{status, tier, tier_id, matched, matched_ids, reason}``。词表与 nav-grid-editor 的
        ``resolve_tier_by_height`` **逐字对齐**（共享向量 v2 是契约，两边各跑一遍）：

        - ``status`` ∈ ``{"unique", "ambiguous", "main", "invalid"}``；
        - ``tier`` 是内部键（主图为空串），``tier_id`` 是 API 拼写（主图为 ``"__main__"``）；
        - ``matched`` / ``matched_ids`` 同理，是命中的层列表（``ambiguous`` 时是全部候选）；
        - ``reason`` ∈ ``{"region_hit", "overlap", "no_layers", "no_region_covers",
          "invalid_map", "invalid_position"}``。

        **``invalid`` 与 ``main`` 必须分开**：地图名非法或坐标非有限数时，这条位置消息本身
        不可用，**不代表"玩家在地表"**。把两者混为一谈会让消费方把视图切到大地图——那是
        一个基于坏输入的错误结论。``main`` 才是"判定结果就是大地图"。
    """

    def result(status: str, reason: str, tiers: list[str] | None = None) -> dict:
        """统一构造返回值：内部键与 API 拼写都给，省得每个调用点各自转一遍。"""
        keys = sorted(tiers or [])
        return {
            "status": status,
            "tier": keys[0] if status == "unique" else "",
            "tier_id": (keys[0] or MAIN_TIER) if status == "unique" else "",
            "matched": keys,
            "matched_ids": [tier or MAIN_TIER for tier in keys],
            "reason": reason,
        }

    if not valid_map_name(map_name):
        return result("invalid", "invalid_map")
    try:
        x, y, z = float(x), float(y), float(z)
    except (TypeError, ValueError, OverflowError):
        return result("invalid", "invalid_position")
    if not all(math.isfinite(value) for value in (x, y, z)):
        return result("invalid", "invalid_position")

    # 候选 = 声明了**有效** floor_regions 的层。区间全非法的层在加载时就被丢空了
    # （GridMeta 只保留合法区间），于是它不进候选——这正是向量「全部区间都非法 → no_layers」
    # 那条所要的行为。
    mapped = [
        (tier, grid) for (name, tier), grid in layered.layers.items() if name == map_name and grid.meta.floor_regions
    ]
    if not mapped:
        # 这张图一个高度区间都没标：兜底到大地图，但 reason 与"没有层"分开。
        # 实测这份数据的区间覆盖率极低（整张 map02 只有 2 段各 39 格），所以这条是常态。
        return result("main", "no_layers")

    matched = [
        tier for tier, grid in mapped if any(region_contains(region, x, y, z) for region in grid.meta.floor_regions)
    ]
    if len(matched) == 1:
        return result("unique", "region_hit", matched)
    if len(matched) > 1:
        # 顺序不保证（来自目录顺序），调用方不要依赖；排序只为日志稳定
        return result("ambiguous", "overlap", matched)
    return result("main", "no_region_covers")
