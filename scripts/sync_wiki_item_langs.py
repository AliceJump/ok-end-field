# -*- coding: utf-8 -*-

"""把官方 Wiki catalog 抓取结果归类为多语言物品表。

数据源：
- ``scripts/capture_wiki_catalog.py`` 抓取的 skport 原始 JSON（7 语言 × 13 类型），
  位于 ``tools/wiki_catalog/by_lang/<stamp>_<lang>/m<main>_s<sub>.json``；
- ``scripts/capture_wiki_zhcn.py`` 抓取的 skland 简中 JSON（15 类型），
  位于 ``tools/wiki_catalog/zh_cn/<stamp>/m1_s<sub>.json``。
每个 item 含 itemId（同库唯一）、name、lang、brief.associate.id（跨域稳定 hash）。

行为：
1. 读取最新一批 by_lang 原始 JSON，按 itemId 合并 7 语言名称；
2. 读取最新一批 zh_cn 简中 JSON，按 associate.id（干员/武器类）或
   zh_TW 繁转简名称匹配，补 zh_CN 节点（skport 无简中，必须靠 skland）；
3. 写 assets/data/wiki_items.json（{zh_CN名: {lang: {"string": ...}}}），
   键以官方简中名为准（无简中时用 zh_TW 繁转简兜底），
   语言键映射：zh-Hant→zh_TW, en→en_US, ja→ja_JP, ko→ko_KR,
   ru→ru_RU, th→th_TH, id→id_ID；
4. 同步官方译名进 i18n/*/LC_MESSAGES/ok.po 并编译 .mo（有官方值的语言）；
5. 覆盖 assets/lang/*.json 中相同中文的 string/pattern 节点
   （map_marks.json 优先：wiki_items 跳过其已有简中名）；
6. 幂等：名称无变化时不产生写入。

注：skland 独有类目（武器基质/任务/活动/系统蓝图/装扮/能量淤积点/
蚀刻章/档案库，约 700 条）在 skport 无对应条目，不写入 wiki_items.json；
es_MX 未在抓取列表中时 es_ES 节点不写。
"""

import json
import re
import sys
from pathlib import Path

try:
    from zhconv import convert as zhconvert
except ImportError:
    zhconvert = None

from _lang_sync_common import (
    build_official,
    print_json_result,
    print_po_result,
    sync_lang_jsons,
    sync_po_entries,
    sync_zh_cn_self_patch,
    write_json,
)

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "tools" / "wiki_catalog" / "by_lang"
ZHCN_DIR = ROOT / "tools" / "wiki_catalog" / "zh_cn"
OUT_JSON = ROOT / "assets" / "data" / "wiki_items.json"
I18N_DIR = ROOT / "i18n"

# 抓取语言代码 -> 项目 lang JSON 语言键
LANG_MAP = {
    "zh-Hant": "zh_TW",
    "en": "en_US",
    "ja": "ja_JP",
    "ko": "ko_KR",
    "ru": "ru_RU",
    "th": "th_TH",
    "id": "id_ID",
}

# po locale（项目 6 语，仅同步有官方值的）
PO_LOCALES = ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES", "ru_RU", "th_TH", "id_ID")

# 各语言抓取目录命名前缀（find_latest 用）
STAMP_PATTERN = "[0-9]{8}_[0-9]{6}"


