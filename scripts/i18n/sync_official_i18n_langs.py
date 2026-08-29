"""用官方解包 i18n_texts（14 语言）补齐 assets/lang/*.json 与 i18n/*/LC_MESSAGES/ok.po。

逻辑（按用户要求）：
- 加载 assets/data/i18n_texts/{CN,TC,EN,JP,KR,MX,...}.json 官方语言表；
- 建立「官方语言 -> {文本: [key, ...]}」反查索引（含全角罗马数字/括号归一化）；
- 对 assets/lang/*.json 每个节点：用节点**任意已有语言值**（string 或纯文本 pattern）
  反查官方 key（跳过含正则元字符的 pattern）；
- 多个候选 key 按「与已有语言值匹配票数」投票选最可信 key；
- 命中后把该 key 的**全部项目语言**官方值拷贝补齐缺失语言键（**不覆盖已有**）；
- 同步官方译名进 i18n/*/LC_MESSAGES/ok.po 并编译 .mo（复用 _lang_sync_common）；
- 幂等：无变化不写入；支持 --dry 预览。
"""

import argparse
import json
import re
import sys
from pathlib import Path

import json5

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lang_sync_common import (
    norm_zh_name,
    sync_po_entries,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]
LANG_DIR = ROOT / "assets" / "lang"
I18N_DIR = ROOT / "assets" / "data" / "i18n_texts"
PO_DIR = ROOT / "i18n"

# 官方语言文件 -> 项目 lang JSON 语言键
OFFICIAL_TO_REPO = {
    "CN": "zh_CN",
    "TC": "zh_TW",
    "EN": "en_US",
    "JP": "ja_JP",
    "KR": "ko_KR",
    "MX": "es_ES",
    "BR": "pt_BR",
    "DE": "de_DE",
    "FR": "fr_FR",
    "ID": "id_ID",
    "IT": "it_IT",
    "RU": "ru_RU",
    "TH": "th_TH",
    "VN": "vi_VN",
}
# 解包全部 14 种语言 -> lang JSON 节点全量写入
REPO_LANGS = (
    "zh_CN",
    "zh_TW",
    "en_US",
    "ja_JP",
    "ko_KR",
    "es_ES",
    "pt_BR",
    "de_DE",
    "fr_FR",
    "id_ID",
    "it_IT",
    "ru_RU",
    "th_TH",
    "vi_VN",
)
# ok.po / .mo 仅同步项目 UI 支持的 6 种 locale（i18n/ 目录只有这些）
PO_LANGS = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")

# 强正则元字符：含这些的 pattern 视为真正的正则，字面/完整匹配失败后不参与反查
_STRONG_REGEX_META = re.compile(r"[|^$*+?\\]")
# 弱正则元字符：仅 [ ] . ( ) { } 的 pattern 视为“普通 pattern”，
# 可能只是字面文本（如 "Canned Citrome [B]"），允许做完整词条匹配
_WEAK_REGEX_META = re.compile(r"[\[\]().{}]")


def _norm(text: str) -> str:
    """官方名键名差异归一（与 norm_zh_name 一致，另做全角转半角字母数字）。"""
    t = norm_zh_name(text)
    # 全角英数 -> 半角（官方表多为半角，OCR/lang 可能全角）
    t = t.translate(
        str.maketrans(
            {
                "０": "0",
                "１": "1",
                "２": "2",
                "３": "3",
                "４": "4",
                "５": "5",
                "６": "6",
                "７": "7",
                "８": "8",
                "９": "9",
                "Ａ": "A",
                "Ｂ": "B",
                "Ｃ": "C",
                "Ｄ": "D",
                "Ｅ": "E",
                "Ｆ": "F",
                "Ｇ": "G",
                "Ｈ": "H",
                "Ｉ": "I",
                "Ｊ": "J",
                "Ｋ": "K",
                "Ｌ": "L",
                "Ｍ": "M",
                "Ｎ": "N",
                "Ｏ": "O",
                "Ｐ": "P",
                "Ｑ": "Q",
                "Ｒ": "R",
                "Ｓ": "S",
                "Ｔ": "T",
                "Ｕ": "U",
                "Ｖ": "V",
                "Ｗ": "W",
                "Ｘ": "X",
                "Ｙ": "Y",
                "Ｚ": "Z",
                "ａ": "a",
                "ｂ": "b",
                "ｃ": "c",
                "ｄ": "d",
                "ｅ": "e",
                "ｆ": "f",
                "ｇ": "g",
                "ｈ": "h",
                "ｉ": "i",
                "ｊ": "j",
                "ｋ": "k",
                "ｌ": "l",
                "ｍ": "m",
                "ｎ": "n",
                "ｏ": "o",
                "ｐ": "p",
                "ｑ": "q",
                "ｒ": "r",
                "ｓ": "s",
                "ｔ": "t",
                "ｕ": "u",
                "ｖ": "v",
                "ｗ": "w",
                "ｘ": "x",
                "ｙ": "y",
                "ｚ": "z",
            }
        )
    )
    return t.strip()


