#!/usr/bin/env python3
"""
明日方舟：终末地 — 队伍配对算法

基于元素反应、角色定位、技能协同、增强触发链等多维度评分，
为玩家推荐最优 4 人队伍组合。

评分维度：
  1. 元素反应覆盖 (element_reaction)
  2. 角色定位均衡 (role_balance)
  3. 增强触发链   (enhancement_chain)
  4. 状态施加/消耗协同 (status_synergy)
  5. 连携技触发条件覆盖 (link_coverage)

用法：
  python tools/team_composer.py                    # 推荐所有队伍组合 top-10
  python tools/team_composer.py --must 莱万汀 卡缪  # 固定某些角色，推荐剩余位
  python tools/team_composer.py --element 灼热      # 偏好某元素
  python tools/team_composer.py --element2 灼热 物理 # 偏好双元素组合
  python tools/team_composer.py --json              # 输出 JSON 格式
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TEAM_SIZE = 4

# 四种法术异常状态：不同元素附着交叉触发
ANOMALY_RECIPES: dict[str, tuple[str, str]] = {
    # 异常名 → (需要元素A, 需要元素B)  顺序无关
    "STATUS_CORROSION":   ("自然", "非自然"),   # 自然 + 其他
    "STATUS_FROZEN":      ("寒冷", "非寒冷"),   # 寒冷 + 其他
    "STATUS_CONDUCTING":  ("电磁", "非电磁"),   # 电磁 + 其他
    "STATUS_BURNING":     ("灼热", "非灼热"),   # 灼热 + 其他
}

# 同元素触发法术爆发
SPELL_BURST_COMBOS: list[str] = ["自然", "寒冷", "电磁", "灼热"]

# 元素→脆弱类型映射
ELEMENT_TO_VULN: dict[str, str] = {
    "自然": "VULN_NATURAL",
    "寒冷": "VULN_COLD",
    "电磁": "VULN_ELECTROMAGNETIC",
    "灼热": "VULN_BURN",
    "物理": "VULN_PHYSICAL",
}

ELEMENTS = {"物理", "自然", "寒冷", "电磁", "灼热"}
PROFESSIONS = {"先锋", "近卫", "重装", "狙击", "术师", "医疗", "辅助", "特种", "突击"}

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class SkillInfo:
    skill_id: str
    name: str
    skill_type: str  # 普通攻击/战技/连携技/终结技
    element: str
    has_enhancement: bool
    enhancement: Optional[dict] = None
    description: str = ""
    effects: list[dict] = field(default_factory=list)
    trigger_conditions: list[str] = field(default_factory=list)
    stagger_value: int = 0
    spirit_cost: int = 0


@dataclass
class CharacterData:
    character_id: str
    name: str
    star: int
    element: str
    profession: str
    weapon_type: str
    skills: list[SkillInfo] = field(default_factory=list)

    # --- 从技能中提取的聚合属性 ---
    attach_elements: set[str] = field(default_factory=set)      # 该角色能施加的元素附着
    can_produce_anomalies: set[str] = field(default_factory=set)  # 能产生的法术异常
    can_consume_anomalies: set[str] = field(default_factory=set)  # 能消耗的法术异常
    vuln_effects: set[str] = field(default_factory=set)          # 能施加的脆弱效果
    buff_effects: set[str] = field(default_factory=set)          # 能提供的增益
    debuff_effects: set[str] = field(default_factory=set)        # 能施加的减益
    status_effects: set[str] = field(default_factory=set)        # 能施加的异常状态
    link_trigger_conditions: list[str] = field(default_factory=list)  # 连携技触发条件
    enhancement_triggers: list[dict] = field(default_factory=list)    # 增强触发条件
    has_heal: bool = False
    has_shield: bool = False
    stagger_value: int = 0  # 普攻失衡值（主控时）


def load_character(filepath: Path) -> CharacterData:
    """从 JSON 文件加载角色数据。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    skills = []
    for s in data.get("skills", []):
        skill = SkillInfo(
            skill_id=s["skill_id"],
            name=s["name"],
            skill_type=s["skill_type"],
            element=s.get("element", data.get("element", "")),
            has_enhancement=s.get("has_enhancement", False),
            enhancement=s.get("enhancement"),
            description=s.get("description", ""),
            effects=s.get("effects", []),
            stagger_value=s.get("stagger_value", 0),
            spirit_cost=s.get("spirit_cost", 0),
        )
        # 提取增强触发条件文本
        if skill.has_enhancement and skill.enhancement:
            tc = skill.enhancement.get("trigger_condition", {})
            if tc.get("text"):
                skill.trigger_conditions.append(tc["text"])
                # 也提取 trigger_condition.effects 中的前置状态
                for eff in tc.get("effects", []):
                    skill.trigger_conditions.append(f"requires:{eff}")
        skills.append(skill)

    char = CharacterData(
        character_id=data["character_id"],
        name=data["name"],
        star=data.get("star", 0),
        element=data.get("element", ""),
        profession=data.get("profession", ""),
        weapon_type=data.get("weapon_type", ""),
        skills=skills,
    )

    # 聚合分析
    for sk in skills:
        # 元素附着
        for eff in sk.effects:
            eid = eff.get("effect_id", "")
            if eid.startswith("ATTACH_"):
                elem_map = {
                    "ATTACH_COLD": "寒冷", "ATTACH_BURN": "灼热",
                    "ATTACH_ELECTROMAGNETIC": "电磁", "ATTACH_NATURAL": "自然",
                }
                if eid in elem_map:
                    char.attach_elements.add(elem_map[eid])
            # 脆弱
            if eid.startswith("VULN_"):
                char.vuln_effects.add(eid)
            # 增益
            if eid.startswith("BUFF_"):
                char.buff_effects.add(eid)
            # 减益
            if eid.startswith("DEBUFF_"):
                char.debuff_effects.add(eid)
            # 异常状态
            if eid.startswith("STATUS_") and eid not in (
                "STATUS_SPELL_INFLICT", "STATUS_SPELL_BURST",
                "STATUS_SPELL_ANOMALY", "STATUS_BROKEN",
            ):
                char.status_effects.add(eid)
            # 治疗/护盾
            if eid in ("BUFF_HEAL", "TRIGGER_HEAL"):
                char.has_heal = True
            if eid in ("BUFF_SHIELD", "TRIGGER_SHIELD"):
                char.has_shield = True
            # 消耗法术异常
            if eid.startswith("CLEAR_"):
                clear_map = {
                    "CLEAR_COLD": "STATUS_FROZEN",
                    "CLEAR_NATURAL": "STATUS_CORROSION",
                }
                if eid in clear_map:
                    char.can_consume_anomalies.add(clear_map[eid])

        # 从增强 trigger 判断能消耗的异常
        if sk.has_enhancement and sk.enhancement:
            tc = sk.enhancement.get("trigger_condition", {})
            for teff in tc.get("effects", []):
                anomaly_map = {
                    "STATUS_BURNING": "STATUS_BURNING",
                    "STATUS_FROZEN": "STATUS_FROZEN",
                    "STATUS_CONDUCTING": "STATUS_CONDUCTING",
                    "STATUS_CORROSION": "STATUS_CORROSION",
                    "STATUS_SHRED": "STATUS_SHRED",
                }
                if teff in anomaly_map:
                    char.can_consume_anomalies.add(anomaly_map[teff])
            char.enhancement_triggers.append(tc)

        # 连携技触发条件
        if sk.skill_type == "连携技":
            char.link_trigger_conditions.append(sk.description)

        # 普攻失衡
        if sk.skill_type == "普通攻击":
            char.stagger_value = sk.stagger_value

    # 判断能产生的法术异常：能施加元素附着的角色理论上可以与不同元素队友配合产生异常
    # 但如果自己能施加多种不同元素附着，则自身就能触发
    if len(char.attach_elements) >= 2:
        # 自身多元素 → 可以自触发
        for anomaly, (a, b) in ANOMALY_RECIPES.items():
            if "非" in a or "非" in b:
                # 需要两种不同元素
                if len(char.attach_elements) >= 2:
                    char.can_produce_anomalies.add(anomaly)

    return char


