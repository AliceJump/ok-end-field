# -*- coding: utf-8 -*-
"""sync_*.py 公共工具：JSON 读写、ok.po 同步、lang JSON 官方译名回写。

供 scripts/i18n/sync_map_mark_langs.py 与 scripts/i18n/sync_wiki_item_langs.py 共用，
消除两脚本之间的重复实现（SonarCloud new_duplicated_lines）。
"""

import json
import json5
import re
import sys
from collections import defaultdict
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
            lang: (v or "").strip()
            for lang, v in langs.items()
            if lang != "zh_CN" and (v or "").strip()
        }
    return official


def sync_po_entries(official: dict, locales: tuple, i18n_dir: Path,
                    quiet: bool = False,
                    create_missing: bool = True) -> tuple[dict, list]:
    """把官方译名同步进 i18n/*/LC_MESSAGES/ok.po 并编译 .mo。

    - 精确匹配：msgid == 简中名 → 覆盖 msgstr 为官方译名（官方有值时）；
    - 缺失条目：create_missing=True 时新增；False 时跳过（只更新已有 msgid）；
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
                if not create_missing:
                    continue
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
        data = json5.loads(path.read_text(encoding="utf-8"))
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


# ---------- 多语言值匹配补全（不只按 zh_CN） ----------

# 全角字母数字 -> 半角（匹配归一用）
_FULLWIDTH = str.maketrans({
    c: chr(ord(c) - 0xFEE0)
    for c in "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９"
})


def normalize_value(value: str) -> str:
    """通用匹配归一：去空白、全角字母数字转半角、折叠空白、小写。

    用于按「任意语言值」匹配官方数据源/其他 lang 节点，避免因
    写法差异（全角/空格/大小写）漏配。补回的值始终用原始值。
    """
    s = (value or "").strip().translate(_FULLWIDTH)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def node_have_langs(node: dict, langs: tuple) -> dict:
    """节点已有语言值：{lang: 原始值}（string/pattern 任一）。"""
    have = {}
    for lang in langs:
        d = node.get(lang)
        if not isinstance(d, dict):
            continue
        for sub in ("string", "pattern"):
            v = d.get(sub)
            if isinstance(v, str) and v.strip():
                have[lang] = v.strip()
                break
    return have


def _node_style(node: dict) -> tuple[str, str]:
    """返回 (zh_CN 值, 节点风格 string/pattern)。

    zh_CN 缺失时返回 ("", 风格)，风格回退到节点任一已有语言，
    便于「只有 en_US 等残节点」也能按原风格补齐其余语言。
    """
    zh_node = node.get("zh_CN")
    if isinstance(zh_node, dict):
        for sub in ("string", "pattern"):
            v = zh_node.get(sub)
            if isinstance(v, str) and v.strip():
                return v.strip(), sub
    for lang in REPO_LANGS:
        d = node.get(lang)
        if isinstance(d, dict):
            for sub in ("string", "pattern"):
                v = d.get(sub)
                if isinstance(v, str) and v.strip():
                    return "", sub
    return "", "pattern"


def build_lang_value_index(entries: list) -> dict:
    """把词条列表转成 {(lang, 归一化值): [词条, ...]} 反向索引。

    词条形如 {lang: 原始值}。空值 / "?" 占位不建索引。
    """
    index = defaultdict(list)
    for entry in entries:
        for lang, val in entry.items():
            if not isinstance(val, str):
                continue
            val = val.strip()
            if not val or val == "?":
                continue
            index[(lang, normalize_value(val))].append(entry)
    return index


def fill_missing_from_index(data: dict, index: dict,
                            langs: tuple = REPO_LANGS) -> list:
    """按任意语言值匹配词条索引，补全节点缺失语言。

    - 用节点每个已有语言值（含 zh_CN）查 index，命中词条均为候选；
    - 候选对某缺失语言给出唯一值（且非 "?"）时才补；
    - 只补缺失，不覆盖已有；风格跟随 zh_CN（string/pattern）。
    返回变更列表 [(key, zh, lang, val)]。
    """
    touched = []
    for key, node in data.items():
        zh, style = _node_style(node)
        if not zh:
            continue
        have = node_have_langs(node, langs + ("zh_CN",))
        missing = [lang for lang in langs if lang not in have]
        if not missing:
            continue
        candidates = []
        seen = set()
        for lang, val in have.items():
            for entry in index.get((lang, normalize_value(val)), ()):
                eid = id(entry)
                if eid not in seen:
                    seen.add(eid)
                    candidates.append(entry)
        if not candidates:
            continue
        for lang in missing:
            vals = {
                e[lang].strip()
                for e in candidates
                if isinstance(e.get(lang), str)
                and e[lang].strip() and e[lang].strip() != "?"
            }
            if len(vals) == 1:
                val = vals.pop()
                node[lang] = {style: val}
                touched.append((key, zh, lang, val))
    return touched


def fill_missing_cross_files(data_map: dict, langs: tuple = REPO_LANGS
                             ) -> tuple[dict, list]:
    """lang/*.json 之间按任意语言值匹配互相补全缺失语言。

    - data_map: {Path: 已加载的 JSON dict}（内存态，函数内直接修改）；
    - 以全部 lang JSON 节点建立 (lang, 归一化值) -> 节点 索引；
    - 节点任一已有语言值（含 zh_CN / en_US 等）命中其他节点即为候选；
    - 缺 zh_CN 的残节点也参与：命中后连同 zh_CN 一起补齐；
    - 候选对某缺失语言给出唯一值时才补；冲突/无值跳过。
    返回 (每文件统计, 变更列表 [(fname, key, zh, lang, val)])。
    """
    all_langs = ("zh_CN",) + langs
    loaded = []  # (path, key, node, have, style, zh)
    for path, data in data_map.items():
        for key, node in data.items():
            zh, style = _node_style(node)
            have = node_have_langs(node, all_langs)
            loaded.append((path, key, node, have, style, zh))

    index = defaultdict(list)
    for i, (_, _, _, have, _, _) in enumerate(loaded):
        for lang, val in have.items():
            if val == "?":
                continue
            index[(lang, normalize_value(val))].append(i)

    stats = defaultdict(int)
    all_touched = []
    for i, (path, key, node, have, style, zh) in enumerate(loaded):
        missing = [lang for lang in all_langs if lang not in have]
        if not missing:
            continue
        candidates = []
        seen = set()
        for lang, val in have.items():
            for j in index.get((lang, normalize_value(val)), ()):
                if j == i or j in seen:
                    continue
                seen.add(j)
                candidates.append(loaded[j])
        if not candidates:
            continue
        for lang in missing:
            vals = {
                cand[3].get(lang)
                for cand in candidates
                if cand[3].get(lang) and cand[3].get(lang) != "?"
            }
            if len(vals) == 1:
                val = vals.pop()
                node[lang] = {style: val}
                stats[path.name] += 1
                all_touched.append((path.name, key, zh or val, lang, val))
    return dict(stats), all_touched


def main() -> int:
    print("Shared helpers module; not meant to be run directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
