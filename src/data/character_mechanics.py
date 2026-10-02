"""角色机制语义层：从当前 character_skills 快照提取不能压成单个战技的核心机制。

这一层只保存快照明确支持的事实，不猜实战状态。运行时/规划器可以据此决定：
- 是否允许使用旧的“每角色一个战技 CastOption”模型；
- 一个角色有哪些私有资源、阶段和主控持续窗口；
- 后续二进制导出需要保留哪些语义，而不是只保留伤害数值。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILLS_DIR = _ROOT / "assets" / "data" / "character_skills"


@dataclass(frozen=True)
class MechanicResource:
    key: str
    label: str
    maximum: int | None = None


@dataclass(frozen=True)
class MechanicTransition:
    action: str
    phase: str
    next_phase: str | None = None
    sp_gate: float | None = None
    sp_cost: float | None = None
    sp_refund: float = 0.0
    requires: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    repeats: int = 1
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CharacterMechanic:
    key: str
    name: str
    archetype: str
    generic_cycle_safe: bool
    resources: tuple[MechanicResource, ...] = ()
    transitions: tuple[MechanicTransition, ...] = ()
    state_seconds: float | None = None
    forced_main_control_seconds: float | None = None
    evidence: tuple[str, ...] = ()

    @property
    def blocker_reason(self) -> str | None:
        if self.generic_cycle_safe:
            return None
        return f"{self.name}:{self.archetype}"


def _number(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(match.group()) if match else None


def _skill(payload: dict, skill_type: str) -> dict | None:
    for item in payload.get("skills") or ():
        if isinstance(item, dict) and item.get("skill_type") == skill_type:
            return item
    return None


def _last_stat(skill: dict | None, label: str) -> float | None:
    if not skill:
        return None
    for row in (skill.get("rank_stats") or {}).get("rows") or ():
        if row.get("label") != label:
            continue
        values = row.get("values") or ()
        return _number(values[-1]) if values else None
    return None


def _effect_count(skill: dict | None, effect_id: str) -> int | None:
    if not skill:
        return None
    values = []
    for effect in skill.get("effects") or ():
        if isinstance(effect, dict) and effect.get("effect_id") == effect_id:
            count = effect.get("count")
            if isinstance(count, (int, float)):
                values.append(int(count))
    for enhancement in skill.get("enhancements") or ():
        if not isinstance(enhancement, dict):
            continue
        for effect in enhancement.get("effects") or ():
            if isinstance(effect, dict) and effect.get("effect_id") == effect_id:
                count = effect.get("count")
                if isinstance(count, (int, float)):
                    values.append(int(count))
    positives = [value for value in values if value > 0]
    return max(positives) if positives else None


def _mifu(key: str, payload: dict) -> CharacterMechanic | None:
    battle = _skill(payload, "战技")
    link = _skill(payload, "连携技")
    ult = _skill(payload, "终结技")
    if not battle:
        return None
    text = battle.get("description") or ""
    phase1 = re.search(r"断云：消耗(\d+)技力，施放后返还(\d+)技力", text)
    phase2 = re.search(r"追形：消耗(\d+)技力", text)
    phase3 = re.search(r"开天：消耗(\d+)技力", text)
    shred = re.search(r"不少于(\d+)层", text)
    if not (phase1 and phase2 and phase3):
        return None
    p1_cost, p1_refund = map(float, phase1.groups())
    p2_cost = float(phase2.group(1))
    p3_cost = float(phase3.group(1))
    shred_layers = int(shred.group(1)) if shred else 3
    return CharacterMechanic(
        key=key,
        name=str(payload.get("name") or key),
        archetype="multi_stage_battle",
        generic_cycle_safe=False,
        resources=(MechanicResource("shred", "破防层数"),),
        transitions=(
            MechanicTransition(
                "battle", "断云", "追形", p1_cost, p1_cost, p1_refund,
                produces=("battle_phase:追形",),
                notes=("原生下一段替换技能",),
            ),
            MechanicTransition(
                "battle", "追形", "开天", p2_cost, p2_cost,
                requires=(f"shred>={shred_layers}",),
                consumes=(f"shred:{shred_layers}+",),
                produces=("battle_phase:开天", "STATUS_HEAVY_STRIKE"),
            ),
            MechanicTransition(
                "battle", "开天", "断云", p3_cost, p3_cost,
                notes=("伤害类型按猛击而非普通战技处理",),
            ),
            MechanicTransition(
                "link", "拳出无悔", "追形",
                requires=(f"shred>={shred_layers}",),
                produces=("VULN_PHYSICAL", "battle_phase:追形"),
                notes=((link or {}).get("description") or "",),
            ),
            MechanicTransition(
                "ult", "绝心", "追形",
                produces=("STATUS_HEAVY_HIT", "battle_phase:追形"),
                notes=((ult or {}).get("description") or "",),
            ),
        ),
        evidence=(
            f"断云 {p1_cost:g}SP -> 返还 {p1_refund:g}SP",
            f"追形 {p2_cost:g}SP；消耗至少 {shred_layers} 层破防才进入开天",
            f"开天 {p3_cost:g}SP",
            "连携/终结技后战技替换为追形",
        ),
    )


def _typhoeus(key: str, payload: dict) -> CharacterMechanic | None:
    battle = _skill(payload, "战技")
    link = _skill(payload, "连携技")
    ult = _skill(payload, "终结技")
    if not (battle and link and ult):
        return None
    text = battle.get("description") or ""
    shots = re.search(r"可施放(\d+)次空中普通攻击", text)
    convert = re.search(r"每(\d+)点启示可以转化为1枚猎矢", link.get("description") or "")
    if not (shots and convert):
        return None
    shot_count = int(shots.group(1))
    insight_per_arrow = int(convert.group(1))
    link_sign = _effect_count(link, "STACK_SIGN") or 0
    link_arrows = _effect_count(link, "STACK_HUNTING_ARROW") or 0
    ult_arrows = _effect_count(ult, "STACK_HUNTING_ARROW") or 0
    insight_cap = link_sign if link_sign > 0 else insight_per_arrow * max(link_arrows, 1)
    return CharacterMechanic(
        key=key,
        name=str(payload.get("name") or key),
        archetype="main_control_attack_channel",
        generic_cycle_safe=False,
        resources=(
            MechanicResource("insight", "启示", insight_cap or None),
            MechanicResource("hunting_arrow", "猎矢"),
            MechanicResource("air_shots", "浮空普攻次数", shot_count),
        ),
        transitions=(
            MechanicTransition(
                "battle", "进入浮空", "浮空射击",
                sp_gate=_last_stat(battle, "技力消耗"),
                sp_cost=_last_stat(battle, "技力消耗"),
                produces=("STATUS_HOVERING",),
                notes=(f"随后必须完成最多 {shot_count} 次空中普攻",),
            ),
            MechanicTransition(
                "normal", "浮空射击", "浮空射击",
                requires=("ATTACH_NATURAL",),
                consumes=("ATTACH_NATURAL:1", "STACK_HUNTING_ARROW:0..1"),
                produces=("STACK_SIGN:1",),
                repeats=shot_count,
            ),
            MechanicTransition(
                "link", "启示转猎矢", "浮空射击",
                requires=(f"STACK_SIGN:{insight_cap}",),
                consumes=(f"STACK_SIGN:{insight_cap}",),
                produces=(f"STACK_HUNTING_ARROW:{link_arrows}", "reset:air_shots"),
                notes=(f"{insight_per_arrow} 启示 -> 1 猎矢",),
            ),
            MechanicTransition(
                "ult", "冰山呼告", "浮空射击",
                produces=(f"STACK_HUNTING_ARROW:{ult_arrows}", "STATUS_HOVERING", "reset:air_shots"),
                notes=(f"终结技后还有 {shot_count} 次空中普攻",),
            ),
        ),
        evidence=(
            f"战技进入浮空并开放 {shot_count} 次空中普攻",
            "每次命中自然附着会消耗 1 层并获得 1 启示",
            f"启示满时连携转化为猎矢（{insight_per_arrow}:1）并重置空中普攻次数",
            f"终结技直接获得 {ult_arrows} 枚猎矢并重置浮空普攻",
        ),
    )


def _zhuang(key: str, payload: dict) -> CharacterMechanic | None:
    battle = _skill(payload, "战技")
    link = _skill(payload, "连携技")
    ult = _skill(payload, "终结技")
    if not (battle and link and ult):
        return None
    battle_text = battle.get("description") or ""
    per_cast = re.search(r"一次战技最多生成(\d+)柄青霆剑", battle_text)
    sword_cap = int(_last_stat(battle, "青霆剑数量限制") or 0) or None
    ult_duration = _last_stat(ult, "持续时间（秒）")
    if not per_cast:
        return None
    per_cast_cap = int(per_cast.group(1))
    return CharacterMechanic(
        key=key,
        name=str(payload.get("name") or key),
        archetype="consume_status_build_stack_burst",
        generic_cycle_safe=False,
        resources=(
            MechanicResource("conducting", "导电等级", 4),
            MechanicResource("qingting_sword", "青霆剑", sword_cap),
            MechanicResource("ult_state", "天理合真"),
        ),
        transitions=(
            MechanicTransition(
                "link", "一息万变", None,
                requires=("ATTACH_ELECTROMAGNETIC",),
                consumes=("ATTACH_ELECTROMAGNETIC:all",),
                produces=("STATUS_CONDUCTING:+1_or_apply",),
            ),
            MechanicTransition(
                "battle", "惊霆诀", None,
                sp_gate=_last_stat(battle, "技力消耗"),
                sp_cost=_last_stat(battle, "技力消耗"),
                consumes=("STATUS_CONDUCTING:all_if_present", "STACK_QINGTING_SWORD:all_on_attack"),
                produces=(f"STACK_QINGTING_SWORD:1..{per_cast_cap}",),
                notes=("导电等级决定本次倍率和生成剑数",),
            ),
            MechanicTransition(
                "ult", "万钧风雷", "天理合真",
                produces=("ult_state",),
                notes=("状态内战技/连携强化", "首次战技免费且不消耗导电并固定生成3剑"),
            ),
            MechanicTransition(
                "battle", "天理合真首次惊霆诀", None,
                sp_gate=0,
                sp_cost=0,
                requires=("ult_state", "first_battle_in_ult"),
                consumes=("STACK_QINGTING_SWORD:all_on_attack",),
                produces=("STACK_QINGTING_SWORD:3", "ATTACH_ELECTROMAGNETIC"),
            ),
        ),
        state_seconds=ult_duration,
        evidence=(
            "连携消耗电磁附着并施加/升级导电",
            f"战技消费导电并单次最多生成 {per_cast_cap} 柄青霆剑",
            f"青霆剑快照上限 {sword_cap if sword_cap is not None else 'unknown'}",
            f"终结技持续 {ult_duration:g}s；首次战技免费且固定生成 3 剑" if ult_duration else "终结技首次战技免费且固定生成 3 剑",
        ),
    )


def _yvonne(key: str, payload: dict) -> CharacterMechanic | None:
    battle = _skill(payload, "战技")
    link = _skill(payload, "连携技")
    ult = _skill(payload, "终结技")
    if not (battle and link and ult):
        return None
    trigger = None
    for enhancement in battle.get("enhancements") or ():
        if not isinstance(enhancement, dict):
            continue
        effects = enhancement.get("trigger_condition", {}).get("effects") or {}
        any_effects = tuple(effects.get("any") or ())
        if {"ATTACH_COLD", "ATTACH_NATURAL"}.issubset(set(any_effects)):
            trigger = any_effects
            break
    ult_duration = _last_stat(ult, "持续时间（秒）")
    max_crit = int(_last_stat(ult, "最大叠加层数") or 0) or None
    per_layer_energy = _last_stat(battle, "消耗每层附着额外获得终结技能量")
    if not trigger:
        return None
    return CharacterMechanic(
        key=key,
        name=str(payload.get("name") or key),
        archetype="consume_attachment_freeze_control",
        generic_cycle_safe=False,
        resources=(
            MechanicResource("spell_attach", "法术附着层数", 4),
            MechanicResource("frozen", "冻结", 1),
            MechanicResource("ult_crit_stack", "终结技暴击层数", max_crit),
        ),
        transitions=(
            MechanicTransition(
                "battle", "冰冰弹·β型", None,
                sp_gate=_last_stat(battle, "技力消耗"),
                sp_cost=_last_stat(battle, "技力消耗"),
                requires=("ATTACH_COLD|ATTACH_NATURAL",),
                consumes=("spell_attach:all",),
                produces=("STATUS_FROZEN",),
                notes=(
                    f"每消耗 1 层附着额外获得 {per_layer_energy:g} 终结技能量" if per_layer_energy is not None else "消耗层数影响伤害/终结技能量",
                ),
            ),
            MechanicTransition(
                "link", "速冻仔·u37", None,
                requires=("STATUS_FROZEN", "main_control_final_strike"),
                produces=("STATUS_FROZEN",),
            ),
            MechanicTransition(
                "ult", "冷冻射手", "主控强化普攻",
                requires=("prefer:STATUS_FROZEN",),
                consumes=("STATUS_FROZEN:on_final_attack_if_present",),
                produces=("forced_main_control",),
                notes=("必须保留普通攻击直到持续时间结束前最后一击",),
            ),
        ),
        forced_main_control_seconds=ult_duration,
        evidence=(
            "战技要求寒冷或自然附着并消费目标全部法术附着后冻结",
            "连携要求冻结目标受到主控重击",
            f"终结技强制主控约 {ult_duration:g}s" if ult_duration else "终结技强制切换主控",
            "终结技最后一次普通攻击为重击；冻结存在时追加伤害并消耗冻结",
        ),
    )


_BUILDERS = {
    "mi_fu": _mifu,
    "typhoeus": _typhoeus,
    "zhuang_fangyi": _zhuang,
    "yvonne": _yvonne,
}


@lru_cache(maxsize=1)
def load_character_mechanics() -> dict[str, CharacterMechanic]:
    result: dict[str, CharacterMechanic] = {}
    for key, builder in _BUILDERS.items():
        path = _SKILLS_DIR / f"{key}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mechanic = builder(key, payload)
        if mechanic is not None:
            result[mechanic.name] = mechanic
    return result


def mechanic_blockers(team: list[str]) -> tuple[CharacterMechanic, ...]:
    mechanics = load_character_mechanics()
    return tuple(
        mechanic
        for name in team
        if name != "?"
        for mechanic in (mechanics.get(name),)
        if mechanic is not None and not mechanic.generic_cycle_safe
    )


def clear_mechanics_cache() -> None:
    load_character_mechanics.cache_clear()