def load_all_characters(data_dir: Path) -> list[CharacterData]:
    """加载所有角色数据。"""
    chars = []
    for fp in sorted(data_dir.glob("*.json")):
        try:
            chars.append(load_character(fp))
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ 跳过 {fp.name}: {e}", file=sys.stderr)
    return chars


# ---------------------------------------------------------------------------
# 评分引擎
# ---------------------------------------------------------------------------
@dataclass
class TeamScore:
    """队伍评分详情。"""
    total: float = 0.0
    element_reaction: float = 0.0
    role_balance: float = 0.0
    enhancement_chain: float = 0.0
    status_synergy: float = 0.0
    link_coverage: float = 0.0
    details: list[str] = field(default_factory=list)


def score_team(team: list[CharacterData]) -> TeamScore:
    """计算 4 人队伍的综合评分。

    权重分配（满分 100）：
      - 状态生产-消耗链 (status_synergy): 35  ← 配队核心：A产出X → B消耗X
      - 增强触发链 (enhancement_chain): 25  ← 角色增强机制的协同
      - 元素反应覆盖 (element_reaction): 20 ← 法术异常种类覆盖
      - 角色定位均衡 (role_balance): 10     ← 基本的DPS/辅助/承伤/治疗
      - 连携技覆盖 (link_coverage): 10      ← 连携技触发条件覆盖
    """
    ts = TeamScore()
    elements = {c.element for c in team}
    professions = {c.profession for c in team}

    # =====================================================================
    # 0. 预计算：谁生产什么状态，谁消耗什么状态
    # =====================================================================
    # 状态生产者：角色的 effects 里施加的 STATUS_* 或 ATTACH_*
    status_producers: dict[str, list[str]] = {}  # status_effect_id → [角色名]
    for c in team:
        # 从技能 effects 收集
        for s in c.skills:
            for eff in s.effects:
                eid = eff.get("effect_id", "")
                if eid.startswith("STATUS_") and eid not in (
                    "STATUS_SPELL_INFLICT", "STATUS_SPELL_BURST",
                    "STATUS_SPELL_ANOMALY", "STATUS_BROKEN",
                ):
                    status_producers.setdefault(eid, []).append(c.name)
                if eid.startswith("ATTACH_"):
                    # 元素附着可以由队友配合产生法术异常
                    status_producers.setdefault(eid, []).append(c.name)
        # 从增强效果收集
        if c.skills:
            for s in c.skills:
                if s.has_enhancement and s.enhancement:
                    for eff in s.enhancement.get("effects", []):
                        eid = eff.get("effect_id", "")
                        if eid.startswith("STATUS_") and eid not in (
                            "STATUS_SPELL_INFLICT", "STATUS_SPELL_BURST",
                            "STATUS_SPELL_ANOMALY", "STATUS_BROKEN",
                        ):
                            status_producers.setdefault(eid, []).append(c.name)

    # 状态消耗者：角色的增强 trigger_condition.effects 里标注需要的状态
    status_consumers: dict[str, list[str]] = {}  # status_effect_id → [角色名]
    for c in team:
        for s in c.skills:
            if s.has_enhancement and s.enhancement:
                tc = s.enhancement.get("trigger_condition", {})
                for eff in tc.get("effects", []):
                    if eff.startswith("STATUS_"):
                        status_consumers.setdefault(eff, []).append(c.name)

    # =====================================================================
    # 1. 状态生产-消耗链 (满分 35) ← 核心
    # =====================================================================
    # 每条有效链：不同角色生产X → 另一角色消耗X
    chain_pairs: list[str] = []
    chain_count = 0
    for status_id, producers in status_producers.items():
        consumers = status_consumers.get(status_id, [])
        if not consumers:
            continue
        unique_producers = set(producers)
        unique_consumers = set(consumers)
        # 去掉自己生产自己消耗的情况（自循环不算协同）
        cross = set()
        for p in unique_producers:
            for con in unique_consumers:
                if p != con:
                    cross.add((p, con))
        if cross:
            chain_count += len(cross)
            for p, con in cross:
                chain_pairs.append(f"{p}→{con} ({status_id})")

    # 基础分：每条链 8 分，上限 30
    ts.status_synergy = min(chain_count * 8, 30)

    # 额外加分：如果有多条不同的状态链（不是同一个状态的多条链）
    unique_statuses_with_chain = set()
    for status_id, producers in status_producers.items():
        consumers = status_consumers.get(status_id, [])
        if consumers and any(p != c for p in producers for c in consumers):
            unique_statuses_with_chain.add(status_id)
    if len(unique_statuses_with_chain) >= 2:
        ts.status_synergy += 5  # 多状态链协同 +5

    ts.status_synergy = min(ts.status_synergy, 35)
    for cp in chain_pairs:
        ts.details.append(f"状态链: {cp}")

    # =====================================================================
    # 2. 增强触发链 (满分 25)
    # =====================================================================
    # 角色的增强 trigger 需要特定状态，团队中有谁能提供
    enhancement_triggers_found = 0
    for c in team:
        for s in c.skills:
            if s.has_enhancement and s.enhancement:
                tc = s.enhancement.get("trigger_condition", {})
                trigger_effects = tc.get("effects", [])
                if not trigger_effects:
                    continue
                for teff in trigger_effects:
                    # 检查团队中是否有其他人能产出这个状态
                    producers_for_this = status_producers.get(teff, [])
                    other_producers = [p for p in producers_for_this if p != c.name]
                    if other_producers:
                        enhancement_triggers_found += 1
                        ts.details.append(f"增强触发: {c.name}.需要{teff}←{','.join(set(other_producers))}")

    ts.enhancement_chain = min(enhancement_triggers_found * 8, 25)

    # =====================================================================
    # 3. 元素反应覆盖 (满分 20)
    # =====================================================================
    all_attach = set()
    for c in team:
        all_attach |= c.attach_elements
    for c in team:
        if c.element != "物理":
            all_attach.add(c.element)

    anomaly_coverage: set[str] = set()
    for anomaly, (a, b) in ANOMALY_RECIPES.items():
        if "非自然" in b:
            if "自然" in all_attach and any(e != "自然" for e in all_attach):
                anomaly_coverage.add(anomaly)
        elif "非寒冷" in b:
            if "寒冷" in all_attach and any(e != "寒冷" for e in all_attach):
                anomaly_coverage.add(anomaly)
        elif "非电磁" in b:
            if "电磁" in all_attach and any(e != "电磁" for e in all_attach):
                anomaly_coverage.add(anomaly)
        elif "非灼热" in b:
            if "灼热" in all_attach and any(e != "灼热" for e in all_attach):
                anomaly_coverage.add(anomaly)

    # 每种异常 +4（原来是+8，降低权重）
    ts.element_reaction += len(anomaly_coverage) * 4

    # 同元素法术爆发（有 2+ 同元素角色 +3）
    from collections import Counter
    elem_count = Counter(c.element for c in team)
    for elem, cnt in elem_count.items():
        if elem != "物理" and cnt >= 2:
            ts.element_reaction += 3
            ts.details.append(f"同元素{elem}×{cnt}→法术爆发")

    if anomaly_coverage:
        names = {"STATUS_CORROSION": "腐蚀", "STATUS_FROZEN": "冻结",
                 "STATUS_CONDUCTING": "导电", "STATUS_BURNING": "燃烧"}
        ts.details.append(f"法术异常覆盖: {'/'.join(names[a] for a in sorted(anomaly_coverage))}")

    # 元素多样性加分（不同元素数 × 1，最多 +4）
    ts.element_reaction += min(len(elements), 5) * 1
    ts.element_reaction = min(ts.element_reaction, 20)

    # =====================================================================
    # 4. 角色定位均衡 (满分 10)
    # =====================================================================
    role_categories: dict[str, list[str]] = {
        "dps": ["先锋", "近卫", "突击", "狙击"],
        "caster": ["术师", "辅助"],
        "tank": ["重装", "近卫"],
        "healer": ["医疗", "辅助"],
    }
    covered_roles: dict[str, bool] = {}
    for cat, profs in role_categories.items():
        covered_roles[cat] = any(c.profession in profs for c in team)

    ts.role_balance += sum(2 for v in covered_roles.values() if v)  # 每项2分=8
    if any(c.has_heal for c in team):
        ts.role_balance += 1
        ts.details.append("有治疗")
    if any(c.has_shield for c in team):
        ts.role_balance += 1
        ts.details.append("有护盾")
    ts.role_balance = min(ts.role_balance, 10)

    # =====================================================================
    # 5. 连携技触发条件覆盖 (满分 10)
    # =====================================================================
    link_conditions: list[str] = []
    for c in team:
        link_conditions.extend(c.link_trigger_conditions)

    condition_keywords = ["燃烧", "冻结", "导电", "腐蚀", "破防", "失衡",
                          "法术异常", "物理脆弱", "法术脆弱", "击飞", "倒地",
                          "电磁附着", "寒冷附着", "灼热附着", "自然附着"]
    covered_conditions = set()
    for cond in link_conditions:
        for kw in condition_keywords:
            if kw in cond:
                covered_conditions.add(kw)

    ts.link_coverage = min(len(covered_conditions) * 1, 10)
    if covered_conditions:
        ts.details.append(f"连携触发覆盖: {'/'.join(sorted(covered_conditions))}")

    # =====================================================================
    # 总分
    # =====================================================================
    ts.total = ts.element_reaction + ts.role_balance + ts.enhancement_chain + ts.status_synergy + ts.link_coverage
    return ts