def load_official() -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, str]]]:
    """加载官方语言表，返回 {项目语言键: {文本: [key,...]}}（反查索引）。"""
    index = {}
    raw_texts = {}
    for official, repo in OFFICIAL_TO_REPO.items():
        path = I18N_DIR / f"{official}.json"
        if not path.exists():
            continue
        table = json.loads(path.read_text(encoding="utf-8"))
        raw_texts[repo] = table  # key -> 官方文本
        rev = {}
        for key, text in table.items():
            text = (text or "").strip()
            if not text:
                continue
            n = _norm(text)
            rev.setdefault(n, []).append(key)
            rev.setdefault(text, []).append(key)
        index[repo] = rev
    return index, raw_texts


def split_regex_pattern(pattern: str) -> list:
    """把强正则 pattern 拆成可做字面反查的多个片段。

    处理：
    - 去掉首尾 ^ / $ 锚点（如 "Task$" -> "Task"）；
    - 按 | 拆分支（如 "激发|放弃" -> ["激发", "放弃"]）；
    - 每个分支去掉捕获/非捕获组括号与 `\\d+` 等转义（如 "(天|小时)" -> ["天", "小时"]）；
    - 返回非空片段列表；拆不出字面片段的返回空列表。
    """
    p = pattern.strip()
    p = p.removeprefix("^")
    p = p.removesuffix("$")
    if not p:
        return []
    out = []
    for branch in p.split("|"):
        b = branch.strip()
        if not b:
            continue
        # 去掉 (?i) (?=...) (?<=...) 等内联标记/前后视
        b = re.sub(r"\(\?[^)]*\)", "", b)
        # 去掉 \d \w \s 等转义类及其量词（\d+、\d* 等）
        b = re.sub(r"\\[dDwWsS](?:\?|\*|\+)?", "", b)
        # 去掉其它转义（\. \( 等）保留字面
        b = re.sub(r"\\(.)", r"\1", b)
        # 去掉剩余括号
        b = b.replace("(", "").replace(")", "")
        # 去掉裸量词（+ * ?），如 "Навыки+" -> "Навыки"
        b = re.sub(r"[+*?]", "", b)
        b = b.strip()
        if b:
            out.append(b)
    return out


def node_values(node: dict) -> list:
    """收集节点已有的 (lang, text, is_pattern) 锚点。

    - 仅使用 zh_CN 语言锚点做 key 匹配（纯 zh 匹配），其它语言不参与投票；
    - string 一律作为锚点；
    - pattern 只要不含强正则元字符（| ^ $ * + ? \\）就作为锚点：
      含弱元字符（[ ] . ( ) { }）的“普通 pattern”可能是字面文本
      （如 "Canned Citrome [B]"），先做字面精确匹配，再做完整词条匹配；
    - 含强正则元字符的 pattern（如 "excitation|give up"、"Task$"）
      拆成多个字面片段（split_regex_pattern）分别作为锚点，
      各片段反查后由 pick_keys / official_vals_multi 合并。
    """
    anchors = []
    for lang, sub in node.items():
        if lang != "zh_CN" or not isinstance(sub, dict):
            continue
        for kind in ("string", "pattern"):
            v = sub.get(kind)
            if not isinstance(v, str) or not v.strip():
                continue
            v = v.strip()
            if kind == "pattern" and _STRONG_REGEX_META.search(v):
                for seg in split_regex_pattern(v):
                    if seg:
                        anchors.append((lang, seg, False))
                continue
            anchors.append((lang, v, kind == "pattern"))
    return anchors


def pick_keys(anchors, index) -> list:
    """用多个语言锚点投票选官方 key，返回 [(key, 票数), ...] 按票数降序。

    匹配优先级（每语言）：
    1. 字面精确（text 或归一化后 == 官方词条）；
    2. 若锚点为“普通 pattern”（含弱元字符如 [B] 或纯字面），
       用 re.fullmatch 匹配官方表中与该语言等长的完整词条
       （完整匹配到任意完整字符也算匹配）。
    """
    candidates = {}
    for lang, text, is_pattern in anchors:
        rev = index.get(lang)
        if not rev:
            continue
        keys = rev.get(text) or rev.get(_norm(text)) or []
        if not keys and is_pattern:
            # 普通 pattern：允许正则完整匹配官方词条
            try:
                rx = re.compile(text)
            except Exception:
                rx = None
            if rx is not None:
                # 遍历该语言官方词条，fullmatch 命中即算
                for official_text, ks in rev.items():
                    if official_text and rx.fullmatch(official_text):
                        keys = ks
                        break
        for k in keys:
            candidates.setdefault(k, 0)
            candidates[k] += 1
    if not candidates:
        return []
    # 票数降序；同票按首见顺序
    return sorted(candidates.items(), key=lambda kv: (-kv[1], list(candidates).index(kv[0])))


