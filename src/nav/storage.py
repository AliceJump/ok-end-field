# -*- coding: utf-8 -*-
"""导航数据的文件读取。

目录约定（相对仓库根，与项目既有 assets/configs 风格一致）：
- configs/nav/         建图输入数据：轨迹、滑索、传送点、经验（受阻/已验证），
                       由外部采集/学习工具产出
- assets/nav/          派生产物：<mapId>.grid.json

经验文件按「坐标」存储（不按节点 id），重 build 后仍可迁移。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

# 经验文件名（不含扩展名）
ZIPS = "zips"
TELEPORTS = "teleports"
BLOCKED = "blocked"
VERIFIED_DROPS = "verified_drops"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def trail_path(data_dir: Path | str, map_id: str) -> Path:
    """原始轨迹文件（JSONL，每行一条 {"t","x","y","z"}）。"""
    return _ensure_dir(Path(data_dir)) / f"trails_{map_id}.jsonl"


def experience_path(data_dir: Path | str, map_id: str, name: str) -> Path:
    return Path(data_dir) / f"{name}_{map_id}.json"


def read_trails(data_dir: Path | str, map_id: str) -> list[dict]:
    """读取全部原始轨迹点。"""
    path = trail_path(data_dir, map_id)
    if not path.exists():
        return []
    points = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                points.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return points


def read_experience(data_dir: Path | str, map_id: str, name: str) -> list[Any]:
    path = experience_path(data_dir, map_id, name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def list_recorded_maps(data_dir: Path | str) -> set[str]:
    """扫描 data_dir 里出现过的 mapId（轨迹文件 + 经验文件）。"""
    root = Path(data_dir)
    if not root.exists():
        return set()
    maps: set[str] = set()
    for path in root.glob("*.jsonl"):
        name = path.stem  # trails_<mapId>
        if name.startswith("trails_"):
            maps.add(name[len("trails_"):])
    for path in root.glob("*.json"):
        name = path.stem
        for prefix in (ZIPS, TELEPORTS, BLOCKED, VERIFIED_DROPS):
            if name.startswith(prefix + "_"):
                maps.add(name[len(prefix) + 1:])
    return maps


def iter_grid_files(grid_dir: Path | str) -> Iterator[Path]:
    root = Path(grid_dir)
    if not root.exists():
        return
    yield from sorted(root.glob("*.grid.json"))


def pick_best_2d_grid(grid_dir: Path | str, map_id: str) -> tuple:
    """在 grid_dir 里找 <mapId>_*_2d.grid.json，返回 free 格最多的那份。

    二维网格是导航网格编辑器的产物（无高度 [x,z] 数据）；同地图可能有多个
    zoom 的文件，取覆盖最全（cells 最多）的。返回 (grid_or_None, path_or_None)。
    """
    from src.nav.grid import GridMap

    root = Path(grid_dir)
    best = None
    best_path = None
    for cand in sorted(root.glob(f"{map_id}_*_2d.grid.json")):
        try:
            g = GridMap.load(cand)
        except Exception:
            continue
        if best is None or len(g.cells) > len(best.cells):
            best, best_path = g, cand
    return best, best_path