# ---------------------------------------------------------------------------
# 技能价值分析（技力经济学）
# ---------------------------------------------------------------------------

# 效果基础价值评分（不考虑增强，纯效果本身的价值）
# 满分10：这个效果本身值多少技力
EFFECT_BASE_VALUE: dict[str, int] = {
    # 高价值：改变战局
    "STATUS_FROZEN": 9,       # 冻结：强控+法术异常
    "STATUS_CORROSION": 8,    # 腐蚀：全属性抗性下降
    "STATUS_CONDUCTING": 7,   # 导电：法术伤害提高
    "STATUS_BURNING": 7,      # 燃烧：持续伤害
    "VULN_ALL": 9,            # 法术脆弱：全元素增伤
    "VULN_COLD": 7,           # 寒冷脆弱
    "VULN_BURN": 7,           # 灼热脆弱
    "VULN_ELECTROMAGNETIC": 7,
    "VULN_NATURAL": 7,
    "VULN_PHYSICAL": 7,       # 物理脆弱
    "BUFF_HEAL": 8,           # 治疗
    "BUFF_SHIELD": 8,         # 护盾
    "BUFF_DAMAGE_UP": 7,      # 伤害提升
    "BUFF_ATTACK_UP": 6,      # 攻击力提升
    "BUFF_CRIT_RATE_UP": 5,   # 暴击率
    "BUFF_CRIT_DMG_UP": 5,    # 暴击伤害
    "TRIGGER_ADDITIONAL": 8,  # 追加攻击
    "MECH_VACUUM": 6,         # 聚怪
    "STATUS_SLOW": 4,         # 缓速

    # 中价值：有战术意义但不核心
    "ATTACH_COLD": 5,         # 寒冷附着（后续可触发冻结）
    "ATTACH_BURN": 5,         # 灼热附着（后续可触发燃烧）
    "ATTACH_ELECTROMAGNETIC": 5,
    "ATTACH_NATURAL": 5,      # 自然附着（后续可触发腐蚀）
    "STATUS_SHRED": 5,        # 破防（叠层用）
    "DEBUFF_DEF_DOWN": 5,     # 防御力降低
    "STATUS_SPELL_INFLICT": 4, # 法术附着

    # 低价值：消耗战技值不划算
    "STATUS_KNOCKDOWN": 2,    # 倒地：物理异常控制不稳定，首次只触发破防
    "STATUS_HEAVY_HIT": 3,    # 击飞：类似倒地
    "STATUS_STAGGER": 2,      # 失衡：基础异常，不算赚
    "STATUS_SHATTER": 3,      # 碎甲：需要叠满破防才触发
    "STATUS_HEAVY_STRIKE": 3, # 猛击：需要叠满破防才触发
    "DEBUFF_SPEED_DOWN": 2,   # 减速
    "DEBUFF_HEAL_DOWN": 2,    # 治疗降低
}

