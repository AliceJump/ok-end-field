# -*- coding: utf-8 -*-

"""从官方地图 API（四语）+ Atlos（西语）同步多语言译名到 world_map.json。

数据源：
- 简中：zonai.skland.com（域名决定语言）
- 英/日/韩：zonai.skport.com + ``sk-language: en|ja|ko``（短代码）
- 西语：github.com/Terra-Online/Atlos 仓库 locale/data/region/es-ES.json
  （游戏本地化提取，覆盖 145/168 site；缺 应龙关/北部禁区 site 级）

行为：
1. 拉取 tree + catalog 四语 + es region。
2. 按 world_map.json 的 zh_CN pattern 匹配官方中文名，覆盖
   en_US/ja_JP/ko_KR/es_ES（仅当官方有值且与现有值不同）。
3. 更新快照 tools/official_five_lang.json（供文档/人工校验）。
4. 输出变更统计；无变更时退出码 0（CI 据此跳过提交）。
"""

import json
import re
import sys
from pathlib import Path
from urllib import request

try:
    import polib
except ImportError:
    polib = None

SKLAND = "https://zonai.skland.com"
SKPORT = "https://zonai.skport.com"
ATLOS_REGION = (
    "https://raw.githubusercontent.com/Terra-Online/Atlos/HEAD/"
    "talos/src/locale/data/region/es-ES.json"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://game.skport.com/map/endfield",
    "Origin": "https://game.skport.com",
}

ROOT = Path(__file__).resolve().parent.parent
WORLD_MAP_JSON = ROOT / "assets" / "lang" / "world_map.json"
SNAPSHOT_JSON = ROOT / "tools" / "official_five_lang.json"
I18N_DIR = ROOT / "i18n"

# po locale -> 官方语言键
PO_LANGS = {
    "en_US": "en",
    "ja_JP": "ja",
    "ko_KR": "ko",
    "es_ES": "es",
}

# es 层级 -> zonai level 名（按 es sub name 匹配）
ES_SUB_TO_LV = {
    "Meseta de poder": "Power Plateau",
    "Veta de origen": "Origin Lodespring",
    "Parque científico de originio": "Originium Science Park",
    "Cantera Aburrey": "Aburrey Quarry",
    "Senda del valle": "Valley Pass",
    "La Base": "The Hub",
    "Ciudad de Wuling": "Wuling City",
    "Valle de Jingyu": "Jingyu Valley",
    "Empalizada Qingbo": "Qingbo Stockade",
    "Área de pruebas": "Test Area",
    "Piedra Marcadora": "Marker Stone",
    "Valle Ocultaespadas": "Sword Vault Dale",
}


