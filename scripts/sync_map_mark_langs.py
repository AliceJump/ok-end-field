# -*- coding: utf-8 -*-

"""从官方地图公开接口同步地图标记（markTemplates）多语言名称。

数据源（均免登录、免签名）：zonai.skport.com 的 map/tree + map/mark/list，
六语全部通过 ``sk-language`` 头获取：
- 简中 ``zh_Hans`` / 繁中 ``zh_Hant``（官方代码，非 zhs/zht）
- 英 ``en`` / 日 ``ja`` / 韩 ``ko`` / 西 ``es_MX``（官方西语，与 es_ES 同源）

行为：
1. 抓取 map/tree 得到全部 mapId/levelId 组合；
2. 对每个组合拉取六语 mark/list，按 markTemplates 的 templateId（md5）合并；
3. 更新 assets/data/map_marks.json（zh_CN 简中名为键，zh_CN/zh_TW/en_US/ja_JP/ko_KR/es_ES 六语），
   结构与 characters.json 一致（{lang: {"string": ...}}）；
4. 同步官方译名进 i18n/*/LC_MESSAGES/ok.po 并编译 .mo；
5. 幂等：官方名称无变化时不产生任何写入。

注：mark/list 只覆盖地图标记类物品（储藏箱/资源点/传送点等），BOSS/怪物等
非标记物品不在模板内，不会同步（对应 item_names.json 其余名称保持不变）。
"""

import json
import sys
import time
from pathlib import Path
from urllib import parse, request

from _lang_sync_common import (
    build_official,
    print_json_result,
    print_po_result,
    sync_lang_jsons,
    sync_po_entries,
    sync_zh_cn_self_patch,
    write_json,
)

SKPORT = "https://zonai.skport.com"

# 官方语言代码 -> (host, sk-language)
LANG_TO_CODE = {
    "zh_CN": (SKPORT, "zh_Hans"),
    "zh_TW": (SKPORT, "zh_Hant"),
    "en_US": (SKPORT, "en"),
    "ja_JP": (SKPORT, "ja"),
    "ko_KR": (SKPORT, "ko"),
    "es_ES": (SKPORT, "es_MX"),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0 Safari/537.36",
    "Accept": "*/*",
    "Content-Type": "application/json",
    "platform": "3",
    "vname": "1.0.0",
    "timestamp": "0",
}

ROOT = Path(__file__).resolve().parent.parent
LANG_MARKS_JSON = ROOT / "assets" / "data" / "map_marks.json"
ITEM_NAMES_JSON = ROOT / "assets" / "items" / "map" / "item_names.json"
I18N_DIR = ROOT / "i18n"

# po locale -> lang JSON 节点（六语官方全覆盖）
PO_LANGS = ("zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")


def get_json(url: str, headers: dict):
    for attempt in range(3):
        try:
            req = request.Request(url, headers=headers)
            with request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def fetch_tree() -> dict:
    url = f"{SKPORT}/web/v1/game/endfield/map/tree"
    headers = {**HEADERS, "Origin": "https://game.skport.com", "Referer": "https://game.skport.com/"}
    return get_json(url, headers)["data"]


def fetch_templates(lang: str, map_id: str, level_id: str | None) -> dict[str, str]:
    """返回 {templateId: 名称}。"""
    host, code = LANG_TO_CODE[lang]
    params = {"mapId": map_id}
    if level_id:
        params["levelId"] = level_id
    url = f"{host}/web/v1/game/endfield/map/mark/list?{parse.urlencode(params)}"
    headers = {**HEADERS, "Origin": "https://game.skport.com", "Referer": "https://game.skport.com/"}
    if code:
        headers["sk-language"] = code
    data = get_json(url, headers).get("data", {})
    return {
        t["id"]: t["name"]
        for t in data.get("markTemplates", [])
        if t.get("id") and t.get("name")
    }