# 增强触发条件能被满足时的额外加分
ENHANCEMENT_BONUS = 5


@dataclass
class SkillVerdict:
    """单个技能的值不值得放判定。"""
    character: str
    skill_name: str
    skill_type: str       # 战技/终结技
    spirit_cost: int
    verdict: str          # "⭐核心" / "✅值得" / "⚠️条件不满足" / "❌不值得"
    base_value: int       # 基础效果价值
    enhancement_bonus: int  # 增强加分（0=没增强或不满足条件）
    total_value: int
    reason: str
    consumes: list[str] = field(default_factory=list)   # 消耗什么状态
    requires: list[str] = field(default_factory=list)    # 需要什么状态
    produces: list[str] = field(default_factory=list)    # 产出什么状态


def analyze_team_skills(team: list[CharacterData]) -> list[SkillVerdict]:
    """分析队伍中每个角色的技能值不值得放。

    算法：
    1. 预计算团队能产出的所有状态
    2. 对每个角色的战技/终结技：
       a. 计算基础效果价值（所有 effects 的 EFFECT_BASE_VALUE 之和）
       b. 如果有增强：检查团队能否满足 trigger_condition.effects
          - 能满足 → 加 ENHANCEMENT_BONUS
          - 不能满足 → 增强效果作废，基础效果仍可用
       c. 计算性价比 = total_value / spirit_cost（每点技力的价值）
       d. 根据 total_value 和性价比给出判定
    """
    # 预计算：团队能产出的所有状态
    team_produces: dict[str, set[str]] = {}  # status → {角色名}
    for c in team:
        for s in c.skills:
            for eff in s.effects:
                eid = eff.get("effect_id", "")
                team_produces.setdefault(eid, set()).add(c.name)
            # 终结技/战技的 enhancement.effects 也算产出
            if s.has_enhancement and s.enhancement:
                for eff in s.enhancement.get("effects", []):
                    eid = eff.get("effect_id", "")
                    team_produces.setdefault(eid, set()).add(c.name)
        # attach_elements 也能产出对应附着
        for elem in c.attach_elements:
            elem_attach = {
                "寒冷": "ATTACH_COLD", "灼热": "ATTACH_BURN",
                "电磁": "ATTACH_ELECTROMAGNETIC", "自然": "ATTACH_NATURAL",
            }
            if elem in elem_attach:
                team_produces.setdefault(elem_attach[elem], set()).add(c.name)

    verdicts: list[SkillVerdict] = []

    for c in team:
        for s in c.skills:
            if s.skill_type not in ("战技", "终结技"):
                continue

            spirit_cost = s.spirit_cost

            # ---- 1. 基础效果价值 ----
            base_value = 0
            produces: list[str] = []
            consumes: list[str] = []
            for eff in s.effects:
                eid = eff.get("effect_id", "")
                base_value += EFFECT_BASE_VALUE.get(eid, 1)
                produces.append(eid)
            # 终结技的 effects 也可能有产出
            if s.has_enhancement and s.enhancement:
                for eff in s.enhancement.get("effects", []):
                    eid = eff.get("effect_id", "")
                    # 增强效果的价值单独算
                    pass

            # ---- 2. 增强条件检查 ----
            enhancement_bonus = 0
            requires: list[str] = []
            enhancement_triggered = False
            if s.has_enhancement and s.enhancement:
                tc = s.enhancement.get("trigger_condition", {})
                trigger_effects = tc.get("effects", [])
                enhancement_effects = s.enhancement.get("effects", [])

                if trigger_effects:
                    # 需要团队产出这些状态
                    all_met = True
                    for teff in trigger_effects:
                        requires.append(teff)
                        if teff not in team_produces:
                            all_met = False
                        elif c.name in team_produces[teff]:
                            # 自己产出自己消耗——检查是否有其他人也能产出
                            others = team_produces[teff] - {c.name}
                            if not others:
                                all_met = False  # 只有自己，不够稳定

                    if all_met:
                        enhancement_triggered = True
                        for eff in enhancement_effects:
                            eid = eff.get("effect_id", "")
                            enhancement_bonus += EFFECT_BASE_VALUE.get(eid, 3)
                            produces.append(eid)
                else:
                    # 无条件增强（如终结技期间自动触发）
                    enhancement_triggered = True
                    for eff in enhancement_effects:
                        eid = eff.get("effect_id", "")
                        enhancement_bonus += EFFECT_BASE_VALUE.get(eid, 3)
                        produces.append(eid)

            total_value = base_value + enhancement_bonus

            # ---- 2b. 链路贡献度 ----
            # 如果这个技能的产出是队友增强条件的前置，额外加分
            # 同时也检查：队友技能描述里提到"需要XX附着"的技能（如伊冰弹需要寒冷/自然附着）
            chain_bonus = 0
            chain_reasons = []
            claimed: set[str] = set()  # 每个产出只贡献一次链路
            for prod in produces:
                if prod in claimed:
                    continue
                for tc in team:
                    if tc.name == c.name:
                        continue
                    for ts in tc.skills:
                        if ts.has_enhancement and ts.enhancement:
                            tc_trigger = ts.enhancement.get("trigger_condition", {}).get("effects", [])
                            if prod in tc_trigger:
                                chain_bonus += 4
                                chain_reasons.append(f"{tc.name}的{ts.name}需要{prod}")
                                claimed.add(prod)
                                break
                        if prod in ts.trigger_conditions:
                            chain_bonus += 4
                            chain_reasons.append(f"{tc.name}的{ts.name}依赖{prod}")
                            claimed.add(prod)
                            break

            total_value += chain_bonus

            # ---- 3. 判定 ----
            if total_value == 0:
                verdict = "❌ 不值得"
                reason = "无有效效果，浪费技力"
            elif enhancement_triggered and enhancement_bonus > 0:
                if total_value >= 10:
                    verdict = "⭐ 核心"
                    reason = f"增强触发！效果价值 {total_value}"
                else:
                    verdict = "✅ 值得"
                    reason = f"增强触发，效果价值 {total_value}"
            elif s.has_enhancement and s.enhancement and not enhancement_triggered:
                # 有增强但条件不满足
                if base_value >= 6:
                    verdict = "⚠️ 基础可用"
                    reason = f"增强未触发（需 {', '.join(requires)}），仅基础效果价值 {base_value}"
                elif base_value >= 3:
                    verdict = "⚠️ 勉强"
                    reason = f"增强未触发，基础效果偏弱（{base_value}），技力紧张时可跳过"
                else:
                    verdict = "❌ 不值得"
                    reason = f"增强未触发，基础效果太弱（{base_value}），技力留给其他人"
            else:
                # 无增强的技能
                if total_value >= 7:
                    verdict = "✅ 值得"
                    reason = f"无条件效果，价值 {total_value}"
                elif total_value >= 4:
                    verdict = "⚠️ 看情况"
                    reason = f"效果一般（{total_value}），技力充足时可放"
                else:
                    verdict = "❌ 不值得"
                    reason = f"效果偏弱（{total_value}），不值得消耗技力"

            if chain_bonus > 0:
                reason += f"（链路+{chain_bonus}: {'; '.join(chain_reasons)}）"
            elif spirit_cost > 0 and total_value > 0:
                efficiency = total_value / spirit_cost * 100  # 每100技力的价值
                reason += f"（性价比 {efficiency:.0f}/百技力）"

            verdicts.append(SkillVerdict(
                character=c.name,
                skill_name=s.name,
                skill_type=s.skill_type,
                spirit_cost=spirit_cost,
                verdict=verdict,
                base_value=base_value,
                enhancement_bonus=enhancement_bonus,
                total_value=total_value,
                reason=reason,
                consumes=consumes,
                requires=requires,
                produces=produces,
            ))

    return verdicts