_RICHTEXT_TAG = re.compile(r"<[^>]+>")


def _strip_richtext(text: str) -> str:
    """剥离官方富文本/注音标签，只留纯文本。

    官方文本池可能含注音标签（<p="しゅそ" padding=0>首礎</p> -> 首礎）或
    <@...> 富文本标签。lang JSON 的 pattern 用于 OCR 匹配、ok.po msgstr
    用于界面显示，均不应含 HTML 标签（AGENTS.md 最小原则）。
    """
    return _RICHTEXT_TAG.sub("", text)


def official_vals_multi(raw_texts: dict, ranked: list) -> dict:
    """合并多个候选官方 key 的项目语言值。

    对每种语言，按票数从高到低取第一个有值的 key 的文本（可能有重复
    key 指向不同文本，取全部去重后逐个尝试）。

    官方文本池中同一对象可能同时存在"带注音/富文本标签版"与"纯文本版"
    （如首墩：<p="しゅそ" padding=0>首礎</p> 与 首礎 各占一个 key）。
    因此优先选纯文本候选；若全部候选都带标签，才回退剥离标签。
    """
    vals = {}
    for lang in REPO_LANGS:
        table = raw_texts.get(lang)
        if not table:
            continue
        fallback = None
        for key, _ in ranked:
            v = table.get(key)
            if isinstance(v, str) and v.strip():
                if _RICHTEXT_TAG.search(v):
                    if fallback is None:
                        fallback = _strip_richtext(v).strip()
                    continue  # 优先找同锚点的纯文本版本
                vals[lang] = v.strip()
                break
        else:
            if fallback is not None:
                vals[lang] = fallback
    return vals


def official_format_vals(raw_texts: dict, index: dict, cn_format: str) -> dict:
    """把官方格式文本（如 ``%d天``）转换为 OCR 正则（如 ``(\\d+)天``）。"""
    ranked = pick_keys([("zh_CN", cn_format, False)], index)
    if not ranked:
        return {}
    vals = official_vals_multi(raw_texts, ranked)
    converted = {}
    for lang, value in vals.items():
        # 官方格式文本的占位符对应 OCR 中的任意数字；其余内容按字面匹配。
        escaped = re.escape(value)
        escaped = escaped.replace(r"%d", r"(\d+)").replace(r"%s", r"(\d+)")
        converted[lang] = escaped
    return converted