def sync_po(merged: dict[str, dict[str, str]]) -> tuple[dict, list]:
    """把官方译名同步进 i18n/*/LC_MESSAGES/ok.po。

    - 精确匹配：msgid == zh_CN 名 → 覆盖 msgstr 为官方译名（官方有值时）；
    - 缺失条目：新增（zh_CN 外全部官方译名；缺官方值的语言保持空待补）。
    返回 (每语言统计, 变更列表)。
    """
    # zh -> {en_US, ja_JP, ko_KR, es_ES}（重复 zh 名取首次，官方同名）
    official = build_official(merged, lambda _tid, langs: langs.get("zh_CN"))

    all_stats, all_touched = sync_po_entries(official, ("zh_TW", *PO_LANGS), I18N_DIR)
    zh_stats, zh_touched = sync_zh_cn_self_patch(
        official.keys(), I18N_DIR, update_existing=True
    )
    all_stats = {**zh_stats, **all_stats}
    all_touched = zh_touched + all_touched
    return all_stats, all_touched


def sync_other_lang_jsons(merged: dict[str, dict[str, str]]) -> tuple[dict, list]:
    """以 map_marks 官方表为准，覆盖其他 lang JSON 中相同中文的 string/pattern 节点。

    - 匹配键：节点 zh_CN 值（string 或 pattern）== 官方 zh_CN 名（相同中文）；
    - 内容相同即替换：string/pattern 类型不限，目标语言节点含哪个键就替换哪个值；
    - zh_CN 不覆盖；仅官方有值且与现有不同时写入。
    返回 (每文件统计, 变更列表)。
    """
    official = build_official(merged, lambda _tid, langs: langs.get("zh_CN"))
    return sync_lang_jsons(official, ROOT / "assets" / "lang", skip_files=("map_marks.json",))


def main():
    tree = fetch_tree()
    maps = tree.get("maps", [])
    print(f"maps: {len(maps)}")

    queries = []
    for game_map in maps:
        map_id = game_map["id"]
        queries.append((map_id, None))
        for level in game_map.get("levels", []):
            level_id = level.get("id")
            if level_id:
                queries.append((map_id, level_id))
    print(f"mark queries: {len(queries)}")

    # templateId -> {lang: name}
    merged: dict[str, dict[str, str]] = {}
    for lang in LANG_TO_CODE:
        print(f"fetching {lang}...")
        for map_id, level_id in queries:
            try:
                templates = fetch_templates(lang, map_id, level_id)
            except Exception as e:
                print(f"  FAIL {map_id}/{level_id}: {e}")
                continue
            for tid, name in templates.items():
                merged.setdefault(tid, {})[lang] = name

    # 读旧 JSON，仅写有变化的语言值（键：zh_CN 简中名，同名合并）
    old = {}
    if LANG_MARKS_JSON.exists():
        old = json.loads(LANG_MARKS_JSON.read_text(encoding="utf-8"))

    changed = 0
    added = 0
    for tid in sorted(merged):
        zh = (merged[tid].get("zh_CN") or "").strip()
        if not zh:
            continue
        node = old.setdefault(zh, {})
        for lang, name in merged[tid].items():
            if lang == "zh_CN":
                continue
            name = (name or "").strip()
            old_val = (node.get(lang) or {}).get("string")
            if old_val != name:
                node[lang] = {"string": name}
                if old_val is not None:
                    changed += 1
                else:
                    added += 1
        old_val = (node.get("zh_CN") or {}).get("string")
        if old_val != zh:
            node["zh_CN"] = {"string": zh}
            added += 1

    if changed or added:
        write_json(LANG_MARKS_JSON, old)
    print(f"templates: {len(merged)}  merged zh_CN keys: {len(old)}  added: {added}  changed: {changed}")

    # 覆盖统计：item_names.json 中哪些名称官方模板已覆盖
    if ITEM_NAMES_JSON.exists():
        item_names = json.loads(ITEM_NAMES_JSON.read_text(encoding="utf-8"))
        official_zh = set()
        for tid, langs in merged.items():
            zh = langs.get("zh_CN")
            if zh:
                official_zh.add(zh.strip())
        covered = [n for n in item_names if n.strip() in official_zh]
        missing = [n for n in item_names if n.strip() not in official_zh]
        print(f"item_names.json: {len(covered)} covered by official templates")
        if missing:
            print(f"  not covered ({len(missing)}, kept as-is):")
            for name in sorted(missing):
                print(f"    {name}")

    print()
    print("Syncing official mark names into ok.po...")
    po_stats, po_touched = sync_po(merged)
    print_po_result(po_stats, po_touched)

    print()
    print("Overwriting other lang JSON string nodes with official names...")
    json_stats, json_touched = sync_other_lang_jsons(merged)
    print_json_result(json_stats, json_touched)

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