def format_skill_analysis(team: list[CharacterData], verdicts: list[SkillVerdict]) -> str:
    """格式化技能分析输出。"""
    lines = []
    lines.append(f"{'='*60}")
    lines.append(f"  队伍技能分析（技力经济学）")
    lines.append(f"  队伍: {' / '.join(c.name for c in team)}")
    lines.append(f"{'─'*60}")

    for c in team:
        lines.append(f"\n  【{c.name}】{c.element} | {c.profession}")
        char_verdicts = [v for v in verdicts if v.character == c.name]
        for v in char_verdicts:
            cost_str = f"（{v.spirit_cost}技力）" if v.spirit_cost > 0 else "（免费）"
            lines.append(f"    {v.verdict} {v.skill_name} {cost_str}")
            lines.append(f"      {v.reason}")
            if v.requires:
                lines.append(f"      需要: {', '.join(v.requires)}")
            if v.produces:
                lines.append(f"      产出: {', '.join(v.produces)}")

    # 总结：按优先级排序
    lines.append(f"\n{'─'*60}")
    lines.append(f"  推荐释放顺序（按价值从高到低）:")
    sorted_v = sorted(verdicts, key=lambda x: (-x.total_value, x.spirit_cost))
    for i, v in enumerate(sorted_v, 1):
        if "不值得" not in v.verdict:
            lines.append(f"    {i}. {v.character}.{v.skill_name} → {v.verdict}（价值{v.total_value}，{v.spirit_cost}技力）")

    lines.append(f"{'='*60}")
    return "\n".join(lines)