def is_regex_like(node: dict, lang: str) -> bool:
    sub = node.get(lang)
    if not isinstance(sub, dict):
        return False
    p = sub.get("pattern")
    return isinstance(p, str) and bool(_STRONG_REGEX_META.search(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只预览不写入")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    index, raw_texts = load_official()
    print(f"官方语言表加载完成: {len(raw_texts)} 语言")

    json_stats = {}
    all_touched = []
    po_official = {}  # {zh_CN名: {lang: 官方译名}} 供 ok.po 同步

    for path in sorted(LANG_DIR.glob("*.json")):
        data = json5.loads(path.read_text(encoding="utf-8"))
        stats = 0
        for key, node in data.items():
            if not isinstance(node, dict):
                continue
            anchors = node_values(node)
            if not anchors:
                continue
            # 每个锚点分支独立反查官方翻译，再按 | 合并。
            # 支持 "A|B" 多分支正则 pattern：各分支分别翻译后合并为 "A'|B'"，
            # 避免缺失语言只补到第一个分支（如 k_059a808c 理智补给|危机筹备）。
            branch_ovs = []
            branch_po_ovs = []
            for a_lang, a_text, a_is_pattern in anchors:
                # 游戏显示的是“数字+单位”，官方文本池将其保存为 %d天/%d小时。
                # 普通的“天”/“小时”反查只能得到单位缩写或找不到 key，
                # 因此这里按格式词条特例生成完整 OCR 正则。
                format_anchor = {"天": "%d天", "小时": "%d小时"}.get(a_text) if len(anchors) > 1 else None
                if format_anchor:
                    ranked_format = pick_keys([("zh_CN", format_anchor, False)], index)
                    ov_b = official_format_vals(raw_texts, index, format_anchor)
                    if ov_b:
                        branch_ovs.append(ov_b)
                        branch_po_ovs.append(official_vals_multi(raw_texts, ranked_format))
                    continue
                ranked_b = pick_keys([(a_lang, a_text, a_is_pattern)], index)
                if not ranked_b:
                    continue
                ov_b = official_vals_multi(raw_texts, ranked_b)
                if ov_b:
                    branch_ovs.append(ov_b)
                    branch_po_ovs.append(ov_b)
            # 多分支 pattern 必须所有分支都找到官方候选后才写回。
            # 官方缺少某个分支时，不能把另一个分支误写成完整结果。
            if len(anchors) > 1 and len(branch_ovs) != len(anchors):
                continue
            ov = {}
            for lang in REPO_LANGS:
                parts = [b.get(lang) for b in branch_ovs if b.get(lang)]
                if parts:
                    ov[lang] = "|".join(dict.fromkeys(parts))
            if not ov:
                continue
            # zh_CN 锚点（用于 ok.po msgid）
            zh = None
            for lang in ("zh_CN", "zh_TW"):
                sub = node.get(lang)
                if isinstance(sub, dict):
                    for kind in ("string", "pattern"):
                        v = sub.get(kind)
                        if (
                            isinstance(v, str)
                            and v.strip()
                            and not (kind == "pattern" and _STRONG_REGEX_META.search(v))
                        ):
                            zh = v.strip()
                            break
                if zh:
                    break
            if not zh:
                # 用任意锚点语言作 fallback
                zh = anchors[0][1]
            # 补齐缺失语言键（不覆盖已有）
            sub_style = None
            for kind in ("string", "pattern"):
                z = node.get("zh_CN", {}).get(kind)
                if isinstance(z, str) and z.strip():
                    sub_style = kind
                    break
            if sub_style is None:
                sub_style = "string"
            for lang in REPO_LANGS:
                if lang == "zh_CN":
                    continue
                val = ov.get(lang)
                if not val:
                    continue
                cur = node.get(lang)
                # 已有语言键：解包是可信源，与官方不同则覆盖；
                # 但真正的正则 pattern（含 | ^ $ * + ? \ 等强元字符）保留，不覆盖
                if is_regex_like(node, lang):
                    continue
                if isinstance(cur, dict) and (
                    isinstance(cur.get("string"), str) or isinstance(cur.get("pattern"), str)
                ):
                    # 覆盖已有值（string 或普通 pattern）
                    for kind in ("string", "pattern"):
                        if isinstance(cur.get(kind), str) and cur[kind] != val:
                            cur[kind] = val
                            stats += 1
                            all_touched.append((path.name, key, zh, lang, val))
                            break
                    continue
                node[lang] = {sub_style: val}
                stats += 1
                all_touched.append((path.name, key, zh, lang, val))
            # 收集 ok.po 官方译名（zh_CN 名 -> 各语言官方值，仅 UI 支持的 6 种）。
            # lang JSON 的多分支 pattern 可以合并，但 PO 的 msgid 是单个 UI 文本：
            # 例如“理智补给|危机筹备”的 msgid 应只同步“理智补给”的翻译，
            # 不能把 OCR 正则的另一分支拼进界面文案。
            po_ov = branch_po_ovs[0] if branch_po_ovs else ov
            po_vals = {}
            for lang in PO_LANGS:
                if lang == "zh_CN":
                    continue
                v = po_ov.get(lang)
                if v:
                    po_vals[lang] = v
            if po_vals:
                po_official.setdefault(zh, {}).update(po_vals)
        if stats and not args.dry:
            write_json(path, data)
        json_stats[path.name] = stats

    print("\n=== assets/lang/*.json 补齐统计 ===")
    total = 0
    for fname, n in sorted(json_stats.items()):
        if n:
            print(f"  {fname}: +{n} 语言键")
            total += n
    print(f"  合计新增 {total} 个语言键")

    # ok.po 同步（仅 UI 支持的 6 种 locale；不新增 msgid，只更新已有条目）
    print("\n=== i18n ok.po 同步 ===")
    if po_official and not args.dry:
        po_stats, po_touched = sync_po_entries(po_official, PO_LANGS, PO_DIR, create_missing=False)
        for loc, n in po_stats.items():
            print(f"  {loc}: {n} entries updated")
    else:
        print(f"  (dry 或无可同步) 候选 msgid: {len(po_official)}")

    # 详细变更写文件
    if args.verbose:
        report = ROOT / "tmp" / "ef_sync_official_report.txt"
        with open(report, "w", encoding="utf-8") as f:
            for fname, key, zh, lang, val in all_touched:
                f.write(f"{fname}\t{key}\t{zh}\t{lang}\t{val}\n")
        print(f"\n详细变更: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
