# -*- coding: utf-8 -*-
"""导航网格建图 CLI。

用法（在仓库根目录）：
    uv run python scripts/build_nav_grid.py --map map01
    uv run python scripts/build_nav_grid.py --map all

输入：
    configs/nav/            轨迹(jsonl)与经验(json)：滑索/传送点/受阻
    assets/items/map/summary.json   物品点位（含标定传送点，自动入 teleports）
输出：
    assets/nav/<mapId>.grid.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 直接以脚本方式运行时，把仓库根加入 sys.path 以便 import src.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.nav import storage
from src.nav.grid import GridMap, build_grid


def load_waypoints(summary_path: str | None) -> dict[str, dict[str, list[dict]]]:
    if not summary_path:
        return {}
    path = Path(summary_path)
    if not path.exists():
        print(f"[warn] 未找到点位文件: {path}，跳过 waypoint 点位")
        return {}
    with path.open(encoding="utf-8") as fp:
        data = json.load(fp)
    out: dict[str, dict[str, list[dict]]] = {}
    for map_id, items in data.items():
        out[map_id] = {name: list(pts) for name, pts in items.items()}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="构建导航网格（3D 体素，机器可读）")
    parser.add_argument("--map", required=True, help="地图 id（如 map01），或 all")
    parser.add_argument("--data-dir", default="configs/nav", help="轨迹与经验目录")
    parser.add_argument("--out", default="assets/nav", help="网格输出目录")
    parser.add_argument("--waypoints", default="assets/items/map/summary.json",
                        help="物品点位文件；传 none 禁用")
    parser.add_argument("--teleport-items", default="协议传送点,传送台",
                        help="waypoints 中按传送点入图的物品名（逗号分隔）；传 none 禁用")
    parser.add_argument("--cell-size", type=float, default=1.0, help="网格尺寸（米），默认 1")
    args = parser.parse_args(argv)

    waypoints_all = load_waypoints(None if args.waypoints.lower() == "none" else args.waypoints)
    teleport_items: set[str] = set()
    if args.teleport_items.lower() != "none":
        teleport_items = {n.strip() for n in args.teleport_items.split(",") if n.strip()}

    map_ids: list[str]
    if args.map == "all":
        map_ids = sorted(storage.list_recorded_maps(args.data_dir) | set(waypoints_all.keys()))
    else:
        map_ids = [args.map]

    for map_id in map_ids:
        t0 = time.time()
        grid = build_grid(
            trails=storage.read_trails(args.data_dir, map_id),
            waypoints=waypoints_all.get(map_id, {}),
            teleports=storage.read_experience(args.data_dir, map_id, storage.TELEPORTS),
            zips=storage.read_experience(args.data_dir, map_id, storage.ZIPS),
            blocked=storage.read_experience(args.data_dir, map_id, storage.BLOCKED),
            teleport_item_names=teleport_items,
            cell_size=args.cell_size,
        )
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{map_id}.grid.json"
        grid.save(path)
        grid._ensure_free()
        print(
            f"[{map_id}] cells={len(grid.cells)} free={len(grid._free)} "
            f"blocked={len(grid.blocked)} teleports={len(grid.teleports)} "
            f"zips={len(grid.zips)} 耗时 {time.time() - t0:.2f}s -> {path}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
