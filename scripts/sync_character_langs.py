# -*- coding: utf-8 -*-

"""从第三方 wiki（endfield.wiki.gg）同步干员（Operator）西语译名（es_ES）。

数据源：
- endfield.wiki.gg 的 ``{{Operator infobox}}`` 模板（经 MediaWiki API 抓取 wikitext）：
  ``name``（官方英文名）、``cnname``/``tcname``/``jpname``/``krname``/``spname``/``runame``
  （简中/繁中/日/韩/西/俄）、``filename``（内部 ID 如 chr_0028_wulfa）。

行为：
1. 分页抓取 wiki Category:Operators 全部干员 infobox；
2. 只回写 es_ES（西语）到 assets/lang/characters.json——wiki_items（skport
   官方 7 语）负责 en_US/ja_JP/ko_KR 等其余语言，zh_CN/zh_TW 以本地
   canonical 为准（仅打印差异）；本脚本是西语的唯一官方来源；
3. 已知 key 的新角色自动新增节点（assets/data/characters.json + assets/lang/characters.json），
   新节点先以 wiki.gg 全字段补全，后续由 wiki_items 覆盖其余语言；
   未知 key 的新角色打印提示（ZH_KEY_MAP 需人工补充）。
4. 幂等：西语已是最新时不产生任何变更。

注：assets/data/characters.json 的 ``en`` 是内部 ID（与 FeatureList 的 ``xxx_contact``
枚举绑定），不随本脚本改动。
"""

import json
import re
import sys
from pathlib import Path
from urllib import request, parse

from pypinyin import pinyin, Style

ROOT = Path(__file__).resolve().parent.parent
LANG_CHARACTERS_JSON = ROOT / "assets" / "lang" / "characters.json"
CANON_CHARACTERS_JSON = ROOT / "assets" / "data" / "characters.json"

WIKI_API = "https://endfield.wiki.gg/api.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}

# 官方语言字段名 -> characters.json 节点名
FIELD_TO_LANG = {
    "name": "en_US",
    "cnname": "zh_CN",
    "tcname": "zh_TW",
    "jpname": "ja_JP",
    "krname": "ko_KR",
    "spname": "es_ES",
}

# 已有节点仅同步 es_ES（其余语言由 wiki_items 官方 7 语覆盖）
SYNC_LANGS = ("es_ES",)

# 新角色节点补全用语言（保证节点完整，随后由 wiki_items 覆盖其余语言）
NEW_OP_LANGS = ("en_US", "ja_JP", "ko_KR", "es_ES")

# 中文 canonical 名 -> characters.json 内部 key（程序 ID）。
# 新角色默认由 pypinyin 自动生成拼音 key；此处仅保留已验证的
# 官方拼写与多音字例外（如 什/缪/茜 等，拼音库默认读音可能错误）。
ZH_KEY_MAP = {
    "庄方宜": "zhuang_fang_yi", "洛茜": "luo_qian", "汤汤": "tang_tang",
    "管理员": "guan_li_yuan", "黎风": "li_feng", "余烬": "yu_jin",
    "洁尔佩塔": "jie_er_pei_ta", "艾尔黛拉": "ai_er_dai_la", "骏卫": "jun_wei",
    "莱万汀": "lai_wan_ting", "伊冯": "yi_feng", "别礼": "bie_li",
    "陈千语": "chen_qian_yu", "昼雪": "zhou_xue", "赛希": "sai_xi",
    "狼卫": "lang_wei", "佩丽卡": "pei_li_ka", "弧光": "hu_guang",
    "阿列什": "a_lie_shi", "艾维文娜": "ai_wei_wen_na", "大潘": "da_pan",
    "埃特拉": "ai_te_la", "卡契尔": "ka_qi_er", "安塔尔": "an_ta_er",
    "萤石": "ying_shi", "秋栗": "qiu_li",
    "诀": "jue", "卡缪": "ka_miao", "弭弗": "mi_fu", "梨诺": "li_nuo",
}