def find_latest_batches() -> dict[str, Path]:
    """返回 {lang_code: 最新批次目录}。"""
    if not RAW_DIR.exists():
        print(f"  [raw] missing {RAW_DIR}")
        return {}
    batches: dict[str, list[Path]] = {}
    for d in sorted(RAW_DIR.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        stamp, code = parts
        if len(stamp) != 15 or not stamp[:8].isdigit():
            continue
        if code in LANG_MAP:
            batches.setdefault(code, []).append(d)
    return {code: sorted(dir_list)[-1] for code, dir_list in batches.items()}


def find_latest_zhcn_batch() -> Path | None:
    """返回最新 zh_cn 简中批次目录（无则 None）。"""
    if not ZHCN_DIR.exists():
        return None
    dirs = []
    for d in sorted(ZHCN_DIR.iterdir()):
        if not d.is_dir():
            continue
        if len(d.name) != 15 or not d.name[:8].isdigit():
            continue
        dirs.append(d)
    return dirs[-1] if dirs else None


def load_items(batch: Path) -> dict[str, tuple[str, str | None]]:
    """读取一个语言批次目录，返回 {itemId: (name, associate_id)}。"""
    out = {}
    for f in sorted(batch.glob("m*_s*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in d.get("data", {}).get("catalog", []):
            for s in c.get("typeSub", []):
                for it in s.get("items", []):
                    iid = it.get("itemId")
                    name = it.get("name")
                    if iid and name:
                        a = (it.get("brief") or {}).get("associate") or {}
                        out[str(iid)] = (name, a.get("id") if a else None)
    return out


def normalize_name(name: str) -> str:
    """简中名规范化：去空格、统一括号/引号为半角。"""
    s = name.strip()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("，", ",")
    s = s.replace("“", '"').replace("”", '"').replace("『", '"').replace("』", '"')
    return s


def merge_zh_cn(merged: dict[str, dict[str, str]], zhcn_batch: Path) -> dict:
    """把 skland 简中条目合并进 merged：返回 {iid: zh_CN_name} 新增映射。

    - associate.id 相同优先（跨域稳定，干员/武器类）；
    - 否则用 zh_TW 名繁转简 + 规范化后与简中名精确匹配；
    - 匹配目标只限 merged 已有条目（skport 无的类目不新增）。
    """
    zhcn_items = load_items(zhcn_batch)
    print(f"  zh_CN: {len(zhcn_items)} items <- {zhcn_batch.name}")

    if not zhcn_items:
        return {}

    # skport 侧索引
    aid_to_iid = {}
    name_to_iids: dict[str, list[str]] = {}
    for iid, langs in merged.items():
        tw = (langs.get("zh_TW") or "").strip()
        if tw:
            if zhconvert:
                key = normalize_name(zhconvert(tw, "zh-cn"))
            else:
                key = normalize_name(tw)
            name_to_iids.setdefault(key, []).append(iid)

    # 用 by_lang 批次的 associate 信息建立 aid 索引
    batches = find_latest_batches()
    for code, batch in batches.items():
        if code != "zh-Hant":
            continue
        items = load_items(batch)
        for iid, (name, aid) in items.items():
            if aid:
                aid_to_iid.setdefault(aid, iid)

    matched = {}
    unmatched = []
    for sl_iid, (name, aid) in zhcn_items.items():
        hit = None
        if aid and aid in aid_to_iid:
            hit = aid_to_iid[aid]
        else:
            key = normalize_name(name)
            cands = name_to_iids.get(key, [])
            if len(cands) == 1:
                hit = cands[0]
            elif len(cands) > 1:
                hit = cands[0]
        if hit:
            matched[hit] = name
        else:
            unmatched.append(name)

    print(f"  zh_CN matched: {len(matched)}  unmatched: {len(unmatched)}")
    if unmatched:
        print(f"  zh_CN unmatched samples ({min(10, len(unmatched))}):")
        for n in sorted(unmatched)[:10]:
            print(f"    {n}")
    return matched


def _zh_of(iid: str, langs: dict[str, str], zhcn: dict[str, str]) -> str:
    """条目简中名：skland 官方简中优先，否则 zh_TW 繁转简兜底。"""
    zh = (zhcn.get(iid) or "").strip()
    if zh:
        return zh
    tw = (langs.get("zh_TW") or "").strip()
    return zhconvert(tw, "zh-cn") if (zhconvert and tw) else tw


def sync_po(merged: dict[str, dict[str, str]], zhcn: dict[str, str]) -> tuple[dict, list]:
    """把官方译名同步进 i18n/*/LC_MESSAGES/ok.po（msgid == 简中名）。

    msgid 统一用简中名：优先 skland 官方简中（zhcn），否则 zh_TW 名繁转简；
    各语言 msgstr 用对应官方译名。zh_CN 用官方简中自补（msgid == msgstr）。
    """
    # map_marks 优先：跳过其已有的简中名（由 sync_map_mark_langs.py 先行同步）
    marks_json = ROOT / "assets" / "data" / "map_marks.json"
    marks_names = ()
    if marks_json.exists():
        marks_names = tuple(json.loads(marks_json.read_text(encoding="utf-8")))

    official = build_official(merged, lambda iid, langs: _zh_of(iid, langs, zhcn))
    for zh in marks_names:
        official.pop(zh, None)
        zhcn.pop(zh, None)
    all_stats, all_touched = sync_po_entries(official, PO_LOCALES, I18N_DIR, quiet=True)
    zh_stats, zh_touched = sync_zh_cn_self_patch(zhcn.values(), I18N_DIR, quiet=True)
    all_stats = {**all_stats, **zh_stats}
    all_touched.extend(zh_touched)
    return all_stats, all_touched


def sync_other_lang_jsons(merged: dict[str, dict[str, str]], zhcn: dict[str, str]) -> tuple[dict, list]:
    """以 wiki_items 官方表为准，覆盖其他 lang JSON 中相同中文的 string/pattern 节点。

    - 优先级低于 map_marks.json：官方表含 map_marks 已有的简中名时跳过，
      map_marks 先跑且更权威（地图点位名），wiki_items 只补其余物品名；
    - 匹配键：节点 zh_CN 值（string 或 pattern）== 官方简中名（相同中文）；
    - string/pattern 类型不限，目标语言节点含哪个键就替换哪个值；
    - zh_CN 不覆盖；仅官方有值且与现有不同时写入。
    返回 (每文件统计, 变更列表)。
    """
    official = build_official(merged, lambda iid, langs: _zh_of(iid, langs, zhcn))

    # map_marks 优先：跳过其已有的简中名（由 sync_map_mark_langs.py 先行覆盖）
    marks_json = ROOT / "assets" / "data" / "map_marks.json"
    if marks_json.exists():
        marks = json.loads(marks_json.read_text(encoding="utf-8"))
        for zh in marks:
            official.pop(zh, None)

    return sync_lang_jsons(official, ROOT / "assets" / "lang",
                           skip_files=("map_marks.json", "wiki_items.json"))


def main():
    batches = find_latest_batches()
    if not batches:
        print("No wiki catalog raw data found; wiki_items.json untouched (capture locally, e.g. run capture_wiki_catalog.py).")
        return 0
    print(f"language batches: {sorted(batches)}")

    merged: dict[str, dict[str, str]] = {}
    for code, batch in sorted(batches.items()):
        items = load_items(batch)
        lang_key = LANG_MAP[code]
        for iid, (name, aid) in items.items():
            merged.setdefault(iid, {})[lang_key] = name
        print(f"  {code}: {len(items)} items <- {batch.name}")

    print(f"merged items: {len(merged)}")

    # 简中合并（skland -> zh_CN 节点）
    zhcn_batch = find_latest_zhcn_batch()
    zhcn_map = {}
    if zhcn_batch:
        zhcn_map = merge_zh_cn(merged, zhcn_batch)
    else:
        print("  [zh_cn] no raw data found; run capture_wiki_zhcn.py first")

    old = {}
    if OUT_JSON.exists():
        old = json.loads(OUT_JSON.read_text(encoding="utf-8"))

    changed = added = 0
    for iid in sorted(merged):
        # 键：官方简中名优先，否则 zh_TW 繁转简兜底（保证无 zh_CN 条目不丢）
        zh_cn = (zhcn_map.get(iid) or "").strip()
        if not zh_cn:
            tw = (merged[iid].get("zh_TW") or "").strip()
            zh_cn = zhconvert(tw, "zh-cn") if (zhconvert and tw) else tw
        if not zh_cn:
            continue
        node = old.setdefault(zh_cn, {})
        for lang, name in merged[iid].items():
            name = (name or "").strip()
            old_val = (node.get(lang) or {}).get("string")
            if old_val != name:
                node[lang] = {"string": name}
                if old_val is None:
                    added += 1
                else:
                    changed += 1
        if iid in zhcn_map:
            name = (zhcn_map[iid] or "").strip()
            old_val = (node.get("zh_CN") or {}).get("string")
            if old_val != name:
                node["zh_CN"] = {"string": name}
                if old_val is None:
                    added += 1
                else:
                    changed += 1

    if changed or added:
        write_json(OUT_JSON, old)
    print(f"wiki_items.json: {len(old)} zh_CN keys  added: {added}  changed: {changed}")

    print()
    print("Syncing official names into ok.po...")
    po_stats, po_touched = sync_po(merged, zhcn_map)
    print_po_result(po_stats, po_touched)

    print()
    print("Overwriting other lang JSON string nodes with official names...")
    json_stats, json_touched = sync_other_lang_jsons(merged, zhcn_map)
    print_json_result(json_stats, json_touched)

    return 0


if __name__ == "__main__":
    sys.exit(main())
