# -*- coding: utf-8 -*-
"""独立校验导航网格 ``*.grid.npz``（不依赖 src/nav/grid_io，专门用来独立验收编辑器产出）。

刻意用原始 ``numpy`` + ``json`` 重新实现一遍规范检查，**不复用** ``grid_io``——
否则"自己验自己"，读方和写方可能同时错在同一个假设上。

检查项：
- npz 成员恰好 ``cells`` + ``meta``；``meta`` 必须是**字符串数组**（object 数组会逼读方
  开 ``allow_pickle``，直接判错）。
- ``cells``：uint8、二维、非空、取值只允许 0/1/2。
- ``meta``：``magic`` / ``schema_version`` 精确匹配、``cell_size > 0``、
  ``origin`` 是 3 个数字、``axis_convention`` 与规范逐字一致、``map_name``/``zoom`` 存在。
- 统计：形状、三态计数与占比、``origin``/``cell_size``/``extent``（独立算）。
- 可疑点：可行走区域的连通性（分几块、最大块占比、单格孤点）——分块本身没错，
  但**跨块导航必然失败**，单格孤点几乎一定是误点；另外会提示"没有任何阻挡格"。
- ``--against <旧 JSON>``：与编辑器旧格式逐格对拍（自己实现 origin 平移与
  "blocked 优先"规则，不调 ``grid_io``）。

用法::

    python scripts/nav/verify_grid.py <文件或目录>...
    python scripts/nav/verify_grid.py grids2d/*.npz
    python scripts/nav/verify_grid.py x.npz --against tiles/maps/base01/4/base01_4.grid.json

退出码：0 = 全部通过（允许有"可疑点"提示）；1 = 有硬错误或对拍不一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

MAGIC = "okef-nav-grid"
SCHEMA_VERSION = 2
AXIS_CONVENTION = (
    "world = origin + (i,j)*cell_size; i=row=z, j=col=x, both positive; "
    "origin is the min corner of cells[0,0]; 2D ignores y"
)
VALID_STATES = {0, 1, 2}
STATE_NAMES = {0: "未知", 1: "可行走", 2: "阻挡"}
GRID_SUFFIX = ".grid.npz"


class Report:
    """单个网格文件的诊断结果。

    ``info`` 是正常统计，``warns`` 是可人工判断的可疑点，``errors`` 是硬错误。
    只要存在 ``errors``，命令行最终退出码就是非零。
    """

    def __init__(self, name: str):
        """创建指定文件名的空报告。"""
        self.name = name
        self.errors: list[str] = []
        self.warns: list[str] = []
        self.lines: list[str] = []

    def info(self, text: str) -> None:
        """记录一条普通统计信息。"""
        self.lines.append(text)

    def error(self, text: str) -> None:
        """记录硬错误；会导致该文件校验不通过。"""
        self.errors.append(text)

    def warn(self, text: str) -> None:
        """记录需要人工判断的可疑点，不影响通过状态。"""
        self.warns.append(text)

    def dump(self) -> None:
        """按统计、可疑点、错误的顺序输出报告。"""
        head = f"=== {self.name}"
        print(head)
        for line in self.lines:
            print("   " + line)
        for w in self.warns:
            print("   [可疑] " + w)
        for e in self.errors:
            print("   [错误] " + e)
        print(f"   -> {'通过' if not self.errors else '不通过'}")


def load_raw(path: Path, rep: Report):
    """按规范读 npz，返回 (cells, meta_dict)。任何不合规都记进 rep.errors。"""
    try:
        with np.load(path, allow_pickle=False) as data:
            members = sorted(data.files)
            if members != ["cells", "meta"]:
                rep.error(f"npz 成员应为 ['cells','meta']，实际 {members}")
                return None, None
            cells = data["cells"]
            meta_raw = data["meta"]
    except Exception as exc:  # 含 "Object arrays cannot be loaded when allow_pickle=False"
        rep.error(f"读取失败：{type(exc).__name__}: {exc}"
                  "（meta 若是 object 数组就会这样，必须写成 numpy 字符串数组）")
        return None, None

    # ---- cells ----
    if cells.dtype != np.uint8:
        rep.error(f"cells dtype 应为 uint8，实际 {cells.dtype}")
    if cells.ndim != 2:
        rep.error(f"cells 应为二维，实际 ndim={cells.ndim}")
    if cells.size == 0:
        rep.error("cells 为空网格")
    if cells.size:
        bad = sorted(set(np.unique(cells).tolist()) - VALID_STATES)
        if bad:
            rep.error(f"cells 出现非法取值 {bad}（只允许 0=未知/1=可行走/2=阻挡）")

    # ---- meta ----
    if meta_raw.dtype.kind not in ("U", "S"):
        rep.error(f"meta 应为 numpy 字符串数组（np.array(json.dumps(...))），实际 dtype={meta_raw.dtype}")
        return cells, None
    if meta_raw.size != 1:
        rep.error(f"meta 应是单个字符串，实际 size={meta_raw.size}")
        return cells, None
    text = meta_raw.item()
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        meta = json.loads(text)
    except ValueError as exc:
        rep.error(f"meta 不是合法 JSON: {exc}")
        return cells, None
    if not isinstance(meta, dict):
        rep.error("meta 顶层应为 JSON 对象")
        return cells, None

    if meta.get("magic") != MAGIC:
        rep.error(f"magic 应为 {MAGIC!r}，实际 {meta.get('magic')!r}")
    if meta.get("schema_version") != SCHEMA_VERSION:
        rep.error(f"schema_version 应为 {SCHEMA_VERSION}，实际 {meta.get('schema_version')!r}")
    if str(meta.get("axis_convention") or "") != AXIS_CONVENTION:
        rep.warn(f"axis_convention 与规范文字不一致：{meta.get('axis_convention')!r}")
    try:
        cell_size = float(meta.get("cell_size"))
        if not cell_size > 0:
            raise ValueError
    except (TypeError, ValueError):
        rep.error(f"cell_size 必须是正数，实际 {meta.get('cell_size')!r}")
        cell_size = None
    origin = meta.get("origin")
    if not (isinstance(origin, (list, tuple)) and len(origin) == 3
            and all(isinstance(v, (int, float)) for v in origin)):
        rep.error(f"origin 应是 3 个数字，实际 {origin!r}")
        origin = None
    for key in ("map_name", "zoom"):
        if not str(meta.get(key) or "").strip():
            rep.warn(f"meta 缺少 {key}")
    return cells, {"raw": meta, "cell_size": cell_size, "origin": origin}


def report_cells(cells: np.ndarray, meta: dict, rep: Report) -> None:
    """输出形状、三态占比、世界范围和连通性诊断。"""
    if cells is None or meta is None or meta["cell_size"] is None or meta["origin"] is None:
        return
    h, w = int(cells.shape[0]), int(cells.shape[1])
    total = h * w
    rep.info(f"cells: uint8 {cells.shape}，{total} 格")
    counts = {s: int((cells == s).sum()) for s in (0, 1, 2)}
    rep.info("三态：" + "  ".join(
        f"{STATE_NAMES[s]} {counts[s]} ({counts[s] / total * 100:.1f}%)" for s in (0, 1, 2)))
    origin, cs = meta["origin"], meta["cell_size"]
    rep.info(f"origin=({origin[0]:.2f}, {origin[1]:.2f}, {origin[2]:.2f})  cell_size={cs}")
    extent = (origin[0], origin[2], origin[0] + w * cs, origin[2] + h * cs)
    rep.info(f"extent(x/z)=({extent[0]:.2f}, {extent[1]:.2f}) ~ ({extent[2]:.2f}, {extent[3]:.2f})")
    rep.info(f"map_name={meta['raw'].get('map_name')!r} zoom={meta['raw'].get('zoom')!r} "
             f"source={meta['raw'].get('source')!r} created={meta['raw'].get('created')!r}")

    # 可疑点
    if counts[1] == 0:
        rep.error("没有任何可行走格")
    if counts[2] == 0:
        rep.warn("没有任何阻挡格：多半是没标墙，规划会缺少边界")
    _report_connectivity(cells, rep)


def _report_connectivity(cells: np.ndarray, rep: Report) -> None:
    """可行走 / 规划器可达 两个口径的连通性（都用规划器真实的邻域规则）。

    关键：``neighbors`` 返回的是**非阻挡**格，也就是"可行走 ∪ 未知"都算能走
    （未知由代价模型 ×5 加价）。所以只按"可行走"算块数会**低估** A* 的失败率：

    - **可行走连通块**（不冒险即可达）：衡量"采集到的轨迹连不连续"，是数据质量问题；
    - **规划器可达连通块**（可行走 ∪ 未知）：A* 真正能到哪；**跨这些块才必然失败**
      （隔的是阻挡，冒险也过不去）。

    两者的邻域规则一致：八连通，且斜向时两条正交邻格必须**非阻挡**
    （与 ``grid_io.neighbors`` 禁斜穿墙角同规则）。
    """
    blocked = cells == 2
    free_mask = cells == 1
    passable_mask = cells != 2
    parts: dict[str, list[int]] = {}
    for label, mask in (("可行走（不冒险即可达）", free_mask),
                        ("规划器可达（可行走∪未知）", passable_mask)):
        if int(mask.sum()) > 200_000:
            rep.info(f"{label}：{int(mask.sum())} 格，数量过大，跳过连通性检查")
            continue
        sizes = _components(mask, blocked)
        parts[label] = sizes
        rep.info(f"{label}：{len(sizes)} 块（大小 {sizes[:8]}{'…' if len(sizes) > 8 else ''}），"
                 f"最大占比 {sizes[0] / int(mask.sum()) * 100:.1f}%")

    reachable = parts.get("规划器可达（可行走∪未知）")
    if reachable and len(reachable) > 1:
        rep.warn(f"规划器可达域分成 {len(reachable)} 块：跨块导航必然失败"
                 f"（隔的是阻挡，允许冒险也过不去）")

    # 单格情况按**邻居事实**报，不用图论"单格块"——后者会被我们自己的禁斜穿规则放大：
    # 一个格只要对角有可通行格、而角上被挡，它在规划器图里就是孤立的，但游戏里人
    # 其实能从对角走过去。这种"我们自己造的孤立"不该提示用户。
    facts = _single_cell_facts(cells)
    if facts["free_walled"]:
        rep.warn(f"{len(facts['free_walled'])} 个可行走格八邻全是阻挡（被彻底围住）："
                 + "、".join(f"({i},{j})" for i, j in facts["free_walled"][:6])
                 + " —— 若确认无用途可在编辑器里删掉")
    if facts["free_lonely"]:
        rep.info(f"{len(facts['free_lonely'])} 个可行走格没有可行走邻居（孤立）："
                 + "、".join(f"({i},{j})" for i, j in facts["free_lonely"][:6])
                 + " —— 邻接的是未知格，规划可冒险连出去；"
                 "是「邻域还没标全」还是「误点」要看画它时的意图，工具不替用户判断")
    if facts["unknown_walled"]:
        rep.info(f"另有 {len(facts['unknown_walled'])} 个**未知**格八邻全是阻挡（封闭小空间）："
                 "这些格没人画过，与误点无关，规划会绕开")


def _single_cell_facts(cells: np.ndarray) -> dict:
    """按"八邻是什么"统计值得提示的单格，返回三组坐标（向量化，边界只数界内邻居）。

    刻意不用图论"单格连通块"：那会被我们自己的**禁斜穿墙角**规则放大——一个格只要
    对角有可通行格、而角上被挡，它在规划器图里就是孤立的，但游戏里人能从对角走过去。
    这种"我们自己造的孤立"不该提示用户。

    - ``free_walled``：可行走格，界内八邻全是阻挡 —— 真被围住；
    - ``free_lonely``：可行走格，界内没有可行走邻居 —— 孤立（多半是邻域没标全）；
    - ``unknown_walled``：未知格，界内八邻全是阻挡 —— 被墙围死的未探明小空间（非人为）。
    """
    h, w = cells.shape
    blocked = cells == 2
    free = cells == 1
    n_valid = np.zeros((h, w), np.int8)
    n_blocked = np.zeros((h, w), np.int8)
    n_free = np.zeros((h, w), np.int8)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if not (di or dj):
                continue
            i0, i1 = max(0, -di), h - max(0, di)
            j0, j1 = max(0, -dj), w - max(0, dj)
            sl = (slice(i0, i1), slice(j0, j1))
            nb = (slice(i0 + di, i1 + di), slice(j0 + dj, j1 + dj))
            n_valid[sl] += 1
            n_blocked[sl] += blocked[nb]
            n_free[sl] += free[nb]
    walled = (n_valid == 8) & (n_blocked == 8)
    out = {
        "free_walled": np.argwhere(free & walled).tolist(),
        "free_lonely": np.argwhere(free & (n_free == 0) & ~walled).tolist(),
        "unknown_walled": np.argwhere((cells == 0) & walled).tolist(),
    }
    return {k: [tuple(int(v) for v in p) for p in ps] for k, ps in out.items()}


def _components(mask: np.ndarray, blocked: np.ndarray) -> list[int]:
    """按规划器的邻域规则标连通块，返回各块大小（降序）。

    规则与 ``grid_io.DenseGrid.neighbors`` 一致：八连通；斜向时两条正交邻格
    必须非阻挡；越界视为阻挡。这样"规划器可达"那一栏就是 A* 的真实可达域。
    """
    h, w = mask.shape
    seen = np.zeros_like(mask)
    sizes: list[int] = []
    for start_i, start_j in np.argwhere(mask):
        if seen[start_i, start_j]:
            continue
        stack = [(int(start_i), int(start_j))]
        seen[start_i, start_j] = True
        size = 0
        while stack:
            i, j = stack.pop()
            size += 1
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if di == 0 and dj == 0:
                        continue
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < h and 0 <= nj < w):
                        continue
                    if seen[ni, nj] or not mask[ni, nj]:
                        continue
                    if di and dj and (blocked[i + di, j] or blocked[i, j + dj]):
                        continue          # 禁斜穿墙角，与规划器一致
                    seen[ni, nj] = True
                    stack.append((ni, nj))
        sizes.append(size)
    sizes.sort(reverse=True)
    return sizes


def compare_legacy(cells: np.ndarray, meta: dict, legacy_path: Path, rep: Report) -> None:
    """与编辑器旧 JSON 逐格对拍：自己实现 origin 平移、blocked 优先。"""
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception as exc:
        rep.error(f"读旧 JSON 失败 {legacy_path.name}: {exc}")
        return
    leg_cells = [(int(c), int(r)) for c, r in (raw.get("cells") or [])]
    leg_blocked = [(int(c), int(r)) for c, r in (raw.get("blocked") or [])]
    if not (leg_cells or leg_blocked):
        rep.info(f"对拍 {legacy_path.name}: 旧文件为空，跳过")
        return
    origin = raw.get("origin") or [0.0, 0.0, 0.0]
    cs = float(raw.get("cell_size") or 1.0)
    keys = leg_cells + leg_blocked
    min_col = min(k[0] for k in keys)
    min_row = min(k[1] for k in keys)
    # 独立算出应有的 origin（新语义 = 边界盒最小角）
    want_origin = (float(origin[0]) + min_col * cs, float(origin[2]) + min_row * cs)
    got = (meta["origin"][0], meta["origin"][2])
    if any(abs(a - b) > 1e-6 for a, b in zip(want_origin, got)):
        rep.error(f"origin 平移不符：应为 {tuple(round(v, 3) for v in want_origin)}，"
                  f"实际 {tuple(round(v, 3) for v in got)}")

    want = np.zeros(cells.shape, dtype=np.uint8)
    for col, row in leg_cells:
        i, j = row - min_row, col - min_col
        if 0 <= i < cells.shape[0] and 0 <= j < cells.shape[1]:
            want[i, j] = 1
    for col, row in leg_blocked:                  # 旧规则：blocked 覆盖 free
        i, j = row - min_row, col - min_col
        if 0 <= i < cells.shape[0] and 0 <= j < cells.shape[1]:
            want[i, j] = 2
    diff = int((want != cells).sum())
    if diff:
        mism = np.argwhere(want != cells)[:5]
        rep.error(f"对拍 {legacy_path.name}: {diff} 格不一致，例如 "
                  + ", ".join(f"(i={i},j={j}) 旧={STATE_NAMES[int(want[i, j])]}"
                              f"/新={STATE_NAMES[int(cells[i, j])]}" for i, j in mism))
    else:
        rep.info(f"对拍 {legacy_path.name}: 逐格一致（旧数据 {len(keys)} 个格坐标）")


def verify_one(path: Path, against: Path | None) -> Report:
    """校验单个网格；可选与旧版 JSON 逐格对拍。"""
    rep = Report(path.name)
    if not path.name.endswith(GRID_SUFFIX):
        rep.error(f"文件名必须是 *{GRID_SUFFIX}")
    cells, meta = load_raw(path, rep)
    if cells is not None and meta is not None:
        report_cells(cells, meta, rep)
        if against is not None:
            compare_legacy(cells, meta, against, rep)
    return rep


def collect(paths: list[Path]) -> list[Path]:
    """展开输入目录中的 ``*.grid.npz``，保持每目录排序稳定。"""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob(f"*{GRID_SUFFIX}") if x.is_file()))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"输入不存在: {p}")
    return out


def main(argv: list[str] | None = None) -> int:
    """命令行入口；返回进程退出码，0 表示全部通过。"""
    ap = argparse.ArgumentParser(description="独立校验导航网格 *.grid.npz")
    ap.add_argument("paths", nargs="+", type=Path, help="npz 文件或目录")
    ap.add_argument("--against", type=Path, default=None,
                    help="可选：与编辑器旧 grid.json 逐格对拍（单个文件时用）")
    args = ap.parse_args(argv)

    files = collect(args.paths)
    if not files:
        print(f"没找到 *{GRID_SUFFIX}")
        return 1
    bad = 0
    for f in files:
        rep = verify_one(f, args.against)
        rep.dump()
        bad += bool(rep.errors)
    print(f"\n{len(files) - bad}/{len(files)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
