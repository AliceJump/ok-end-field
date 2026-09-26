"""从 item_details 快照解析武器/装备/武器基质，生成结构化数据文件。

输入：tools/wiki_catalog/item_details/<stamp>/（capture_skland_item_details.py 产物）
输出（assets/data/，写 LF）：
- weapons.json     满级攻击表 / 武器技能 Rank1-9 / 推荐干员 / 推荐基质
- equipments.json  部位 / 套组 / LV70 属性 / 精锻3 满级值 / 套组效果 / 推荐干员
- matrices.json    基质属性与效果（结构随官方页面，保留原始表格）

用法：
    python scripts/skill-data/build_loadout_data.py                 # 最新快照
    python scripts/skill-data/build_loadout_data.py --snapshot 20260924_164955
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from analyze_operator_skills import _document_tables  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ITEM_DETAILS_ROOT = ROOT / "tools/wiki_catalog/item_details"
DATA_DIR = ROOT / "assets/data"


# ---------------------------------------------------------------- 基础工具

def _cell_text(cell) -> str:
    """表格单元格可能是 str 或 list[str]（多段落）。"""
    if cell is None:
        return ""
    if isinstance(cell, list):
        return "\n".join(str(c) for c in cell if str(c).strip())
    return str(cell)


def _iter_widget_contents(doc: dict) -> list[tuple[str, str]]:
    """遍历 (chapter_title, content_id)：按 chapterGroup 分组还原章节归属。

    除 tab ``content`` 外还包括 ``intro.description``（装备套组效果等章节
    的正文存放在 description 文档中）。
    """
    dm = doc.get("documentMap") or {}
    wcm = doc.get("widgetCommonMap") or {}
    title_by_widget: dict[str, str] = {}
    for group in doc.get("chapterGroup") or []:
        chapter_title = str(group.get("title") or "")
        for w in group.get("widgets") or []:
            title_by_widget[str(w.get("id"))] = chapter_title
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for wid, widget in wcm.items():
        chapter = title_by_widget.get(wid, "")
        for td in (widget.get("tabDataMap") or {}).values():
            if not td:
                continue
            for key in ("content", "description"):
                intro = td.get("intro") or {}
                candidates = [td.get(key)]
                if key == "description":
                    candidates.append(intro.get("description"))
                for content in candidates:
                    if content and content in dm and content not in seen:
                        seen.add(content)
                        results.append((chapter, content))
    for did in dm:
        if did not in seen:
            results.append(("", did))
            seen.add(did)
    return results


def _document_plain_text(dm: dict, content_id: str) -> str:
    """按 blockIds 顺序抽取文档纯文本（含表格单元格文本）。"""

    def block_text(b: dict) -> str:
        if not isinstance(b, dict):
            return ""
        if b.get("kind") == "text":
            parts = []
            for el in (b.get("text") or {}).get("inlineElements") or []:
                if el.get("kind") == "text":
                    parts.append(str((el.get("text") or {}).get("text") or ""))
                elif el.get("kind") == "entry":
                    parts.append(f"[entry:{(el.get('entry') or {}).get('id')}]")
            return "".join(parts)
        if b.get("kind") == "table":
            tb = b.get("table") or {}
            cells = []
            for rid in tb.get("rowIds") or []:
                for cid in tb.get("columnIds") or []:
                    cell = (tb.get("cellMap") or {}).get(f"{rid}_{cid}") or {}
                    for child in cell.get("childIds") or []:
                        cells.append(block_text(blocks_ref.get(child) or {}))
            return " ".join(c for c in cells if c)
        return ""

    d = dm.get(content_id) or {}
    blocks_ref = d.get("blockMap") or {}
    lines = []
    for bid in d.get("blockIds") or []:
        t = block_text(blocks_ref.get(bid) or {}).strip()
        if t:
            lines.append(t)
    return "\n".join(lines)


def _all_tables(doc: dict) -> list[tuple[str, list[list[str]]]]:
    """返回 (chapter, table) 列表；单元格规整为多行文本。"""
    dm = doc.get("documentMap") or {}
    out: list[tuple[str, list[list[str]]]] = []
    for chapter, content in _iter_widget_contents(doc):
        for table in _document_tables(dm, content):
            if not table:
                continue
            norm = [[_cell_text(c) for c in row] for row in table]
            out.append((chapter, norm))
    return out


def _kv_tables(doc: dict) -> dict[str, str]:
    """widgetCommonMap tableList 的 label/value 键值对。"""
    result: dict[str, str] = {}
    for widget in (doc.get("widgetCommonMap") or {}).values():
        for t in widget.get("tableList") or []:
            label = str(t.get("label") or "").strip()
            value = t.get("value")
            if label:
                result[label] = _cell_text(value).strip()
    return result


def _entry_ids(cell_text: str) -> list[str]:
    return re.findall(r"\[entry:(\d+)\b", cell_text)


def _tag_groups(doc_tag_tree: list) -> dict[str, tuple[str, str]]:
    """filterTagTree → {tagId: (组名, 名)}。"""
    tags: dict[str, tuple[str, str]] = {}

    def walk(nodes, group: str) -> None:
        for n in nodes or []:
            nid = str(n.get("id") or "")
            name = str(n.get("name") or "").strip()
            if nid:
                tags[nid] = (group, name)
            walk(n.get("children"), name or group)

    walk(doc_tag_tree, "")
    return tags


def _rarity_from_tags(tag_ids: list[str]) -> int | None:
    for t in tag_ids or []:
        m = re.fullmatch(r"1000(\d)", str(t))
        if m:
            return int(m.group(1))
    return None


def _write_json(path: Path, data) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))


# ---------------------------------------------------------------- 解析器

def parse_weapon(item: dict, item_id: str) -> dict:
    doc = item.get("document") or {}
    kv = _kv_tables(doc)
    tables = _all_tables(doc)

    base_attack: dict | None = None
    skills: list[dict] = []
    rec_operators: list[str] = []
    rec_matrices: list[str] = []

    for chapter, table in tables:
        header = [c.strip() for c in table[0]]
        if header and header[0] == "武器等级" and base_attack is None:
            levels = header[1:]
            values: list[str] = []
            for row in table[1:]:
                if row and row[0].strip() == "基础攻击力":
                    values = [c.strip() for c in row[1:]]
            base_attack = {"levels": levels, "values": values}
        elif header and header[0] == "技能详情":
            names = header[1:]
            ranks: dict[str, dict[str, str]] = {}
            for row in table[1:]:
                if not row:
                    continue
                rank = row[0].strip()
                if not re.match(r"Rank\s*\d+", rank):
                    continue
                ranks[rank] = {
                    name: _cell_text(row[i + 1]).strip()
                    for i, name in enumerate(names) if i + 1 < len(row)
                }
            if names:
                skills.append({"names": names, "ranks": ranks})
        elif header and header[:1] == ["推荐装备干员"]:
            if len(table) > 1:
                rec_operators = _entry_ids(_cell_text(table[1][0] if len(table[1]) > 0 else ""))
                if len(header) > 1 and len(table[1]) > 1:
                    rec_matrices = _entry_ids(_cell_text(table[1][1]))

    return {
        "item_id": item_id,
        "type": kv.get("类型", ""),
        "rarity": _rarity_from_tags(item.get("tagIds") or []),
        "owner": kv.get("所属", ""),
        "acquire": kv.get("主要获取方式", ""),
        "release": kv.get("上线时间", ""),
        "skills_summary": kv.get("技能", ""),
        "base_attack": base_attack,
        "weapon_skills": skills,
        "recommended_operator_ids": rec_operators,
        "recommended_matrix_ids": rec_matrices,
    }


def parse_equip(item: dict, item_id: str, equip_tags: dict[str, tuple[str, str]]) -> dict:
    doc = item.get("document") or {}
    tables = _all_tables(doc)

    part = set_name = ability = stat_tag = None
    quality = None
    for tid in item.get("tagIds") or []:
        group, name = equip_tags.get(str(tid), ("", ""))
        if group == "部位":
            part = name
        elif group == "装备套组":
            set_name = name
        elif group == "品质":
            quality = name
        elif group == "能力值":
            ability = name
        elif group == "属性":
            stat_tag = name

    lv70: dict[str, int | str] = {}
    refinement_max: dict[str, str] = {}
    set_effect_text = ""
    rec_operators: list[str] = []
    set_effect_parts: list[str] = []

    dm = doc.get("documentMap") or {}
    for chapter, content in _iter_widget_contents(doc):
        if chapter == "装备套组效果":
            text = _document_plain_text(dm, content).strip()
            if text and text not in set_effect_parts:
                set_effect_parts.append(text)
            for table in _document_tables(dm, content):
                for row in table:
                    for cell in row:
                        t = _cell_text(cell).strip()
                        if t and not t.startswith("[entry:") and t not in set_effect_parts:
                            set_effect_parts.append(t)
            continue
        for table in _document_tables(dm, content):
            if not table:
                continue
            header = [c.strip() for c in table[0]]
            if chapter == "基础属性":
                flat = [c.strip() for row in table for c in row]
                for i, cell in enumerate(flat):
                    if (
                        cell
                        and not cell.startswith("+")
                        and i + 1 < len(flat)
                        and re.fullmatch(r"\+\d+(?:\.\d+)?%?", flat[i + 1])
                    ):
                        value = flat[i + 1]
                        if "%" in value:
                            lv70[cell] = value
                        elif re.fullmatch(r"\+\d+", value):
                            lv70[cell] = int(value[1:])
            elif header and header[0] == "可精锻属性":
                for row in table[1:]:
                    if row and row[0].strip():
                        values = [c.strip() for c in row[1:] if c.strip()]
                        if values:
                            refinement_max[row[0].strip()] = values[-1]
            elif header and header[:1] == ["推荐干员"]:
                for row in table[1:]:
                    for cell in row:
                        rec_operators.extend(_entry_ids(_cell_text(cell)))

    set_effect_text = "\n".join(set_effect_parts).strip()

    return {
        "item_id": item_id,
        "rarity": _rarity_from_tags(item.get("tagIds") or []),
        "quality": quality,
        "part": part,
        "set": set_name,
        "ability_tag": ability,
        "stat_tag": stat_tag,
        "lv70_stats": lv70,
        "refinement_max": refinement_max,
        "set_effect": set_effect_text,
        "recommended_operator_ids": rec_operators,
    }


def parse_matrix(item: dict, item_id: str, matrix_tags: dict[str, tuple[str, str]]) -> dict:
    doc = item.get("document") or {}
    kv = _kv_tables(doc)
    tables = _all_tables(doc)

    tags: dict[str, list[str]] = {}
    for tid in item.get("tagIds") or []:
        group, name = matrix_tags.get(str(tid), ("", ""))
        if group:
            tags.setdefault(group, []).append(name)

    raw_tables = [[[c for c in row] for row in table] for _, table in tables]
    return {
        "item_id": item_id,
        "rarity": _rarity_from_tags(item.get("tagIds") or []),
        "kv": kv,
        "tags": tags,
        "tables": raw_tables,
    }


# ---------------------------------------------------------------- 主流程

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=None, help="item_details 快照目录名（默认最新）")
    args = parser.parse_args()

    if args.snapshot:
        snap_dir = ITEM_DETAILS_ROOT / args.snapshot
    else:
        candidates = sorted(p for p in ITEM_DETAILS_ROOT.iterdir() if p.is_dir())
        if not candidates:
            print("没有可用快照", file=sys.stderr)
            return 1
        snap_dir = candidates[-1]
    manifest = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"快照: {snap_dir.name}  成功 {manifest['success_count']} 条")

    # itemId → name 解析器：zh_cn catalog 全子类 + 本快照 manifest
    id_to_name: dict[str, str] = {}
    zh_cn_dir = ROOT / "tools/wiki_catalog/zh_cn"
    for cat_file in sorted(zh_cn_dir.glob("*/m1_s*.json")):
        payload = json.loads(cat_file.read_text(encoding="utf-8"))
        for catalog in payload.get("data", {}).get("catalog", []):
            for sub in catalog.get("typeSub", []):
                for it in sub.get("items") or []:
                    name = str(it.get("name") or "").strip()
                    if it.get("itemId") and name:
                        id_to_name[str(it["itemId"])] = name
    for entry in manifest.get("items", []):
        id_to_name.setdefault(str(entry["item_id"]), str(entry["name"]).strip())

    # 装备/基质筛选标签树
    def load_tag_tree(sub_id: str) -> dict[str, tuple[str, str]]:
        for cat_file in sorted(zh_cn_dir.glob(f"*/m1_s{sub_id}.json")):
            payload = json.loads(cat_file.read_text(encoding="utf-8"))
            for catalog in payload.get("data", {}).get("catalog", []):
                for sub in catalog.get("typeSub", []):
                    if str(sub.get("id")) == sub_id:
                        return _tag_groups(sub.get("filterTagTree") or [])
        return {}

    equip_tags = load_tag_tree("4")
    matrix_tags = load_tag_tree("7")

    # 干员 catalog id 集：过滤「推荐装备干员」里的非干员条目（相关装备链接等）
    operator_ids: set[str] = set()
    for cat_file in sorted(zh_cn_dir.glob("*/m1_s1.json")):
        payload = json.loads(cat_file.read_text(encoding="utf-8"))
        for catalog in payload.get("data", {}).get("catalog", []):
            for sub in catalog.get("typeSub", []):
                if str(sub.get("id")) == "1":
                    for it in sub.get("items") or []:
                        if it.get("itemId"):
                            operator_ids.add(str(it["itemId"]))

    def _only_operators(ids: list[str]) -> list[str]:
        seen: list[str] = []
        for i in ids:
            if i in operator_ids and i not in seen:
                seen.append(i)
        return seen

    weapons: dict[str, dict] = {}
    equipments: dict[str, dict] = {}
    matrices: dict[str, dict] = {}

    for entry in manifest.get("items", []):
        detail_path = snap_dir / entry["detail_file"]
        payload = json.loads(detail_path.read_text(encoding="utf-8"))
        item = payload.get("data", {}).get("item", {})
        item_id = str(entry["item_id"])
        name = id_to_name.get(item_id, str(entry["name"]).strip())
        kind = entry["kind"]
        if kind == "weapon":
            parsed = parse_weapon(item, item_id)
            parsed["recommended_operator_ids"] = _only_operators(parsed["recommended_operator_ids"])
            parsed["recommended_matrix_ids"] = [
                i for i in parsed["recommended_matrix_ids"] if i in id_to_name
            ]
            weapons[name] = parsed
        elif kind == "equip":
            parsed = parse_equip(item, item_id, equip_tags)
            parsed["recommended_operator_ids"] = _only_operators(parsed["recommended_operator_ids"])
            equipments[name] = parsed
        elif kind == "matrix":
            matrices[name] = parse_matrix(item, item_id, matrix_tags)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(DATA_DIR / "weapons.json", weapons)
    _write_json(DATA_DIR / "equipments.json", equipments)
    _write_json(DATA_DIR / "matrices.json", matrices)

    # 摘要
    print(f"weapons: {len(weapons)}  equipments: {len(equipments)}  matrices: {len(matrices)}")
    missing_attack = [n for n, w in weapons.items() if not (w.get("base_attack") or {}).get("values")]
    missing_refine = [n for n, e in equipments.items() if not e.get("refinement_max")]
    no_set = [n for n, e in equipments.items() if not e.get("set")]
    print(f"武器缺攻击表: {len(missing_attack)} {missing_attack[:5]}")
    print(f"装备缺精锻表: {len(missing_refine)} {missing_refine[:5]}")
    print(f"装备缺套组: {len(no_set)} {no_set[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
