# -*- coding: utf-8 -*-
"""sync_*.py 公共工具：JSON 读写、ok.po 同步、lang JSON 官方译名回写。

供 scripts/sync_map_mark_langs.py 与 scripts/sync_wiki_item_langs.py 共用，
消除两脚本之间的重复实现（SonarCloud new_duplicated_lines）。
"""

import json
import sys
from pathlib import Path

try:
    import polib
except ImportError:
    polib = None


def write_json(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# 仓库数据支持的 6 种语言（wiki 官方表另有 ru_RU/th_TH/id_ID，不写入仓库 lang JSON）
REPO_LANGS = ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")

# 名称规范化映射：键名差异（全角罗马数字/括号/空格）归一到官方简中名，
# 如 储藏箱Ⅳ -> 储藏箱IV，避免因写法差异匹配不上官方译名
_ZH_NORM = str.maketrans({
    "Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV", "Ⅴ": "V", "Ⅵ": "VI",
    "（": "(", "）": ")", "，": ",", "；": ";", "　": "",
})


def norm_zh_name(name: str) -> str:
    """官方名键名差异归一（全角罗马数字/括号/空白）。"""
    return name.translate(_ZH_NORM).strip()


def build_official(merged: dict, zh_of) -> dict:
    """把合并表转为 {简中名: {lang: 官方译名}}，重复简中名取首次。

    zh_of(key, langs) -> 该条目的简中名（为空字符串时跳过）。
    """
    official = {}
    for key, langs in merged.items():
        zh = (zh_of(key, langs) or "").strip()
        if not zh or zh in official:
            continue
        official[zh] = {
            l: (v or "").strip()
            for l, v in langs.items()
            if l != "zh_CN" and (v or "").strip()
        }
    return official


def sync_po_entries(official: dict, locales: tuple, i18n_dir: Path,
                    quiet: bool = False) -> tuple[dict, list]:
    """把官方译名同步进 i18n/*/LC_MESSAGES/ok.po 并编译 .mo。

    - 精确匹配：msgid == 简中名 → 覆盖 msgstr 为官方译名（官方有值时）；
    - 缺失条目：新增；
    - 官方无值 / 语言无变化时不写入。
    返回 (每语言统计, 变更列表)。
    """
    if polib is None:
        if not quiet:
            print("  [po] polib not installed; skipping ok.po sync")
        return {}, []

    all_stats = {}
    all_touched = []
    for loc in locales:
        po_path = i18n_dir / loc / "LC_MESSAGES" / "ok.po"
        if not po_path.exists():
            if not quiet:
                print(f"  [po] missing {po_path}")
            continue
        po = polib.pofile(str(po_path))
        by_mid = {}
        for entry in po:
            by_mid[entry.msgid.rstrip("\n")] = entry
        stats = 0
        touched = []
        for zh, vals in official.items():
            new = (vals.get(loc) or "").strip()
            if not new:
                continue
            entry = by_mid.get(zh)
            if entry is None:
                po.append(polib.POEntry(msgid=zh, msgstr=new))
                stats += 1
                touched.append((zh, "", new))
            elif entry.msgstr != new:
                touched.append((zh, entry.msgstr, new))
                entry.msgstr = new
                stats += 1
        if stats:
            po.save(str(po_path))
            po.save_as_mofile(str(po_path).replace(".po", ".mo"))
            print(f"  [po] {loc}: {stats} entries updated")
        elif not quiet:
            print(f"  [po] {loc}: no changes")
        all_stats[loc] = stats
        all_touched.extend(touched)
    return all_stats, all_touched


def sync_zh_cn_self_patch(zh_names, i18n_dir: Path, update_existing: bool = True,
                          quiet: bool = False) -> tuple[dict, list]:
    """zh_CN 官方简中名自补（msgid == msgstr）。

    update_existing=False 时只补缺失条目，不动已有条目。
    返回 (每语言统计, 变更列表)。
    """
    if polib is None:
        if not quiet:
            print("  [po] polib not installed; skipping ok.po sync")
        return {}, []

    po_path = i18n_dir / "zh_CN" / "LC_MESSAGES" / "ok.po"
    if not po_path.exists():
        if not quiet:
            print(f"  [po] missing {po_path}")
        return {}, []

    po = polib.pofile(str(po_path))
    by_mid = {}
    for entry in po:
        by_mid[entry.msgid.rstrip("\n")] = entry
    stats = 0
    touched = []
    for zh in zh_names:
        zh = (zh or "").strip()
        if not zh:
            continue
        entry = by_mid.get(zh)
        if entry is None:
            po.append(polib.POEntry(msgid=zh, msgstr=zh))
            stats += 1
            touched.append((zh, "", zh))
        elif update_existing and entry.msgstr != zh:
            touched.append((zh, entry.msgstr, zh))
            entry.msgstr = zh
            stats += 1
    if stats:
        po.save(str(po_path))
        po.save_as_mofile(str(po_path).replace(".po", ".mo"))
    return {"zh_CN": stats}, touched


def sync_lang_jsons(official: dict, lang_dir: Path, skip_files: tuple = ()
                    ) -> tuple[dict, list]:
    """以官方表覆盖/补齐 assets/lang/*.json 中相同中文的 string/pattern 节点。

    - 匹配键：节点 zh_CN 值（string 或 pattern）== 官方简中名
      （经 norm_zh_name 归一，全角罗马数字/括号等写法差异也能命中）；
    - string/pattern 类型不限，目标语言节点含哪个键就替换哪个值；
    - 语言节点缺失时新建（按 zh_CN 节点的 string/pattern 风格）；
    - 只补仓库支持的 6 种语言（REPO_LANGS），wiki 官方表的 ru_RU/th_TH/id_ID
      不写入仓库 lang JSON，已有这类节点也不动；
    - zh_CN 不覆盖；仅官方有值且与现有不同时写入。
    返回 (每文件统计, 变更列表)。
    """
    # 官方名查找表：原始键 + 归一化键都指向同一份译名（原始键优先）
    lookup = {}
    for zh, vals in official.items():
        lookup.setdefault(zh, vals)
        lookup.setdefault(norm_zh_name(zh), vals)
    all_stats = {}
    all_touched = []
    for path in sorted(lang_dir.glob("*.json")):
        if path.name in skip_files:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        stats = 0
        touched = []
        for key, node in data.items():
            zh_node = node.get("zh_CN")
            if not isinstance(zh_node, dict):
                continue
            zh = ""
            sub_style = "pattern"
            for sub in ("string", "pattern"):
                val = zh_node.get(sub)
                if isinstance(val, str):
                    zh = val.strip()
                    sub_style = sub
                    break
            if not zh:
                continue
            vals = lookup.get(zh)
            if vals is None:
                vals = lookup.get(norm_zh_name(zh))
            if not vals:
                continue
            for lang in REPO_LANGS:
                val = (vals.get(lang) or "").strip()
                if not val:
                    continue
                cur = node.get(lang)
                has_val = (
                    isinstance(cur, dict)
                    and (
                        isinstance(cur.get("string"), str)
                        or isinstance(cur.get("pattern"), str)
                    )
                )
                if has_val:
                    for sub in ("string", "pattern"):
                        if isinstance(cur.get(sub), str) and cur[sub] != val:
                            cur[sub] = val
                            stats += 1
                            touched.append((key, zh, lang, val))
                else:
                    node[lang] = {sub_style: val}
                    stats += 1
                    touched.append((key, zh, lang, val))
        if stats:
            write_json(path, data)
        all_stats[path.name] = stats
        all_touched.extend(touched)
    return all_stats, all_touched


def print_po_result(po_stats: dict, po_touched: list) -> None:
    for loc, n in po_stats.items():
        print(f"  {loc}: {n} entries updated")
    for mid, old, val in po_touched:
        print(f"  {mid!r}: {old!r} -> {val!r}")


def print_json_result(json_stats: dict, json_touched: list) -> None:
    for fname, n in json_stats.items():
        if n:
            print(f"  {fname}: {n} values updated")
    for key, zh, lang, val in json_touched:
        print(f"  {key} ({zh}) {lang}: {val!r}")


def main() -> int:
    print("Shared helpers module; not meant to be run directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