def slug(text: str) -> str:
    text = text.replace("&", "").replace("Æ", "ae").replace("æ", "ae")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def get_json(url: str, headers: dict):
    req = request.Request(url, headers={**HEADERS, **headers})
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_json(path: Path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_tree_and_catalog(lang: str):
    """lang: zh(走 skland)/en/ja/ko(走 skport + sk-language)。"""
    if lang == "zh":
        host = SKLAND
        headers = {}
    else:
        host = SKPORT
        headers = {"sk-language": lang}
    tree = get_json(f"{host}/web/v1/game/endfield/map/tree", headers)
    catalog = get_json(f"{host}/web/v1/game/endfield/map/catalog", headers)
    return tree["data"], catalog["data"]


def build_official(langs):
    """langs: {'zh': data, 'en': data, ...} -> (name_map, catalog_map, levels)"""
    # name_map: zh -> {en, ja, ko}  (仅同构层级/site)
    # catalog_map: subType id -> {zh, en, ja, ko}
    # levels: 每 map/level 的 site 列表（用于 es 匹配与快照）
    zh_data = langs["zh"]

    name_map = {}
    catalog_map = {}
    levels = []

    for i, m_zh in enumerate(zh_data["maps"]):
        m = {l: langs[l]["maps"][i] for l in langs}
        name_map.setdefault(m_zh["name"], {})["en"] = m["en"]["name"]
        name_map[m_zh["name"]]["ja"] = m["ja"]["name"]
        name_map[m_zh["name"]]["ko"] = m["ko"]["name"]

        level_list = []
        for j, lv_zh in enumerate(m_zh.get("levels", [])):
            lv = {l: m[l]["levels"][j] for l in langs}
            name_map.setdefault(lv_zh["name"], {})["en"] = lv["en"]["name"]
            name_map[lv_zh["name"]]["ja"] = lv["ja"]["name"]
            name_map[lv_zh["name"]]["ko"] = lv["ko"]["name"]

            sites = []
            for k2, s_zh in enumerate(lv_zh.get("subLevels", [])):
                s = {}
                for l in langs:
                    subs = lv[l].get("subLevels", [])
                    s[l] = subs[k2]["name"] if k2 < len(subs) else "?"
                if s_zh["name"]:
                    name_map.setdefault(s_zh["name"], {})
                    for l in ("en", "ja", "ko"):
                        if s[l] != "?":
                            name_map[s_zh["name"]][l] = s[l]
                sites.append({
                    "id": s_zh["id"],
                    "zh": s_zh["name"],
                    "en": s["en"], "ja": s["ja"], "ko": s["ko"],
                })
            level_list.append({
                "id": lv_zh["id"],
                "name": {l: lv[l]["name"] for l in langs},
                "sites": sites,
            })
        levels.append({"id": m_zh["id"], "name": {l: m[l]["name"] for l in langs}, "levels": level_list})

    # catalog: 按 mainType/subType 顺序对齐（四语 subType id 一致）
    zh_cats = zh_data["mainTypes"]
    for i, mt_zh in enumerate(zh_cats):
        subs = {l: langs[l]["mainTypes"][i].get("subTypes", []) for l in langs}
        for j, st_zh in enumerate(mt_zh.get("subTypes", [])):
            st = {l: subs[l][j]["name"] if j < len(subs[l]) else "?" for l in langs}
            entry = {"zh": st_zh["name"]}
            for l in ("en", "ja", "ko"):
                if st[l] != "?":
                    entry[l] = st[l]
            catalog_map[st_zh["id"]] = entry

    return name_map, catalog_map, levels


def fetch_es_sites():
    """返回 (es_site_by_slug, es_sub_by_lv, es_main_by_en)。

    es_main_by_en 通过 site slug 集合的交集，把 zonai 地图（en 名）与 es
    地图（es 名）配对（如 Valley IV -> Valle IV、OMV Dijiang -> NMO Dijiang）。
    """
    data = get_json(ATLOS_REGION, {})
    es_site_by_slug = {}
    es_sub_by_lv = {}
    es_main_by_en = {}
    es_main_by_key = {}
    es_sites_by_key = {}
    for main_key, main_v in data.items():
        es_main_by_key[main_key] = main_v["main"]
        sites = set()
        subs = main_v.get("sub", {})
        if not isinstance(subs, dict):
            continue
        # DJ 型结构: {"name": "NMO Dijiang", "site": {...}}
        # 常规型结构: {"PL": {"name": ..., "site": {...}}, ...}
        for sub_name, sub_v in subs.items():
            if sub_name == "name":
                continue
            if sub_name == "site" and isinstance(sub_v, dict):
                site_dict = sub_v
            elif isinstance(sub_v, dict):
                lv = ES_SUB_TO_LV.get(sub_v.get("name"))
                if lv:
                    es_sub_by_lv[slug(lv)] = sub_v.get("name", "?")
                site_dict = sub_v.get("site", {})
            else:
                continue
            for site_k, site_v in site_dict.items():
                es_site_by_slug[slug(site_k)] = site_v
                sites.add(slug(site_k))
        es_sites_by_key[main_key] = sites
    return es_site_by_slug, es_sub_by_lv, (es_main_by_key, es_sites_by_key)


def match_es_main(levels, es_main_by_key, es_sites_by_key):
    """zonai 地图 en 名 -> es 地图名（site 集合交集最大者）。"""
    es_main_by_en = {}
    for mp in levels:
        en_name = mp["name"]["en"]
        my_sites = {
            slug(s["en"])
            for lv in mp["levels"]
            for s in lv["sites"]
            if s["en"] != "?"
        }
        best_key, best_n = None, 0
        for main_key, es_sites in es_sites_by_key.items():
            n = len(my_sites & es_sites)
            if n > best_n:
                best_key, best_n = main_key, n
        if best_key:
            es_main_by_en[slug(en_name)] = es_main_by_key[best_key]
    return es_main_by_en


def sync_world_map(name_map, es_site_by_slug, es_sub_by_lv, es_main_by_en):
    data = json.loads(WORLD_MAP_JSON.read_text(encoding="utf-8"))
    stats = {l: 0 for l in ("en", "ja", "ko", "es")}
    touched = []
    for key, node in data.items():
        zh = (node.get("zh_CN") or {}).get("pattern")
        if not zh:
            continue
        official = name_map.get(zh)
        if not official:
            continue
        for lang, out_key in (("en", "en_US"), ("ja", "ja_JP"), ("ko", "ko_KR")):
            val = official.get(lang)
            if not val:
                continue
            target = node.setdefault(out_key, {})
            old = target.get("pattern")
            if old != val:
                target["pattern"] = val
                stats[lang] += 1
                touched.append((key, out_key, old, val))
        # es: 站点级按 en slug；层级级按 en name slug；地图级按 es main 名 slug
        en = official.get("en")
        if en:
            es_val = es_site_by_slug.get(slug(en))
            if not es_val:
                es_val = es_sub_by_lv.get(slug(en))
            if not es_val:
                es_val = es_main_by_en.get(slug(en))
            if es_val:
                target = node.setdefault("es_ES", {})
                old = target.get("pattern")
                if old != es_val:
                    target["pattern"] = es_val
                    stats["es"] += 1
                    touched.append((key, "es_ES", old, es_val))
    if touched:
        write_json(WORLD_MAP_JSON, data)
    return stats, touched


def sync_po(name_map, catalog_map, es_main_by_en, es_sub_by_lv, es_site_by_slug):
    """把官方译名同步进 i18n/*/LC_MESSAGES/ok.po。

    - 精确匹配：msgid（去尾换行）== 官方 zh 名 → 整条替换 msgstr。
    - 内嵌替换：msgid 含 zh 名（子串）且 msgstr 含该名的旧译名 → 替换为新译名
      （如「通向武陵城送货点」中的 Test Zone -> Test Area）。
    返回 (每语言变更数, 变更列表)。
    """
    if polib is None:
        print("  [po] polib not installed; skipping ok.po sync")
        return {}, []

    # 合并官方表：zh -> {en, ja, ko, es}
    official = {}
    for zh, vals in name_map.items():
        official.setdefault(zh, {}).update(vals)
    for zh, vals in catalog_map.items():
        official.setdefault(zh, {}).update(vals)
    # es 层级/地图名并入（es main/sub 的 zh 对应在 name_map 已含，但 es 值补充）
    for zh, vals in list(official.items()):
        if "es" in vals and vals["es"] == "?":
            vals.pop("es")
        if zh not in name_map:
            continue
        en = vals.get("en")
        if not en:
            continue
        es_val = es_site_by_slug.get(slug(en))
        if not es_val:
            es_val = es_sub_by_lv.get(slug(en))
        if not es_val:
            es_val = es_main_by_en.get(slug(en))
        if es_val:
            vals["es"] = es_val

    all_stats = {}
    all_touched = []

    for loc, lang_key in PO_LANGS.items():
        po_path = I18N_DIR / loc / "LC_MESSAGES" / "ok.po"
        if not po_path.exists():
            print(f"  [po] missing {po_path}")
            continue
        po = polib.pofile(str(po_path))
        stats = 0
        touched = []

        # 1) 采集旧译名（精确匹配条目的当前 msgstr）
        old_trans = {}
        for entry in po:
            mid = entry.msgid.rstrip("\n")
            if mid in official:
                old_trans[mid] = entry.msgstr

        # 2) 精确匹配替换
        for entry in po:
            mid = entry.msgid.rstrip("\n")
            if mid not in official:
                continue
            new = official[mid].get(lang_key)
            if not new:
                continue
            if entry.msgstr != new:
                touched.append((mid, entry.msgstr, new))
                entry.msgstr = new
                stats += 1

        # 3) 内嵌替换（按 zh 名长度降序，避免子串冲突）
        for zh in sorted(official, key=len, reverse=True):
            if len(zh) < 2:
                continue
            new = official[zh].get(lang_key)
            if not new:
                continue
            old = old_trans.get(zh, "").rstrip("\n")
            if not old or old == new:
                continue
            for entry in po:
                if entry.msgid.rstrip("\n") == zh:
                    continue
                if zh in entry.msgid and old in entry.msgstr:
                    entry.msgstr = entry.msgstr.replace(old, new)
                    stats += 1
                    touched.append((entry.msgid, old, new))

        if stats:
            po.save(str(po_path))
            po.save_as_mofile(str(po_path).replace(".po", ".mo"))
            print(f"  [po] {loc}: {stats} entries updated")
        else:
            print(f"  [po] {loc}: no changes")
        all_stats[loc] = stats
        all_touched.extend(touched)

    return all_stats, all_touched


def main():
    print("Fetching official API (4 langs)...")
    raw = {}
    for lang in ("zh", "en", "ja", "ko"):
        tree, catalog = fetch_tree_and_catalog(lang)
        raw[lang] = {"maps": tree["maps"], "mainTypes": catalog["mainTypes"]}
        print(f"  {lang}: maps={len(tree['maps'])} catalog={len(catalog['mainTypes'])}")

    name_map, catalog_map, levels = build_official(raw)
    print("  name_map:", len(name_map), "catalog:", len(catalog_map))

    print("Fetching Atlos es-ES region...")
    es_site_by_slug, es_sub_by_lv, (es_main_by_key, es_sites_by_key) = fetch_es_sites()
    es_main_by_en = match_es_main(levels, es_main_by_key, es_sites_by_key)
    print(f"  es sites={len(es_site_by_slug)} subs={len(es_sub_by_lv)} mains={es_main_by_en}")

    stats, touched = sync_world_map(name_map, es_site_by_slug, es_sub_by_lv, es_main_by_en)
    print()
    print("world_map.json changes:")
    for lang, n in stats.items():
        print(f"  {lang:>4}: {n}")
    for key, out_key, old, val in touched:
        print(f"  {key} {out_key}: {old!r} -> {val!r}")

    # 快照（含 es site/level/map 级）
    for mp in levels:
        mp["name"]["es"] = es_main_by_en.get(slug(mp["name"]["en"]), "?")
        for lv in mp["levels"]:
            lv["name"]["es"] = es_sub_by_lv.get(slug(lv["name"]["en"]), "?")
            for site in lv["sites"]:
                site["es"] = es_site_by_slug.get(slug(site["en"]), "?")
    snapshot = {
        "source": "official zonai API (zh/en/ja/ko) + Atlos region (es)",
        "updated": __import__("datetime").date.today().isoformat(),
        "maps": levels,
        "catalog": list(catalog_map.values()),
    }
    write_json(SNAPSHOT_JSON, snapshot)
    print()
    print(f"Snapshot saved: {SNAPSHOT_JSON}")

    if not touched:
        print("No world_map.json changes.")

    print()
    print("Syncing official names into ok.po...")
    po_stats, po_touched = sync_po(name_map, catalog_map, es_main_by_en, es_sub_by_lv, es_site_by_slug)
    for loc, n in po_stats.items():
        print(f"  {loc}: {n} entries updated")
    for mid, old, val in po_touched:
        print(f"  {mid!r}: {old!r} -> {val!r}")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
