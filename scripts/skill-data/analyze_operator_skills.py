"""从森空岛官方 WIKI 快照分析干员技能、条件、强化态与特殊层数。

输入是 ``capture_skland_operator_details.py`` 生成的完整原始快照。脚本会：

1. 解析“战斗技能”组件中的技能名称、类型、完整描述；
2. 解析技能数据和升级材料表（RANK 1-9、专精 1-3）；
3. 从描述中按句提取“当/若/如果”条件，区分发动条件与结算条件；
4. 识别层数、阈值、最多/至少、消耗全部、最后一层等特殊层数语义；
5. 使用项目 ``EFFECT_TERMS`` 给条件和结果提出 effect ID 候选；
6. 对照 ``assets/data/character_skills``，输出缺失、描述不一致和多条件建模提示。

脚本只生成审阅报告，不自动改角色 JSON，避免把自然语言启发式结果直接写入正式数据。

用法：
    python scripts/skill-data/analyze_operator_skills.py
    python scripts/skill-data/analyze_operator_skills.py --snapshot 20260830_221449
    python scripts/skill-data/analyze_operator_skills.py --operator 安塔尔 --stdout

默认输出到快照内的 ``analysis/`` 目录（已由 tools/wiki_catalog 的 gitignore 排除）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.effects import EFFECT_TERMS, EffectType, match_effect_terms

SNAPSHOT_ROOT = ROOT / "tools" / "wiki_catalog" / "operator_details"
CHARACTER_SKILLS_DIR = ROOT / "assets" / "data" / "character_skills"

_CONDITION_START_RE = re.compile(r"(?:^|[，,；;。\n])\s*(当|若|如果)(.+?)(?=[；;。\n]|$)")
_ACTIVATION_RE = re.compile(r"可以发动|可发动|才能发动|后可以发动")
_RESULT_SPLIT_RE = re.compile(r"(?:，|,)(?:则|会|将|还会|额外|并|随后)")
_STACK_RE = re.compile(
    r"(?P<comparator>至少|至多|最多|不少于|不超过|达到|已拥有|处于)?\s*"
    r"(?P<count>\d+|一|二|三|四|五|最后)\s*(?P<unit>层|点|次|柄|枚|段|个|处|支)"
    r"(?P<subject>[\u4e00-\u9fffA-Za-z·]*)"
)
_CONSUME_ALL_RE = re.compile(r"消耗(?:掉)?(?:目标|敌人|自身)?(?:身上|的)?所有(?P<subject>[^，。；;]+)")
_REPEAT_RE = re.compile(r"再次施加(?:该|其|同类|相同的)?(?P<subject>[^，。；;]+)")
_SPECIAL_LAST_RE = re.compile(r"最后(?:一|1)(?:层|点|次)|消耗的.+最后(?:一|1)(?:层|点)")

_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "最后": 1,
}

_STATE_STACK_TERMS = tuple(
    sorted(
        {
            term
            for term, effect in EFFECT_TERMS.items()
            if effect.name.startswith("STACK_") or term.endswith("附着")
        }
        | {
            "破防",
            "铁誓",
            "熔火",
            "涡流",
            "青霆剑",
            "雷枪",
            "强雷枪",
            "支援晶体",
            "自制炸弹",
            "连击",
            "种子",
            "蓄力",
        },
        key=len,
        reverse=True,
    )
)


@dataclass
class ConditionAnalysis:
    marker: str
    text: str
    trigger_text: str
    result_text: str
    role: str
    trigger_effects: list[str] = field(default_factory=list)
    result_effects: list[str] = field(default_factory=list)
    stack_rules: list[dict[str, Any]] = field(default_factory=list)
    consumes_all: list[str] = field(default_factory=list)
    repeats: list[str] = field(default_factory=list)
    is_last_resource: bool = False


@dataclass
class SkillAnalysis:
    name: str
    skill_type: str
    description: str
    rank_table: list[list[str]] = field(default_factory=list)
    material_table: list[list[str]] = field(default_factory=list)
    conditions: list[ConditionAnalysis] = field(default_factory=list)
    description_effects: list[str] = field(default_factory=list)
    stack_rules: list[dict[str, Any]] = field(default_factory=list)
    current_skill_id: str | None = None
    current_effects: list[str] = field(default_factory=list)
    current_enhancement_count: int = 0
    review_flags: list[str] = field(default_factory=list)


@dataclass
class OperatorAnalysis:
    item_id: str
    name: str
    detail_file: str
    preview_only: bool
    skills: list[SkillAnalysis] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)


def _inline_text(element: dict) -> str:
    text = element.get("text")
    if isinstance(text, dict):
        return str(text.get("text") or "")
    entry = element.get("entry")
    if isinstance(entry, dict):
        item_id = entry.get("id", "")
        count = entry.get("count", "")
        return f"[entry:{item_id} x{count}]"
    return ""


def _block_text(document: dict, block_id: str, seen: set[str] | None = None) -> str | list[list[str]]:
    """把官方文档块还原为文本或二维表。"""
    seen = set() if seen is None else seen
    if block_id in seen:
        return ""
    seen.add(block_id)
    block = document.get("blockMap", {}).get(block_id, {})
    kind = block.get("kind")

    if kind == "text":
        return "".join(_inline_text(item) for item in block.get("text", {}).get("inlineElements", []))

    if kind == "table":
        table = block.get("table", {})
        rows: list[list[str]] = []
        for row_id in table.get("rowIds", []):
            row: list[str] = []
            for column_id in table.get("columnIds", []):
                cell = table.get("cellMap", {}).get(f"{row_id}_{column_id}", {})
                values = [
                    _block_text(document, child_id, seen.copy())
                    for child_id in cell.get("childIds", [])
                ]
                row.append(" / ".join(str(value) for value in values if value))
            rows.append(row)
        return rows

    values: list[str] = []
    for key in ("childIds", "blockIds"):
        for child_id in block.get(key, []):
            value = _block_text(document, child_id, seen.copy())
            if value:
                values.append(str(value))
    return "\n".join(values)


def _document_parts(document_map: dict, document_id: str | None) -> list[str | list[list[str]]]:
    if not document_id:
        return []
    document = document_map.get(document_id, {})
    return [
        _block_text(document, block_id)
        for block_id in document.get("blockIds", [])
    ]


def _document_text(document_map: dict, document_id: str | None) -> str:
    values: list[str] = []
    for part in _document_parts(document_map, document_id):
        if isinstance(part, str) and part.strip():
            values.append(part.strip())
    return "\n".join(values)


def _document_tables(document_map: dict, document_id: str | None) -> list[list[list[str]]]:
    return [part for part in _document_parts(document_map, document_id) if isinstance(part, list)]


def _effect_values(text: str) -> list[str]:
    values: list[str] = []
    for _, effect in match_effect_terms(text):
        if effect.value not in values:
            values.append(effect.value)
    return values


def _stack_rules(text: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in _STACK_RE.finditer(text):
        raw = match.group(0).strip()
        subject = next((term for term in _STATE_STACK_TERMS if term in raw), None)
        if subject is None:
            continue
        subject_pos = raw.find(subject)
        unit_end = match.end("unit") - match.start()
        if subject_pos >= 0 and subject_pos > unit_end + 3:
            continue
        if match.group("unit") in {"次", "段"} and not any(
            term in raw for term in {"连击", "附着层数"}
        ):
            continue
        raw_count = match.group("count")
        count = int(raw_count) if raw_count.isdigit() else _CHINESE_NUMBERS.get(raw_count)
        comparator = match.group("comparator") or "exact"
        if "及以上" in raw or comparator in {"至少", "不少于", "达到"}:
            operator = ">="
        elif comparator in {"至多", "最多", "不超过"}:
            operator = "<="
        elif comparator in {"已拥有", "处于"}:
            operator = ">="
        else:
            operator = "=="
        result.append(
            {
                "raw": raw,
                "subject": subject,
                "count": count,
                "unit": match.group("unit"),
                "operator": operator,
                "comparator": comparator,
            }
        )
    return result


def _split_condition(marker: str, clause: str) -> tuple[str, str, str]:
    role = "activation" if _ACTIVATION_RE.search(clause) else "resolution"
    if role == "activation":
        trigger = re.split(r"可以发动|可发动|才能发动", clause, maxsplit=1)[0]
        return trigger.strip(" ，,"), "", role

    match = _RESULT_SPLIT_RE.search(clause)
    if match:
        return clause[: match.start()].strip(" ，,"), clause[match.end() :].strip(), role
    return clause.strip(" ，,"), "", role


def _conditions(description: str) -> list[ConditionAnalysis]:
    result: list[ConditionAnalysis] = []
    for match in _CONDITION_START_RE.finditer(description):
        marker, clause = match.group(1), match.group(2).strip()
        trigger_text, result_text, role = _split_condition(marker, clause)
        result.append(
            ConditionAnalysis(
                marker=marker,
                text=clause,
                trigger_text=trigger_text,
                result_text=result_text,
                role=role,
                trigger_effects=_effect_values(trigger_text),
                result_effects=_effect_values(result_text),
                stack_rules=_stack_rules(clause),
                consumes_all=[m.group("subject").strip() for m in _CONSUME_ALL_RE.finditer(clause)],
                repeats=[m.group("subject").strip() for m in _REPEAT_RE.finditer(clause)],
                is_last_resource=bool(_SPECIAL_LAST_RE.search(clause)),
            )
        )
    return result


def _skill_tables(tables: list[list[list[str]]]) -> tuple[list[list[str]], list[list[str]]]:
    rank_table: list[list[str]] = []
    material_table: list[list[str]] = []
    for table in tables:
        if not table:
            continue
        first = table[0][0] if table[0] else ""
        if first == "技能等级" and any("材料消耗" in cell for row in table for cell in row):
            material_table = table
        elif first == "技能等级":
            rank_table = table
    return rank_table, material_table


def _combat_widget(document: dict) -> dict | None:
    for group in document.get("chapterGroup", []):
        for widget in group.get("widgets", []):
            if widget.get("title") == "战斗技能":
                return document.get("widgetCommonMap", {}).get(widget.get("id"))
    return None


def _load_current_characters() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in CHARACTER_SKILLS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        result[str(data.get("name") or path.stem)] = data
    return result


def _normalize_name(name: str) -> str:
    if name.startswith("管理员"):
        return "管理员"
    return name


def _enhancements(skill: dict) -> list[dict]:
    values = skill.get("enhancements") or []
    if not values and skill.get("enhancement"):
        values = [skill["enhancement"]]
    return values


def _analyze_operator(detail_path: Path, current_characters: dict[str, dict]) -> OperatorAnalysis:
    payload = json.loads(detail_path.read_text(encoding="utf-8"))
    item = payload.get("data", {}).get("item", {})
    item_id = str(item.get("itemId") or "")
    name = str(item.get("name") or detail_path.stem)
    document = item.get("document") or {}
    widget = _combat_widget(document)
    analysis = OperatorAnalysis(
        item_id=item_id,
        name=name,
        detail_file=detail_path.name,
        preview_only=widget is None,
    )
    if widget is None:
        analysis.review_flags.append("官方 WIKI 当前为前瞻页，没有完整战斗技能组件")
        return analysis

    current_character = current_characters.get(_normalize_name(name)) or {}
    current_by_name = {
        str(skill.get("name")): skill
        for skill in current_character.get("skills") or []
    }
    document_map = document.get("documentMap") or {}

    for tab in widget.get("tabList", []):
        tab_data = widget.get("tabDataMap", {}).get(tab.get("tabId"), {})
        intro = tab_data.get("intro") or {}
        skill_name = str(intro.get("name") or "")
        description = _document_text(document_map, intro.get("description"))
        rank_table, material_table = _skill_tables(
            _document_tables(document_map, tab_data.get("content")),
        )
        current_skill = current_by_name.get(skill_name) or {}
        current_effects = [
            str(effect.get("effect_id"))
            for effect in current_skill.get("effects") or []
            if isinstance(effect, dict) and effect.get("effect_id")
        ]
        conditions = _conditions(description)
        skill = SkillAnalysis(
            name=skill_name,
            skill_type=str(intro.get("type") or ""),
            description=description,
            rank_table=rank_table,
            material_table=material_table,
            conditions=conditions,
            description_effects=_effect_values(description),
            stack_rules=_stack_rules(description),
            current_skill_id=current_skill.get("skill_id"),
            current_effects=current_effects,
            current_enhancement_count=len(_enhancements(current_skill)),
        )

        current_description = re.sub(r"\s+", "", str(current_skill.get("description") or ""))
        wiki_description = re.sub(r"\s+", "", description)
        if not current_skill:
            skill.review_flags.append("本地角色技能数据中找不到同名技能")
        elif current_description != wiki_description:
            skill.review_flags.append("本地技能描述与官方 WIKI 当前描述不完全一致")
        if len(conditions) > 1 and skill.current_enhancement_count < len(
            [condition for condition in conditions if condition.role == "resolution"]
        ):
            skill.review_flags.append("描述含多个结算条件，本地 enhancements 数量可能不足")
        if any(condition.stack_rules for condition in conditions):
            skill.review_flags.append("包含特殊层数/次数阈值，需核对 operator、count 与消费方向")
        if any(condition.consumes_all for condition in conditions):
            skill.review_flags.append("包含“消耗所有”语义，应使用明确的清除/消费 effect ID")
        if any(condition.repeats for condition in conditions):
            skill.review_flags.append("包含动态“再次施加该效果”语义，不能静态猜测单一元素")
        analysis.skills.append(skill)

    return analysis


def _markdown(operators: list[OperatorAnalysis], snapshot: str) -> str:
    lines = [
        "# 官方 WIKI 技能自动分析报告",
        "",
        f"- 快照：`{snapshot}`",
        f"- 干员：{len(operators)}",
        f"- 技能：{sum(len(operator.skills) for operator in operators)}",
        f"- 前瞻页：{sum(operator.preview_only for operator in operators)}",
        "",
        "## 待审阅摘要",
        "",
        "| 干员 | 技能 | 条件数 | 层数规则 | 当前强化数 | 提示 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for operator in operators:
        if operator.preview_only:
            lines.append(f"| {operator.name} | — | 0 | 0 | 0 | {'；'.join(operator.review_flags)} |")
            continue
        for skill in operator.skills:
            flags = "；".join(skill.review_flags)
            if flags:
                lines.append(
                    f"| {operator.name} | {skill.name} | {len(skill.conditions)} | "
                    f"{len(skill.stack_rules)} | {skill.current_enhancement_count} | {flags} |"
                )

    lines.extend(["", "## 多条件与特殊层数详情", ""])
    for operator in operators:
        selected = [
            skill for skill in operator.skills
            if len(skill.conditions) > 1 or skill.stack_rules or skill.review_flags
        ]
        if not selected:
            continue
        lines.append(f"### {operator.name}")
        lines.append("")
        for skill in selected:
            lines.append(f"#### {skill.name}（{skill.skill_type}）")
            lines.append("")
            lines.append(f"> {skill.description.replace(chr(10), '<br>')}")
            lines.append("")
            if skill.conditions:
                lines.append("| # | 角色 | 触发条件 | 结果 | 条件效果候选 | 结果效果候选 | 层数/消费 |")
                lines.append("|---:|---|---|---|---|---|---|")
                for index, condition in enumerate(skill.conditions, start=1):
                    special = []
                    special.extend(rule["raw"] for rule in condition.stack_rules)
                    special.extend(f"消耗所有{value}" for value in condition.consumes_all)
                    special.extend(f"再次施加{value}" for value in condition.repeats)
                    if condition.is_last_resource:
                        special.append("最后资源触发")
                    lines.append(
                        f"| {index} | {condition.role} | {condition.trigger_text} | {condition.result_text} | "
                        f"{', '.join(condition.trigger_effects)} | {', '.join(condition.result_effects)} | "
                        f"{', '.join(special)} |"
                    )
            if skill.rank_table:
                lines.append("")
                lines.append("技能数值表已写入 JSON；关键字段：")
                for row in skill.rank_table[1:]:
                    if row:
                        lines.append(f"- **{row[0]}**：{' / '.join(row[1:])}")
            if skill.review_flags:
                lines.append("")
                lines.extend(f"- ⚠ {flag}" for flag in skill.review_flags)
            lines.append("")
    return "\n".join(lines)


def _latest_snapshot() -> str:
    latest = SNAPSHOT_ROOT / "latest.json"
    if not latest.exists():
        raise FileNotFoundError("未找到快照；请先运行 capture_skland_operator_details.py")
    return str(json.loads(latest.read_text(encoding="utf-8"))["snapshot"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=None, help="快照目录名，默认读取 latest.json")
    parser.add_argument("--operator", action="append", default=[], help="仅分析指定干员，可重复")
    parser.add_argument("--out", default=None, help="输出目录，默认 <snapshot>/analysis")
    parser.add_argument("--stdout", action="store_true", help="同时输出 Markdown 到终端")
    args = parser.parse_args()

    snapshot = args.snapshot or _latest_snapshot()
    snapshot_dir = SNAPSHOT_ROOT / snapshot
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        parser.error(f"快照不存在：{snapshot_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected_names = set(args.operator)
    current_characters = _load_current_characters()
    operators: list[OperatorAnalysis] = []
    for entry in manifest.get("operators", []):
        name = str(entry.get("name") or "")
        if selected_names and name not in selected_names:
            continue
        detail_path = snapshot_dir / str(entry["detail_file"])
        operators.append(_analyze_operator(detail_path, current_characters))

    out_dir = Path(args.out).resolve() if args.out else snapshot_dir / "analysis"
    if not out_dir.is_relative_to(ROOT):
        parser.error("--out 必须位于仓库内")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "snapshot": snapshot,
        "operator_count": len(operators),
        "skill_count": sum(len(operator.skills) for operator in operators),
        "preview_count": sum(operator.preview_only for operator in operators),
        "effect_term_count": len(EFFECT_TERMS),
        "defined_effect_ids": [effect.value for effect in EffectType],
        "operators": [asdict(operator) for operator in operators],
    }
    json_path = out_dir / "operator_skill_analysis.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = _markdown(operators, snapshot)
    markdown_path = out_dir / "operator_skill_review.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    summary = {
        "snapshot": snapshot,
        "operators": len(operators),
        "skills": payload["skill_count"],
        "conditions": sum(len(skill.conditions) for operator in operators for skill in operator.skills),
        "multi_condition_skills": sum(
            len(skill.conditions) > 1 for operator in operators for skill in operator.skills
        ),
        "stack_rule_skills": sum(bool(skill.stack_rules) for operator in operators for skill in operator.skills),
        "flagged_skills": sum(bool(skill.review_flags) for operator in operators for skill in operator.skills),
        "json": json_path.relative_to(ROOT).as_posix(),
        "markdown": markdown_path.relative_to(ROOT).as_posix(),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.stdout:
        print("\n" + markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