def find_team_by_names(characters: list[CharacterData], names: list[str]) -> Optional[list[CharacterData]]:
    """按名字查找角色组建队伍。"""
    team = []
    for name in names:
        found = [c for c in characters if c.name == name]
        if not found:
            print(f"⚠️ 未找到角色: {name}", file=sys.stderr)
            return None
        team.append(found[0])
    return team if len(team) > 0 else None


def find_best_teams(
    characters: list[CharacterData],
    must_include: list[str] | None = None,
    prefer_element: str | None = None,
    prefer_elements: list[str] | None = None,
    top_n: int = 10,
) -> list[tuple[list[CharacterData], TeamScore]]:
    """搜索最优队伍组合。

    Args:
        characters: 全部角色数据
        must_include: 必须包含的角色名列表
        prefer_element: 偏好元素（加分）
        prefer_elements: 偏好双元素组合（加分）
        top_n: 返回前 N 个结果
    """
    from itertools import combinations

    # 固定角色
    fixed: list[CharacterData] = []
    if must_include:
        for name in must_include:
            found = [c for c in characters if c.name == name]
            if found:
                fixed.append(found[0])
            else:
                print(f"⚠️ 未找到必须包含的角色: {name}", file=sys.stderr)

    remaining = [c for c in characters if c not in fixed]
    need = TEAM_SIZE - len(fixed)
    if need <= 0:
        # 已满或超出
        team = fixed[:TEAM_SIZE]
        return [(team, score_team(team))]

    results: list[tuple[list[CharacterData], TeamScore]] = []
    for combo in combinations(remaining, need):
        team = list(fixed) + list(combo)
        ts = score_team(team)

        # 元素偏好加分
        team_elements = {c.element for c in team}
        if prefer_element and prefer_element in team_elements:
            ts.element_reaction += 5.0
            ts.total += 5.0
        if prefer_elements:
            matched = sum(1 for e in prefer_elements if e in team_elements)
            ts.element_reaction += matched * 3.0
            ts.total += matched * 3.0

        results.append((team, ts))

    results.sort(key=lambda x: -x[1].total)
    return results[:top_n]


