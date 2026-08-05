# -*- coding: utf-8 -*-

"""补全 assets/lang/*.json 缺失语言（两阶段，多语言值匹配）。

阶段 A（数据源 → lang）：把仓库内官方译名数据源收集为词条，按
「任意语言值」（不只 zh_CN）匹配 lang 节点，命中且唯一时补全缺失语言。
数据源：
  - assets/data/wiki_items.json   （wiki 官方物品，8 语）
  - assets/data/map_marks.json    （地图标记，6 语）
  - tools/official_five_lang.json （地图 API 五语快照）
  - assets/lang/characters.json   （角色 lang，6 语完整，作准官方数据源）

阶段 B（lang 内部互相补全）：以同样匹配规则，在 assets/lang/*.json
之间交叉补齐缺失语言。

规则：只补缺失、不覆盖已有；节点风格跟随 zh_CN（string/pattern）；
某缺失语言存在多个不同候选值时跳过（保守）。

用法：
  python scripts/lang_fill_missing.py            # A + B 并写回
  python scripts/lang_fill_missing.py --dry-run  # 只看变更，不写回
  python scripts/lang_fill_missing.py --sources-only
  python scripts/lang_fill_missing.py --cross-only
"""

import argparse
import json
import sys
from pathlib import Path

from _lang_sync_common import (
    build_lang_value_index,
    fill_missing_cross_files,
    fill_missing_from_index,
    normalize_value,
    write_json,
)

ROOT = Path(__file__).resolve().parent.parent
LANG_DIR = ROOT / "assets" / "lang"
DATA_DIR = ROOT / "assets" / "data"
SNAPSHOT_JSON = ROOT / "tools" / "official_five_lang.json"

# 地图 API 快照的语言键 -> 仓库 lang 键
_FIVE_MAP = {
    "zh": "zh_CN",
    "en": "en_US",
    "ja": "ja_JP",
    "ko": "ko_KR",
    "es": "es_ES",
}


def _flat_langs(path: Path) -> list[dict]:
    """读取 {官方名: {lang: {string/pattern: v}}} 结构的数据源。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for langs in data.values():
        entry = {}
        if not isinstance(langs, dict):
            continue
        for lang, node in langs.items():
            if not isinstance(node, dict):
                continue
            for sub in ("string", "pattern"):
                v = node.get(sub)
                if isinstance(v, str) and v.strip():
                    entry[lang] = v.strip()
                    break
        if entry:
            entries.append(entry)
    return entries


def _collect_five_lang(path: Path) -> list[dict]:
    """扁平化官方地图五语快照（嵌套 walk，取含 zh+en 的节点）。"""
    entries = []

    def walk(o):
        if isinstance(o, dict):
            if "zh" in o and "en" in o:
                entry = {}
                for src, dst in _FIVE_MAP.items():
                    v = o.get(src)
                    if isinstance(v, str) and v.strip() and v != "?":
                        entry[dst] = v.strip()
                if entry:
                    entries.append(entry)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return entries


def collect_source_entries() -> list[dict]:
    """收集全部官方译名数据源词条。"""
    entries = []
    for name in ("wiki_items.json", "map_marks.json"):
        p = DATA_DIR / name
        if p.exists():
            entries.extend(_flat_langs(p))
    if SNAPSHOT_JSON.exists():
        entries.extend(_collect_five_lang(SNAPSHOT_JSON))
    # 角色 lang（6 语完整）作为准官方数据源
    chars = LANG_DIR / "characters.json"
    if chars.exists():
        entries.extend(_flat_langs(chars))
    return entries


def stage_sources(data_map: dict, index: dict, dry_run: bool) -> tuple[dict, list, set]:
    """阶段 A：数据源词条索引补全各 lang JSON。返回 (stats, touched, dirty_paths)。"""
    stats = {}
    all_touched = []
    dirty = set()
    for path, data in data_map.items():
        touched = fill_missing_from_index(data, index)
        if touched:
            stats[path.name] = len(touched)
            all_touched.extend((path.name, *t) for t in touched)
            dirty.add(path)
    return stats, all_touched, dirty


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印变更，不写回文件")
    ap.add_argument("--sources-only", action="store_true",
                    help="只跑阶段 A（数据源 → lang）")
    ap.add_argument("--cross-only", action="store_true",
                    help="只跑阶段 B（lang 内部互相补全）")
    args = ap.parse_args()

    # 一次性加载全部 lang JSON 到内存
    data_map = {}
    for path in sorted(LANG_DIR.glob("*.json")):
        data_map[path] = json.loads(path.read_text(encoding="utf-8"))
    name_to_path = {p.name: p for p in data_map}

    total_touched = 0
    dirty: set[Path] = set()

    if not args.cross_only:
        entries = collect_source_entries()
        print(f"[A] 数据源词条: {len(entries)}")
        index = build_lang_value_index(entries)
        stats, touched, dirty_a = stage_sources(data_map, index, args.dry_run)
        dirty |= dirty_a
        n = sum(stats.values())
        total_touched += n
        print(f"[A] 从数据源补全: {n} 处")
        for fname, c in sorted(stats.items()):
            print(f"    {fname}: {c}")
        for fname, key, zh, lang, val in touched:
            print(f"    {fname} {key} ({zh}) {lang}: {val!r}")

    if not args.sources_only:
        stats, touched = fill_missing_cross_files(data_map)
        dirty |= {name_to_path[f] for f in stats}
        n = sum(stats.values())
        total_touched += n
        print(f"[B] lang 内部互相补全: {n} 处")
        for fname, c in sorted(stats.items()):
            print(f"    {fname}: {c}")
        for fname, key, zh, lang, val in touched:
            print(f"    {fname} {key} ({zh}) {lang}: {val!r}")

    if not args.dry_run and dirty:
        for path in sorted(dirty, key=lambda p: str(p)):
            write_json(path, data_map[path])
        print(f"已写回 {len(dirty)} 个文件: {', '.join(p.name for p in sorted(dirty, key=lambda p: str(p)))}")
    else:
        print("dry-run：未写回")

    return 0


if __name__ == "__main__":
    sys.exit(main())
