"""从官方 WIKI 快照为角色技能 JSON 填充分级数值表。

职责：
1. 复用 analyze_operator_skills 的官方文档解析，抽取每个技能的
   RANK 1-9 / 专精 1-3 数值表，写入对应角色 JSON 技能的 ``rank_stats``：
   ``{"levels": [...], "rows": [{"label": str, "values": [str]}]}``
2. 将技能 description 同步为官方原文单行拼接版（仓库惯例：描述不含
   换行、不含 <br>、不嵌数值）。
3. 提取「干员属性」面板（干员等级 × 六维），写入角色 JSON 头部的
   ``base_stats``：``{"levels": [...], "rows": {属性名: [各等级值]}}``。
4. 只更新上述字段；其余字段（effects / enhancements 等）不动。
5. 加载端 src/data/character_skills.py 不读取 rank_stats / base_stats，
   运行时不受影响；skill_rotation（实际伤害排序）读取两者。

匹配键为 (skill_type, name)。同名技能多形态（如「诀」的阵诀·智/意）：描述合并全形态文本，主形态表写入 rank_stats，其余进 rank_stats_variants。
默认 dry-run，--write 才落盘。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_operator_skills import (  # noqa: E402
    CHARACTER_SKILLS_DIR,
    SNAPSHOT_ROOT,
    _combat_widget,
    _document_tables,
    _document_text,
    _latest_snapshot,
    _normalize_name,
    _skill_tables,
)


def _flatten_description(text: str) -> str:
    """官方多段描述拼接为单行（各段以句号结尾，直接相连）。"""
    return "".join(part.strip() for part in text.splitlines() if part.strip())


def _rank_stats(rank_table: list[list[str]]) -> dict | None:
    """把 WIKI 数值表转换为 {levels, rows}；表头不合法时返回 None。"""
    if not rank_table or not rank_table[0]:
        return None
    header = rank_table[0]
    if header[0] != "技能等级":
        return None
    levels = [cell.strip() for cell in header[1:]]
    if not any(levels):
        return None
    rows: list[dict] = []
    for row in rank_table[1:]:
        if not row or not str(row[0]).strip():
            continue
        label = str(row[0]).strip()
        values = [cell.strip() for cell in row[1:]]
        values = [cell.replace(",", ".") if re.fullmatch(r"\d+,\d+", cell) else cell for cell in values]
        # 列数不齐时按表头补空
        values += [""] * (len(levels) - len(values))
        rows.append({"label": label, "values": values[: len(levels)]})
    return {"levels": levels, "rows": rows} if rows else None


def _base_stats(document_map: dict) -> dict | None:
    """提取「干员属性」面板：干员等级 × 六维（生命/攻击/力量/敏捷/智识/意志）。

    属性表是独立 document（不在战斗 widget 内），按表头「干员等级」定位。
    找不到时返回 None。
    """
    for doc_id, doc in document_map.items():
        if "干员等级" not in json.dumps(doc, ensure_ascii=False):
            continue
        for table in _document_tables(document_map, doc_id):
            if not table or not table[0] or table[0][0] != "干员等级":
                continue
            levels = [cell.strip() for cell in table[0][1:]]
            if not any(levels):
                continue
            rows: dict[str, list[str]] = {}
            for row in table[1:]:
                if not row or not str(row[0]).strip():
                    continue
                label = str(row[0]).strip()
                values = [cell.strip() for cell in row[1:]]
                values += [""] * (len(levels) - len(values))
                rows[label] = values[: len(levels)]
            if "攻击力" in rows:
                return {"levels": levels, "rows": rows}
    return None


def fill_operator(detail_path: Path, skills_by_file: dict[Path, dict]) -> dict:
    """解析一个干员快照，返回改动摘要（不落盘）。"""
    payload = json.loads(detail_path.read_text(encoding="utf-8"))
    item = payload.get("data", {}).get("item", {})
    name = str(item.get("name") or detail_path.stem)
    summary = {"name": name, "matched": False, "skills_updated": 0, "desc_updated": 0,
               "stats_updated": 0, "variants": [], "unmatched": [], "preview": False}

    document = item.get("document") or {}
    widget = _combat_widget(document)
    if widget is None:
        summary["preview"] = True
        return summary

    character = skills_by_file.get(_normalize_name(name))
    if character is None:
        return summary
    summary["matched"] = True
    current_by_key: dict[tuple[str, str], dict] = {}
    for skill in character.get("skills") or []:
        current_by_key[(str(skill.get("skill_type")), str(skill.get("name")))] = skill

    document_map = document.get("documentMap") or {}
    # 收集全部 tab，同名 (skill_type, name) 的多个 tab 属于同一技能的不同形态
    tabs_by_key: dict[tuple[str, str], list[dict]] = {}
    tab_order: list[tuple[str, str]] = []
    for tab in widget.get("tabList", []):
        tab_data = widget.get("tabDataMap", {}).get(tab.get("tabId"), {})
        intro = tab_data.get("intro") or {}
        skill_name = str(intro.get("name") or "").strip()
        skill_type = str(intro.get("type") or "").strip()
        key = (skill_type, skill_name)
        if key not in tabs_by_key:
            tab_order.append(key)
            tabs_by_key[key] = []
        tabs_by_key[key].append(tab_data)

    for key, tabs in tabs_by_key.items():
        skill_type, skill_name = key
        target = current_by_key.get(key)
        if target is None:
            summary["unmatched"].append(f"{skill_type}/{skill_name}")
            continue

        # 描述：所有形态的官方文本按 tab 顺序拼接（多形态角色如「诀」的本地
        # 数据历来是全形态合并文本；单形态角色只有一段，行为不变）
        parts = [
            _flatten_description(_document_text(document_map, tab.get("intro", {}).get("description")))
            for tab in tabs
        ]
        wiki_desc = "".join(part for part in parts if part)
        local_desc = re.sub(r"\s+", "", str(target.get("description") or ""))
        if wiki_desc and local_desc != re.sub(r"\s+", "", wiki_desc):
            target["description"] = wiki_desc
            summary["desc_updated"] += 1

        # 数值表：第一个形态为主表；其余形态进 rank_stats_variants（行集合不同，
        # 无法按行合并）
        stats_list = []
        for tab in tabs:
            rank_table, _ = _skill_tables(_document_tables(document_map, tab.get("content")))
            stats = _rank_stats(rank_table)
            if stats is not None:
                stats_list.append(stats)
        if stats_list:
            if stats_list[0] != target.get("rank_stats"):
                target["rank_stats"] = stats_list[0]
                summary["skills_updated"] += 1
            variants = stats_list[1:]
            if variants != target.get("rank_stats_variants"):
                target["rank_stats_variants"] = variants
                summary["skills_updated"] += 1
            if variants:
                summary["variants"].append(f"{skill_type}/{skill_name}多形态×{len(tabs)}")

    # 干员属性面板（base_stats）：与技能无关联，直接挂角色头部
    base_stats = _base_stats(document_map)
    if base_stats is not None and base_stats != character.get("base_stats"):
        character["base_stats"] = base_stats
        summary["stats_updated"] = 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=None, help="快照目录名，默认 latest.json")
    parser.add_argument("--write", action="store_true", help="真正写回角色 JSON（默认 dry-run）")
    args = parser.parse_args()

    snapshot = args.snapshot or _latest_snapshot()
    snapshot_dir = SNAPSHOT_ROOT / snapshot
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))

    # name -> 角色文件路径 + 已加载内容
    skills_by_file: dict[Path, dict] = {}
    paths: dict[str, Path] = {}
    for path in sorted(CHARACTER_SKILLS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        key = str(data.get("name") or path.stem)
        paths[key] = path
        skills_by_file[key] = data

    totals = {"matched": 0, "preview": 0, "no_file": 0, "skills_updated": 0, "desc_updated": 0, "stats_updated": 0}
    for entry in manifest.get("operators", []):
        summary = fill_operator(snapshot_dir / str(entry["detail_file"]), skills_by_file)
        totals["matched" if summary["matched"] else ("preview" if summary["preview"] else "no_file")] += 1
        totals["skills_updated"] += summary["skills_updated"]
        totals["desc_updated"] += summary["desc_updated"]
        totals["stats_updated"] += summary["stats_updated"]
        notes = []
        if summary["variants"]:
            notes.append(f"变体跳过: {', '.join(summary['variants'])}")
        if summary["unmatched"]:
            notes.append(f"本地无同名技能: {', '.join(summary['unmatched'])}")
        print(f"{summary['name']}: 技能表+{summary['skills_updated']} 描述+{summary['desc_updated']}"
              f" 属性+{summary['stats_updated']}"
              + (f"  [{' | '.join(notes)}]" if notes else ""))

    print(f"\n快照 {snapshot}: {totals}")
    if not args.write:
        print("dry-run，未写盘；加 --write 落盘")
        return 0

    for key, data in skills_by_file.items():
        path = paths[key]
        text = json.dumps(data, ensure_ascii=False, indent=4) + "\n"
        path.write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))
    print(f"已写回 {len(skills_by_file)} 个角色文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
