"""静态伤害基准计算器（第一阶段口径）。

口径（见 docs/dev/combat-system/ROTATION_REQUIREMENTS.md 与用户规划）：
- 角色 90 级 / 技能专精3满级（rank_stats 末列）/ 武器 90 级 + 武器技能 Rank9（官方推荐
  基质解锁）/ 装备 4 件（护甲+护手+配件×2）LV70 满精锻（精锻3）/ 3件套组效果。
- 标准假人：DEF=0、RES=0、无失衡、无连击、无队友增益（防御倍率=抗性倍率=1）。
- A 层裸伤害：仅统计「无条件」属性词条（武器技能/套组效果中含触发词的句子不计）。
- B 层（技能自身状态）：暂不实现，缺口见输出 note。

伤害公式（docs/dev/combat-system/DAMAGE_FORMULA.md，标准假人下简化）：
  攻击力 = ((干员攻击 + 武器攻击) × (1+攻击%) + 攻击固定)
           × (1 + 0.005×主属性 + 0.002×副属性)
           [主能力优先取官方 WIKI 标签，缺失时按元素推断（物理→力量、其他→智识）；
            副能力缺官方标注时不计对应项]
  非暴击伤害 = 攻击力 × 总倍率 × (1 + 元素伤害% + 技能类型伤害% + 所有技能伤害% + 所有类型伤害%)
  暴击期望   = 非暴击伤害 × (1 + 暴击率 × 暴击伤害)     （基础 5% / 50%）

用法：
    python scripts/skill-data/compute_damage_baseline.py                # 全角色
    python scripts/skill-data/compute_damage_baseline.py --char puqiena # 单角色 dry-run（打印 trace/排行）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.data.operator_names import _normalize_name  # noqa: E402

DATA_DIR = ROOT / "assets/data"
SNAP_ROOT = ROOT / "tools/wiki_catalog/operator_details"
ZH_CN_DIR = ROOT / "tools/wiki_catalog/zh_cn"

STAT_FLAT = ("生命值", "生命", "攻击力", "防御力", "力量", "敏捷", "智识", "意志", "源石技艺强度")
TRIGGER_WORDS = ("当", "后，", "后,", "时，", "时,", "使", "如果", "若", "释放", "施放", "击败",
                 "触发", "低于", "高于", "施加", "恢复", "返还", "下一次", "每次", "期间")
ELEMENTS = ("物理", "灼热", "寒冷", "电磁", "自然")
SKILL_TYPE_BUCKETS = {
    "普通攻击": "normal_attack_dmg",
    "战技": "skill_dmg",
    "连携技": "combo_dmg",
    "终结技": "ult_dmg",
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。；;\n]", text)
    return [p.strip() for p in parts if p.strip()]


def _is_unconditional(sentence: str) -> bool:
    return not any(w in sentence for w in TRIGGER_WORDS)


def _parse_stat_clause(sentence: str) -> dict | None:
    """把「力量+41」「攻击力+15%」「所有技能伤害+20%」解析为结构化词条。"""
    s = sentence.replace("装备者", "").strip()
    s = re.sub(r"^使自身", "", s)
    # 承伤/防御类（记录但不进伤害面板）
    m = re.fullmatch(r"受到的所有类型伤害-(\d+(?:\.\d+)?)%", s)
    if m:
        return {"kind": "taken_reduction", "value": float(m.group(1))}
    m = re.fullmatch(r"受到的(物理|法术|灼热|寒冷|电磁|自然)伤害-(\d+(?:\.\d+)?)%", s)
    if m:
        return {"kind": "taken_reduction", "value": float(m.group(2))}
    # 平面属性
    m = re.fullmatch(r"(生命值|生命|攻击力|防御力|力量|敏捷|智识|意志|源石技艺强度)\+(\d+)", s)
    if m:
        key = "生命值" if m.group(1) == "生命" else m.group(1)
        return {"kind": "flat", "stat": key, "value": int(m.group(2))}
    # 百分比词条
    patterns = [
        (r"攻击力\+(\d+(?:\.\d+)?)%", "atk_pct"),
        (r"所有技能伤害\+(\d+(?:\.\d+)?)%", "all_skill_dmg"),
        (r"所有类型伤害\+(\d+(?:\.\d+)?)%", "all_damage"),
        (r"造成的所有类型伤害\+(\d+(?:\.\d+)?)%", "all_damage"),
        (r"暴击率\+(\d+(?:\.\d+)?)%", "crit_rate"),
        (r"暴击伤害\+(\d+(?:\.\d+)?)%", "crit_dmg"),
        (r"治疗效率\+(\d+(?:\.\d+)?)%", "heal_eff"),
        (r"终结技充能效率\+(\d+(?:\.\d+)?)%", "ult_charge"),
        (r"连携技冷却缩减\+(\d+(?:\.\d+)?)%", "combo_cd"),
        (r"失衡效率加成\+(\d+(?:\.\d+)?)%", "stagger_eff"),
        (r"最大生命值\+(\d+(?:\.\d+)?)%", "hp_pct"),
    ]
    for pat, key in patterns:
        m = re.fullmatch(pat, s)
        if m:
            return {"kind": "pct", "stat": key, "value": float(m.group(1))}
    for el in ELEMENTS:
        m = re.fullmatch(rf"{el}伤害\+(\d+(?:\.\d+)?)%", s)
        if m:
            return {"kind": "pct", "stat": f"elem_{el}", "value": float(m.group(1))}
    return None


_SET_PREFIX = re.compile(r"^\d件套组效果：")


def _collect_stats(text: str, mods: dict, trace: list[str], source: str) -> None:
    for sentence in _sentences(_SET_PREFIX.sub("", text)):
        # 条件判断在整句级别：句中任何位置出现触发词，整句（含其逗号子句）都不计入，
        # 避免「当…施加燃烧后，灼热伤害+50%」的后段被误判为无条件。
        if not _is_unconditional(sentence):
            continue
        for chunk in re.split(r"[，,]", sentence):
            mod = _parse_stat_clause(chunk)
            if mod:
                mods.append(mod)
                trace.append(f"    [{source}] {chunk} -> {mod}")


def _merge(mods: list[dict]) -> dict:
    merged: dict[str, float] = {}
    for m in mods:
        if m["kind"] == "flat":
            key = f"flat_{m['stat']}"
            merged[key] = merged.get(key, 0) + m["value"]
        elif m["kind"] == "pct":
            key = f"pct_{m['stat']}"
            merged[key] = merged.get(key, 0) + m["value"]
        elif m["kind"] == "taken_reduction":
            merged["taken_reduction"] = merged.get("taken_reduction", 0) + m["value"]
    return merged


def _weapon_rank9_mods(weapon: dict, mods: dict, trace: list[str]) -> None:
    for skill in weapon.get("weapon_skills") or []:
        ranks = skill.get("ranks") or {}
        # Rank 9（或最高键）
        rank_keys = sorted(ranks, key=lambda r: int(re.search(r"\d+", r).group()) if re.search(r"\d+", r) else 0)
        top = rank_keys[-1] if rank_keys else None
        if not top:
            continue
        for _name, text in ranks[top].items():
            for sentence in _sentences(str(text)):
                if not _is_unconditional(sentence):
                    continue
                mod = _parse_stat_clause(sentence)
                if mod:
                    mods.append(mod)
                    trace.append(f"    [武器技能 {top}] {sentence} -> {mod}")


def _piece_stat(piece: dict, stat: str) -> int | None:
    """精锻3 优先（其值为最终总值），否则 LV70 基础值。"""
    ref = piece.get("refinement_max") or {}
    if stat in ref:
        m = re.fullmatch(r"\+?(\d+)", str(ref[stat]))
        if m:
            return int(m.group(1))
    lv = piece.get("lv70_stats") or {}
    if stat in lv:
        m = re.fullmatch(r"\+?(\d+)", str(lv[stat]))
        if m:
            return int(m.group(1))
    return None


# 战技完整倍率覆盖表：部分角色的战技伤害不止单击倍率（召唤物/多段），
# 单击倍率会严重低估其输出，需按官方机制补全。
# 依据 wiki 技能数据表（专精 3）与机制描述：
# - 庄方宜「惊霆诀」：消耗导电生成至多 3 柄青霆剑，依次雷击目标（45%/击），
#   最后一击造成 6 倍伤害 → 完整倍率 = 45% x (2 + 6) = 360%。
#   按「满导电常态 3 柄（生成上限）」建模；消耗每级导电的额外倍率（9%/级）
#   属条件词条不计入。wiki 快照：operator_details/1132_庄方宜。
# 赛希战技为治疗/增幅（无伤害倍率），不在此列。
_SKILL_FULL_MULTIPLIER_OVERRIDES: dict[tuple[str, str], float] = {
    ("zhuangfy", "zhuangfy_skill"): 360.0,
}


def _skill_multiplier(skill: dict) -> tuple[float, float, list[str]]:
    """从 rank_stats 提取（总倍率%, 总失衡值, 条件行说明）。

    - 伤害行：标签含「倍率」或「伤害」，且不含 治疗/效果/失衡；
      「处决」「终结技期间」等条件变体行不计入裸伤害（C 层再算）。
    - 数值：带 % 直接取；纯小数（如 1.3）按倍率小数 ×100；纯大数按百分比。
    """
    mult = 0.0
    stagger = 0.0
    conditional: list[str] = []

    def _parse_pct(raw: str) -> float | None:
        raw = raw.strip()
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
        if m:
            return float(m.group(1))
        m = re.fullmatch(r"(\d+(?:\.\d+)?)", raw)
        if m:
            v = float(m.group(1))
            return v * 100 if v < 20 else v
        return None

    for row in (skill.get("rank_stats") or {}).get("rows") or []:
        label = str(row.get("label") or "")
        values = row.get("values") or []
        if not values:
            continue
        last = str(values[-1])
        flat_label = label.replace("/", "").replace(" ", "")
        if "治疗" in label or "效果" in label or "技力" in label or "能量" in label \
                or any(word in label for word in ("提升", "提高", "增加", "暴击")) \
                or "时长" in label or "时间" in label or "消耗" in label:
            continue
        if "失衡" in label:
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", last)]
            if nums:
                stagger += nums[-1]
            continue
        if "倍率" in label or "伤害" in label or "攻击" in label:
            if "处决" in flat_label or "终结技期间" in flat_label:
                conditional.append(f"{label}: {last}")
                continue
            v = _parse_pct(last)
            if v is not None:
                mult += v
            else:
                conditional.append(f"{label}: {last}")
    return mult, stagger, conditional


EQUIP_PCT_STAT_MAP = {
    "物理伤害加成": ["elem_物理"],
    "灼热伤害加成": ["elem_灼热"],
    "寒冷伤害加成": ["elem_寒冷"],
    "电磁伤害加成": ["elem_电磁"],
    "自然伤害加成": ["elem_自然"],
    "寒冷和电磁伤害加成": ["elem_寒冷", "elem_电磁"],
    "灼热和自然伤害加成": ["elem_灼热", "elem_自然"],
    "法术伤害加成": ["elem_灼热", "elem_寒冷", "elem_电磁", "elem_自然"],
    "普通攻击伤害加成": ["normal_attack_dmg"],
    "战技伤害加成": ["skill_dmg"],
    "连携技伤害加成": ["combo_dmg"],
    "终结技伤害加成": ["ult_dmg"],
    "所有技能伤害加成": ["all_skill_dmg"],
    "暴击率": ["crit_rate"],
    "暴击率加成": ["crit_rate"],
    "暴击伤害": ["crit_dmg"],
    "治疗效率加成": ["heal_eff"],
    "终结技充能效率": ["ult_charge"],
    "终结技充能效率加成": ["ult_charge"],
    # 插板/护板类装备的「主能力」词条：按装备者主能力属性取百分比，
    # 仅作用于主能力总值（compute_character 内乘区），副能力不吃
    "主能力": ["primary"],
    # 对失衡目标伤害加成 / 全伤害减免：条件/防御词条，A 层不计，另行记录
}


def _piece_pct_mods(piece: dict) -> list[tuple[str, float]]:
    """装备精锻/基础属性中的百分比词条（含条件词条，由调用方过滤）。"""
    out: list[tuple[str, float]] = []
    stats = {**(piece.get("lv70_stats") or {}), **(piece.get("refinement_max") or {})}
    for stat, value in stats.items():
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(value))
        if not m:
            continue
        v = float(m.group(1))
        if stat in EQUIP_PCT_STAT_MAP:
            for bucket in EQUIP_PCT_STAT_MAP[stat]:
                out.append((bucket, v))
        elif stat == "对失衡目标伤害加成":
            out.append(("vs_stagger_dmg", v))
        elif stat == "全伤害减免":
            out.append(("dmg_reduction", v))
    return out


def _operator_primary_stats(snap_dir: Path) -> dict[str, str]:
    """干员 itemId → 主能力（力量/敏捷/智识），来自官方 tagIds 10207 组。"""
    tags: dict[str, str] = {}

    def walk(nodes, group: str) -> None:
        for n in nodes or []:
            nid = str(n.get("id") or "")
            if group == "主能力" and nid:
                tags[nid] = str(n.get("name") or "")
            walk(n.get("children"), str(n.get("name") or group))

    for f in [snap_dir / "catalog.json", *sorted(ZH_CN_DIR.glob("*/m1_s1.json"))]:
        if not f.is_file():
            continue
        payload = _load(f)
        for c in payload.get("data", {}).get("catalog", []):
            for sub in c.get("typeSub", []):
                if str(sub.get("id")) == "1":
                    walk(sub.get("filterTagTree") or [], "")
    result: dict[str, str] = {}
    for f in sorted(snap_dir.glob("details/*.json")):
        payload = _load(f)
        item = payload["data"]["item"]
        for tid in item.get("tagIds") or []:
            if str(tid) in tags:
                result[str(item.get("itemId"))] = tags[str(tid)]
    return result


_STAT_NAMES = ("力量", "敏捷", "智识", "意志")
_ABILITY_PAIR = re.compile(
    r"主能力\s*\n(" + "|".join(_STAT_NAMES) + r")\s*\n副能力\s*\n(" + "|".join(_STAT_NAMES) + r")"
)


def _operator_secondary_stats(snap_dir: Path) -> dict[str, str]:
    """干员 itemId → 副能力（力量/敏捷/智识/意志）。

    官方 WIKI 干员页属性表附注「金色=主能力、黑色=副能力」，渲染文本中
    以「主能力\\nX\\n副能力\\nY」相邻行出现；副能力不与主能力重复。
    """
    result: dict[str, str] = {}
    for f in sorted((snap_dir / "rendered_text").glob("*.txt")):
        item_id = f.stem.split("_", 1)[0]
        m = _ABILITY_PAIR.search(f.read_text(encoding="utf-8"))
        if m and m.group(1) != m.group(2):
            result[item_id] = m.group(2)
    return result


def compute_character(key: str, char: dict, build: dict, weapons: dict, equipments: dict,
                      primary_map: dict[str, str], wiki_item_ids: dict[str, list[str]],
                      secondary_map: dict[str, str] | None = None) -> dict:
    trace: list[str] = []
    name = str(char.get("name") or key)
    element = str(char.get("element") or "")
    mods: list[dict] = []

    base_rows = (char.get("base_stats") or {}).get("rows") or {}
    levels = (char.get("base_stats") or {}).get("levels") or []
    base = {k: float(v[-1]) for k, v in base_rows.items() if v}
    trace.append(f"  基础面板（{levels[-1] if levels else '90级'}）: {base}")

    # 武器
    weapon_name = (build.get("weapon") or {}).get("name")
    weapon_atk = 0
    if weapon_name and weapon_name in weapons:
        w = weapons[weapon_name]
        ba = w.get("base_attack") or {}
        if ba.get("values"):
            weapon_atk = float(ba["values"][-1])
        _weapon_rank9_mods(w, mods, trace)
    trace.append(f"  武器: {weapon_name}（满级攻击 {weapon_atk:.0f}，武器技能 Rank9 词条已计入）")

    # 装备
    pieces = (build.get("equipment") or {}).get("pieces") or []
    for piece_name in pieces:
        piece = equipments.get(piece_name or "")
        if not piece:
            continue
        for stat in STAT_FLAT:
            v = _piece_stat(piece, stat)
            if v:
                mods.append({"kind": "flat", "stat": stat, "value": v})
        for bucket, v in _piece_pct_mods(piece):
            if bucket in ("vs_stagger_dmg", "dmg_reduction"):
                # 条件/防御词条：A 层不进伤害加成，记录在 trace
                trace.append(f"    [装备·条件] {piece_name} {bucket}+{v}%（A 层不计）")
                continue
            mods.append({"kind": "pct", "stat": bucket, "value": v})
        trace.append(f"    [装备] {piece_name}（{piece.get('set')}）: {piece.get('refinement_max') or piece.get('lv70_stats')}")

    # 套组效果（主套组 3 件）
    set_main = (build.get("equipment") or {}).get("set_main")
    if set_main:
        set_pieces = [name for name in pieces if name and equipments.get(name, {}).get("set") == set_main]
        if len(set_pieces) >= 3:
            for piece_name in set_pieces:
                piece = equipments[piece_name]
                if piece.get("set_effect"):
                    _collect_stats(piece["set_effect"], mods, trace, f"套组 {set_main}")
                    break

    merged = _merge(mods)
    trace.append(f"  汇总词条: {merged}")

    # 主能力优先官方标注，缺失时按元素回退；副能力只取官方标注
    wiki_item_id_candidates = wiki_item_ids.get(name, [])
    official_primary = next(
        (primary_map[item_id] for item_id in wiki_item_id_candidates if item_id in primary_map),
        None,
    )
    primary = official_primary or ("力量" if element == "物理" else "智识")
    primary_total = base.get(primary, 0) + merged.get(f"flat_{primary}", 0)
    primary_pct = merged.get("pct_primary", 0)
    if primary_pct:
        primary_total *= 1 + primary_pct / 100
    primary_source = "官方标签" if official_primary else "按元素推断"
    pct_note = f"，含主能力词条+{primary_pct:g}%" if primary_pct else ""
    trace.append(f"  主能力: {primary}（{primary_source}）总值 {primary_total:.0f}{pct_note}")
    secondary_stats = secondary_map or {}
    secondary = next(
        (secondary_stats[item_id] for item_id in wiki_item_id_candidates if item_id in secondary_stats),
        None,
    )
    secondary_total = 0.0
    if secondary and secondary != primary:
        secondary_total = base.get(secondary, 0) + merged.get(f"flat_{secondary}", 0)
        trace.append(f"  副能力: {secondary}（官方标注）总值 {secondary_total:.0f}")
    else:
        secondary = None
        trace.append("  副能力: 无官方标注，副属性系数项不计")

    # 攻击力
    atk_base = base.get("攻击力", 0) + weapon_atk
    atk_pct = merged.get("pct_atk_pct", 0) / 100
    atk_fixed = merged.get("flat_攻击力", 0)
    atk = (atk_base * (1 + atk_pct) + atk_fixed) * (
        1 + 0.005 * primary_total + 0.002 * secondary_total
    )
    trace.append(
        f"  攻击力 = ({atk_base:.0f} × {1 + atk_pct:.4f} + {atk_fixed:.0f})"
        f" × (1 + 0.005×{primary_total:.0f} + 0.002×{secondary_total:.0f}) = {atk:.1f}"
    )

    # 暴击
    crit_rate = 0.05 + merged.get("pct_crit_rate", 0) / 100
    crit_dmg = 0.50 + merged.get("pct_crit_dmg", 0) / 100

    # 面板
    hp_total = (base.get("生命值", 0) + merged.get("flat_生命值", 0)) * (1 + merged.get("pct_hp_pct", 0) / 100)
    panel = {
        "HP": round(hp_total, 1),
        "ATK": round(atk, 1),
        "DEF": base.get("防御力", 0) + merged.get("flat_防御力", 0),
        "力量": base.get("力量", 0) + merged.get("flat_力量", 0),
        "敏捷": base.get("敏捷", 0) + merged.get("flat_敏捷", 0),
        "智识": base.get("智识", 0) + merged.get("flat_智识", 0),
        "意志": base.get("意志", 0) + merged.get("flat_意志", 0),
        "暴击率": round(crit_rate, 4),
        "暴击伤害": round(crit_dmg, 4),
        "源石技艺强度": base.get("源石技艺强度", 0) + merged.get("flat_源石技艺强度", 0),
        "atk_pct": merged.get("pct_atk_pct", 0),
        "elem_dmg_pct": {el: merged.get(f"pct_elem_{el}", 0) for el in ELEMENTS},
        "all_skill_dmg": merged.get("pct_all_skill_dmg", 0),
        "all_damage": merged.get("pct_all_damage", 0),
        "heal_eff": merged.get("pct_heal_eff", 0),
        "ult_charge": merged.get("pct_ult_charge", 0),
    }

    # 技能伤害（A 层裸伤害）
    skill_results = []
    for skill in char.get("skills") or []:
        mult, stagger, conditional = _skill_multiplier(skill)
        stype = str(skill.get("skill_type") or "")
        bucket = SKILL_TYPE_BUCKETS.get(stype)
        bonus = merged.get("pct_all_damage", 0) + merged.get("pct_all_skill_dmg", 0)
        if bucket:
            bonus += merged.get(f"pct_{bucket}", 0)
        bonus += merged.get(f"pct_elem_{element}", 0)
        dmg_mult = 1 + bonus / 100
        non_crit = atk * (mult / 100) * dmg_mult
        crit_expect = non_crit * (1 + crit_rate * crit_dmg)
        entry = {
            "skill_id": skill.get("skill_id"),
            "name": skill.get("name"),
            "type": stype,
            "multiplier_pct": round(mult, 1),
            "stagger": stagger,
            "bonus_pct": round(bonus, 1),
            "non_crit": round(non_crit, 1),
            "crit_expect": round(crit_expect, 1),
            "conditional_rows": conditional,
        }
        full_mult = _SKILL_FULL_MULTIPLIER_OVERRIDES.get((key, str(skill.get("skill_id") or "")))
        if full_mult is not None and full_mult != mult:
            full_non_crit = atk * (full_mult / 100) * dmg_mult
            entry["full_multiplier_pct"] = round(full_mult, 1)
            entry["full_non_crit"] = round(full_non_crit, 1)
            entry["full_expect"] = round(full_non_crit * (1 + crit_rate * crit_dmg), 1)
            trace.append(
                f"  技能完整倍率覆盖: {skill.get('name')} {mult:.0f}%→{full_mult:.0f}%"
                f"（召唤物/多段机制，依据 wiki 技能表）"
            )
        skill_results.append(entry)

    # 循环期望（排序口径）：一次标准循环 = 战技完整伤害（含召唤物/多段）
    # + 连携 + 2 次普攻（对应自动轴 12.5s 填充段的站场普攻抽样）。
    # 仅用于 skill_rotation 排序，单技能明细仍以上表为准。
    def _best_expect(skill_type: str) -> float:
        best = 0.0
        for s in skill_results:
            if s.get("type") != skill_type:
                continue
            value = s.get("full_expect") or s.get("crit_expect") or s.get("non_crit") or 0
            try:
                best = max(best, float(value))
            except (TypeError, ValueError):
                continue
        return best

    cycle_expect = round(_best_expect("战技") + _best_expect("连携技") + 2 * _best_expect("普通攻击"), 1)
    trace.append(
        f"  循环期望: {cycle_expect:.0f}"
        f"（战技 {_best_expect('战技'):.0f} + 连携 {_best_expect('连携技'):.0f}"
        f" + 2x普攻 {_best_expect('普通攻击') * 2:.0f}）"
    )

    return {
        "character": name,
        "key": key,
        "element": element,
        "primary_stat": primary,
        "secondary_stat": secondary,
        "panel": panel,
        "cycle_expect": cycle_expect,
        "skills": skill_results,
        "build": {
            "weapon": weapon_name,
            "matrix": (build.get("matrix") or {}).get("name"),
            "set_main": set_main,
            "set_off": (build.get("equipment") or {}).get("set_off"),
            "pieces": pieces,
        },
        "trace": trace,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--char", default=None, help="仅计算指定角色（文件名，如 puqiena）；默认只打印不写回")
    parser.add_argument("--out", default=None, help="输出路径（必须位于 assets/data 下；默认 damage_baseline.json）")
    parser.add_argument("--snapshot", help="operator_details 快照目录名（默认最新）")
    args = parser.parse_args()

    # 输入/输出路径防御：--char 仅允许文件名字符，--out 仅允许写入 assets/data 下
    # （CLI 参数来自外部，直接拼路径属 SonarCloud 路径注入热点）
    if args.char and not re.fullmatch(r"[a-z0-9_]+", args.char):
        print(f"错误：--char 仅接受小写字母/数字/下划线（文件名），收到: {args.char!r}")
        return 2
    if args.out:
        out_path = Path(args.out).resolve()
        if out_path.parent != DATA_DIR.resolve():
            print(f"错误：--out 必须指向 {DATA_DIR} 目录下，收到: {args.out}")
            return 2
    else:
        out_path = DATA_DIR / "damage_baseline.json"

    candidates = sorted(p for p in SNAP_ROOT.iterdir() if p.is_dir()) if SNAP_ROOT.is_dir() else []
    snap_dir = SNAP_ROOT / args.snapshot if args.snapshot else (candidates[-1] if candidates else SNAP_ROOT)
    if not list(snap_dir.glob("details/*.json")) or not list(snap_dir.glob("rendered_text/*.txt")):
        print(f"快照缺少 details/*.json 或 rendered_text/*: {snap_dir}", file=sys.stderr)
        return 1

    weapons = _load(DATA_DIR / "weapons.json")
    equipments = _load(DATA_DIR / "equipments.json")
    primary_map = _operator_primary_stats(snap_dir)
    secondary_map = _operator_secondary_stats(snap_dir)

    # wiki itemId 反查表（干员名 → 全部 itemId）；同名角色可能对应多个 WIKI 条目。
    wiki_item_ids: dict[str, list[str]] = {}
    for f in sorted(snap_dir.glob("details/*.json")):
        payload = _load(f)
        item = payload["data"]["item"]
        name = _normalize_name(str((item.get("brief") or {}).get("name") or "").strip())
        if name:
            wiki_item_ids.setdefault(name, []).append(str(item.get("itemId")))

    results = []
    for path in sorted((DATA_DIR / "character_skills").glob("*.json")):
        if args.char and path.stem != args.char:
            continue
        char = _load(path)
        build_path = DATA_DIR / "character_builds" / f"{path.stem}.json"
        build = _load(build_path) if build_path.exists() else {}
        results.append(compute_character(path.stem, char, build, weapons, equipments,
                                         primary_map, wiki_item_ids, secondary_map))

    text = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    if args.char and not args.out:
        # --char 是调试模式：单角色结果写回默认文件会把全量基准覆盖成 1 个角色，
        # 故默认 dry-run（计算 + 打印 trace/排行，不落盘）；需要落盘时显式 --out
        print("提示：--char 调试模式默认不写回（如需落盘请显式 --out 指定单角色文件）")
        for r in results:
            print(f"\n== {r['character']} 计算过程 ==")
            for line in r.get("trace") or []:
                print(line)
    else:
        out_path.write_bytes(text.encode("utf-8").replace(b"\r\n", b"\n"))
        print(f"已写入 {out_path}（{len(results)} 个角色）")

    # 排行：以「战技 暴击期望」为主指标
    def key_metric(r):
        best = 0.0
        for s in r["skills"]:
            if s["type"] == "战技":
                best = max(best, s["crit_expect"])
        return best

    print()
    print(f"{'角色':<8} {'ATK':>7} {'暴击率':>6} {'战技倍率':>8} {'战技期望':>9}")
    for r in sorted(results, key=key_metric, reverse=True):
        best = max((s for s in r["skills"] if s["type"] == "战技"),
                   key=lambda s: s["crit_expect"], default=None)
        if best:
            print(f"{r['character']:<8} {r['panel']['ATK']:>7.0f} {r['panel']['暴击率']*100:>5.1f}% "
                  f"{best['multiplier_pct']:>7.1f}% {best['crit_expect']:>9.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