def format_team_result(rank: int, team: list[CharacterData], score: TeamScore) -> str:
    """格式化单支队伍推荐结果。"""
    lines = []
    lines.append(f"  #{rank}  总分: {score.total:.1f}")
    lines.append(f"  {'─'*50}")
    for c in team:
        star_str = "★" * c.star
        attach = "/".join(sorted(c.attach_elements)) if c.attach_elements else "-"
        lines.append(f"    {c.name:<8} {star_str:<8} {c.element:<6} {c.profession:<6} [{attach}]")
    lines.append(f"  元素反应: {score.element_reaction:.1f}  "
                 f"定位均衡: {score.role_balance:.1f}  "
                 f"增强链: {score.enhancement_chain:.1f}  "
                 f"状态协同: {score.status_synergy:.1f}  "
                 f"连携覆盖: {score.link_coverage:.1f}")
    if score.details:
        lines.append(f"  💡 {'; '.join(score.details[:3])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="明日方舟：终末地 — 队伍配对推荐算法",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/team_composer.py
  python tools/team_composer.py --must 莱万汀 卡缪
  python tools/team_composer.py --analyze 别礼 伊冯 洁尔佩塔 余烬
  python tools/team_composer.py --element 灼热
  python tools/team_composer.py --top 5 --json
        """,
    )
    parser.add_argument("--must", nargs="*", default=[], help="固定必须包含的角色名")
    parser.add_argument("--analyze", nargs="*", default=None,
                        help="分析给定队伍的技能价值（技力经济学），传入4个角色名")
    parser.add_argument("--element", default=None, help="偏好元素")
    parser.add_argument("--element2", nargs="*", default=None, help="偏好双元素组合")
    parser.add_argument("--top", type=int, default=10, help="显示前 N 个推荐 (默认 10)")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--data-dir", default=None,
                        help="角色数据目录 (默认: assets/data/character_skills)")

    args = parser.parse_args()

    # 确定数据目录
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        # 从脚本位置向上找项目根目录
        script_dir = Path(__file__).resolve().parent
        data_dir = script_dir.parent / "assets" / "data" / "character_skills"

    if not data_dir.exists():
        print(f"❌ 数据目录不存在: {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 加载角色数据: {data_dir}")
    characters = load_all_characters(data_dir)
    print(f"✅ 已加载 {len(characters)} 个角色\n")

    # --analyze 模式：分析给定队伍的技能价值
    if args.analyze is not None:
        if len(args.analyze) < 2:
            print("❌ --analyze 至少需要 2 个角色名", file=sys.stderr)
            sys.exit(1)
        team = find_team_by_names(characters, args.analyze)
        if team is None:
            sys.exit(1)
        verdicts = analyze_team_skills(team)
        print(format_skill_analysis(team, verdicts))
        if args.json:
            import json as json_mod
            json_out = [{
                "character": v.character, "skill": v.skill_name,
                "type": v.skill_type, "verdict": v.verdict,
                "base_value": v.base_value, "enhancement_bonus": v.enhancement_bonus,
                "total_value": v.total_value, "spirit_cost": v.spirit_cost,
                "reason": v.reason, "requires": v.requires, "produces": v.produces,
            } for v in verdicts]
            print(json_mod.dumps(json_out, ensure_ascii=False, indent=2))
        return

    # 显示角色概览
    print("📋 角色列表:")
    print(f"  {'名称':　<8} {'星级':　<8} {'元素':　<6} {'职业':　<6} 附着元素")
    print(f"  {'─'*60}")
    for c in sorted(characters, key=lambda x: (-x.star, x.name)):
        star_str = "★" * c.star
        attach = "/".join(sorted(c.attach_elements)) if c.attach_elements else "-"
        print(f"  {c.name:<8} {star_str:<8} {c.element:<6} {c.profession:<6} {attach}")
    print()

    # 搜索最优组合
    print(f"🔍 搜索最优 {TEAM_SIZE} 人队伍组合 (top-{args.top})...")
    prefer_elem = args.element
    prefer_elems = args.element2

    results = find_best_teams(
        characters,
        must_include=args.must if args.must else None,
        prefer_element=prefer_elem,
        prefer_elements=prefer_elems,
        top_n=args.top,
    )

    print(f"\n🏆 推荐队伍 (共 {len(results)} 支)\n")

    if args.json:
        json_results = []
        for rank, (team, score) in enumerate(results, 1):
            json_results.append({
                "rank": rank,
                "total_score": score.total,
                "scores": {
                    "element_reaction": score.element_reaction,
                    "role_balance": score.role_balance,
                    "enhancement_chain": score.enhancement_chain,
                    "status_synergy": score.status_synergy,
                    "link_coverage": score.link_coverage,
                },
                "members": [
                    {
                        "name": c.name,
                        "element": c.element,
                        "profession": c.profession,
                        "star": c.star,
                    }
                    for c in team
                ],
                "details": score.details,
            })
        print(json.dumps(json_results, ensure_ascii=False, indent=2))
    else:
        for rank, (team, score) in enumerate(results, 1):
            print(format_team_result(rank, team, score))
            print()


if __name__ == "__main__":
    main()