def api_get(params: dict) -> dict:
    url = WIKI_API + "?" + parse.urlencode(params)
    req = request.Request(url, headers=HEADERS)
    with request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_operator_infoboxes() -> dict:
    """分页抓取 Category:Operators 全部干员 infobox。

    返回 {中文名: {en_US/zh_CN/zh_TW/ja_JP/ko_KR/es_ES: 值}}。
    """
    # 1) 分页收集成员标题
    titles = []
    gcmcontinue = ""
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:Operators",
            "cmlimit": "max",
            "format": "json",
        }
        if gcmcontinue:
            params["cmcontinue"] = gcmcontinue
        d = api_get(params)
        titles += [m["title"] for m in d["query"]["categorymembers"]]
        cont = d.get("continue", {})
        gcmcontinue = cont.get("cmcontinue", "")
        if not gcmcontinue:
            break

    # 2) 批量取 wikitext（每批 50）
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        d = api_get({
            "action": "query",
            "titles": "|".join(batch),
            "prop": "revisions",
            "rvslots": "main",
            "rvprop": "content",
            "format": "json",
        })
        for pid, pg in d["query"]["pages"].items():
            if "revisions" not in pg:
                continue
            txt = pg["revisions"][0]["slots"]["main"]["*"]
            m = re.search(r"\{\{Operator infobox\n(.*?)\n\}\}", txt, re.S)
            if not m:
                continue
            body = m.group(1)
            vals = {}
            for f, lang in FIELD_TO_LANG.items():
                mm = re.search(rf"\|{f}\s*=\s*([^\n]*)", body)
                vals[lang] = mm.group(1).strip() if mm else ""
            cn = vals["zh_CN"]
            if not cn:
                continue
            out[cn] = vals
    return out


def load_lang_characters() -> dict:
    return json.loads(LANG_CHARACTERS_JSON.read_text(encoding="utf-8"))


def load_canon_characters() -> dict:
    return json.loads(CANON_CHARACTERS_JSON.read_text(encoding="utf-8"))


def zh_to_key(zh: str) -> str:
    """中文名转程序 key：已验证映射优先，否则 pypinyin 自动生成拼音 key。"""
    key = ZH_KEY_MAP.get(zh)
    if key:
        return key
    return "_".join(x[0] for x in pinyin(zh, style=Style.NORMAL))


def sync_characters(wiki: dict) -> tuple:
    """同步 lang + canonical characters JSON；返回 (变更, 新增, 需人工处理的wiki独有)。"""
    lang = load_lang_characters()
    canon = load_canon_characters()
    key_by_zh = {v["zh_CN"]["string"]: k for k, v in lang.items()}

    changed, added, manual = [], [], []
    for zh, wiki_vals in sorted(wiki.items()):
        key = key_by_zh.get(zh)
        if key is None:
            key = zh_to_key(zh)
        if key is None:
            manual.append(zh)
            continue
        is_new = key not in lang
        node = lang.setdefault(key, {})
        if is_new:
            node.setdefault("zh_CN", {}).setdefault("string", wiki_vals.get("zh_CN", zh))
            node.setdefault("zh_TW", {}).setdefault("string", wiki_vals.get("zh_TW", zh))
            # 新节点以 wiki.gg 全字段补全（es/spname 缺失时用官方英文名回退，
            # 保证节点完整；其余语言随后由 wiki_items 官方 7 语覆盖）
            for lang_key in NEW_OP_LANGS:
                if lang_key not in node and wiki_vals.get(lang_key, "").strip():
                    node[lang_key] = {"string": wiki_vals[lang_key].strip()}
            if "es_ES" not in node and wiki_vals.get("en_US", "").strip():
                node["es_ES"] = {"string": wiki_vals["en_US"].strip()}
        for lang_key in SYNC_LANGS:
            new = wiki_vals.get(lang_key, "").strip()
            # 剔除字段截断残值（如 |spname 缺失时误匹配到 |runame = 行）
            if not new or "|" in new or "=" in new:
                continue
            old = node.get(lang_key, {}).get("string", "")
            if old != new:
                node[lang_key] = {"string": new}
                changed.append((key, zh, lang_key, old, new))
        if is_new:
            added.append(key)

    # canonical JSON 补新角色（zh + en + stars）
    if added:
        changed_canon = False
        for key in added:
            if key not in canon:
                canon[key] = {
                    "zh": lang[key]["zh_CN"]["string"],
                    "en": lang[key]["en_US"]["string"].lower().replace(" ", "_"),
                    "stars": 6,
                }
                changed_canon = True
        if changed_canon:
            CANON_CHARACTERS_JSON.write_text(
                json.dumps(canon, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    if changed or added:
        LANG_CHARACTERS_JSON.write_text(
            json.dumps(lang, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return changed, added, manual


def main():
    print("Fetching operators from endfield.wiki.gg...")
    wiki = get_operator_infoboxes()
    print(f"  {len(wiki)} operators fetched")

    changed, added, manual = sync_characters(wiki)
    print(f"  changed: {len(changed)}")
    for key, zh, lang, old, new in changed:
        print(f"    {zh} ({key}) {lang}: {old!r} -> {new!r}")
    if added:
        print(f"  added: {added}")
    if manual:
        print(f"  MANUAL (new operator, add ZH_KEY_MAP entry): {manual}")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
