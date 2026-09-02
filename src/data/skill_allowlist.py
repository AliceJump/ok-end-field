"""自动技能列表：根据队伍 4 角色的增强链依赖，自动判定哪些战技有意义释放。

编队识别已移至 BattleMixin.detect_team()（通过框架 find_one 原子操作），
本模块仅负责技能匹配逻辑：给定角色名列表 → 判定允许释放的技能序列。

使用方式：
    members = task.detect_team()         # 战斗中截帧，框架 find_one 自动识别
    seq = ["1", "2", "3"]
    filtered = filter_skill_sequence(members, seq)
    # → ["1", "3"]  （2号位伊冯、4号位余烬的战技被禁止）

参见 tools/team_composer.py --allowlist 查看命令行版本。
"""

from __future__ import annotations

import json
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parent.parent.parent
_DATA_DIR = _ROOT / "assets" / "data" / "character_skills"

_SHRED_STACK_PRODUCERS = {
    "STATUS_SHRED",
    "STATUS_HEAVY_HIT",
    "STATUS_KNOCKDOWN",
}

# 豁免名单：这些角色的战技跳过所有检查，直接允许释放
EXEMPT_CHARACTERS: set[str] = set()

# ── 数据加载 ──────────────────────────────────────────────────────────────────

_cached_characters: dict[str, dict] | None = None


def load_characters(data_dir: Path | None = None) -> dict[str, dict]:
    """加载所有 character_skills/*.json，返回 {name: skill_dict} 缓存。"""
    global _cached_characters
    if _cached_characters is not None:
        return _cached_characters

    d = data_dir or _DATA_DIR
    result: dict[str, dict] = {}
    if not d.exists():
        return result
    for fp in sorted(d.glob("*.json")):
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name", fp.stem)
        result[name] = data
    _cached_characters = result
    return result


def clear_cache():
    """清除缓存（测试用）。"""
    global _cached_characters
    _cached_characters = None


# ── 核心逻辑：两阶段构建允许列表 ────────────────────────────────────────────


def _effect_id(effect) -> str:
    if isinstance(effect, str):
        return effect
    if isinstance(effect, dict):
        return effect.get("effect_id", "")
    return ""


def _produced_effect_id(effect) -> str:
    """只把实际施加/增加的效果计为 producer。"""
    effect_id = _effect_id(effect)
    if not effect_id:
        return ""
    if isinstance(effect, dict):
        count = effect.get("count", 1)
        if count is not None and count <= 0:
            return ""
    return effect_id


def _static_release_gate(enhancement: dict) -> dict | None:
    """从 trigger_condition.effects 读取显式静态门控；非 gate 分支返回 None。"""
    trigger = enhancement.get("trigger_condition", {})
    if not isinstance(trigger, dict):
        return None
    effects = trigger.get("effects")
    if not isinstance(effects, dict):
        return None
    for operator in ("all", "any"):
        if operator not in effects:
            continue
        requires = [effect_id for effect_id in effects.get(operator) or [] if (eid := _effect_id(effect_id))]
        if requires:
            return {"operator": operator, "requires": requires}
    return None


def _branch_can_trigger(branch: dict, producers: dict, skill_key: tuple[str, str]) -> bool:
    """判断一个静态 gate 分支是否能由其它队内技能满足。"""
    checks = [
        any(
            (producer_name, producer_skill_id) != skill_key
            for producer_name, producer_skill_id, _ in producers.get(effect_id, [])
        )
        for effect_id in branch["requires"]
    ]
    return all(checks) if branch["operator"] == "all" else any(checks)


def _skill_effects_needed_by_others(
    skill_data: dict,
    all_release_gates: dict,
    self_key: tuple[str, str],
) -> bool:
    """检查技能产出的效果是否被队内其他成员所依赖。

    用于二次评估：
    - 效果被其他队友的 release gate 依赖 → 阻止（增强态优先）
    - 效果仅被自己增强态依赖（trigger_condition）→ 允许
    - 效果无人依赖 → 阻止（效果无依赖）

    返回 True 表示应该释放，
    返回 False 表示不应该释放（有效果但无人依赖）。
    """
    effects = skill_data.get("effects") or []
    if not effects:
        return True

    # 收集自己增强态依赖的效果（trigger_condition.effects）
    self_dep_effects: set[str] = set()
    skill_enhancements = skill_data.get("enhancements") or []
    for enh in skill_enhancements:
        trigger = enh.get("trigger_condition", {})
        if not isinstance(trigger, dict):
            continue
        trigger_effects = trigger.get("effects", [])
        if isinstance(trigger_effects, dict):
            # 新结构：{"all": [...]} 或 {"any": [...]}
            for op in ("all", "any"):
                for eff in trigger_effects.get(op) or []:
                    if isinstance(eff, str):
                        self_dep_effects.add(eff)
        elif isinstance(trigger_effects, list):
            # 兼容旧结构：["A", "B"]
            for eff in trigger_effects:
                if isinstance(eff, str):
                    self_dep_effects.add(eff)

    for eff in effects:
        effect_id = _produced_effect_id(eff)
        if not effect_id:
            continue
        # 效果被自己增强态依赖 → 跳过，不阻止
        if effect_id in self_dep_effects:
            continue
        # 检查是否有其他队友的 release gate 需要这个效果
        for req_key, gates in all_release_gates.items():
            if req_key == self_key:
                continue
            for gate in gates:
                if effect_id in gate.get("requires", []):
                    return True
        # 效果无人依赖 → 阻止
        return False

    # 无产出效果或所有产出效果都被自己依赖 → 允许
    return True


def _build_team_skill_context(
    team_members: list[str],
    characters: dict[str, dict],
) -> dict:
    """第一阶段：扫描队伍所有技能，建立依赖关系图。

    Returns:
        {
            "producers": {effect_id: [(char_name, skill_id, skill_type), ...]},
            "release_gates": {(char_name, skill_id): [gate_branch, ...]},
        }
    """
    team_set = set(team_members)

    # 收集队内所有技能
    all_skills: dict[tuple[str, str], dict] = {}
    for name, cdata in characters.items():
        if name not in team_set:
            continue
        for s in cdata.get("skills", []):
            all_skills[(name, s.get("skill_id", ""))] = s

    # ── producers: 哪些技能在施放后产出什么状态 ──
    producers: dict[str, list[tuple[str, str, str]]] = {}
    for (sname, sid), sdata in all_skills.items():
        producer = (sname, sid, sdata.get("skill_type", ""))
        for eff in sdata.get("effects") or []:
            effect_id = _produced_effect_id(eff)
            if not effect_id:
                continue
            effect_producers = producers.setdefault(effect_id, [])
            if producer not in effect_producers:
                effect_producers.append(producer)
            if effect_id in _SHRED_STACK_PRODUCERS:
                shred_producers = producers.setdefault("STACK_SHRED", [])
                if producer not in shred_producers:
                    shred_producers.append(producer)

    # ── release gates: 每个 enhancement 保持独立分支，分支间按 OR 判定 ──
    release_gates: dict[tuple[str, str], list[dict]] = {}
    for (sname, sid), sdata in all_skills.items():
        if sdata.get("skill_type") != "战技":
            continue
        skill_enhancements = sdata.get("enhancements") or []
        if not skill_enhancements:
            continue

        branches = []
        for enh in skill_enhancements:
            if gate := _static_release_gate(enh):
                branches.append(gate)
        if branches:
            release_gates[(sname, sid)] = branches

    return {"producers": producers, "release_gates": release_gates}


def build_skill_allowlist(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> dict[int, tuple[bool, str]]:
    """判断队伍中每个角色的战技是否允许手动释放。

    两阶段架构：
      1. 建立队伍技能依赖图（producers / release gates）
      2. 逐个角色判定战技是否应被增强态接管

    Args:
        team_members: 4 个角色名，索引 0-3 对应技能键 "1"-"4"
        characters:   角色数据字典（可选，默认加载）

    Returns:
        {位置索引: (是否允许, 阻止原因)}
    """
    if characters is None:
        characters = load_characters()

    if not characters:
        return {i: (True, "") for i in range(len(team_members))}

    # ── 第一阶段：建立队伍技能依赖图 ──
    ctx = _build_team_skill_context(team_members, characters)

    # ── 第二阶段：逐个判定战技 ──
    result: dict[int, tuple[bool, str]] = {}

    for idx, char_name in enumerate(team_members):
        char_data = characters.get(char_name)
        if not char_data:
            result[idx] = (True, "")
            continue

        # 找到该角色的战技
        skill = None
        for s in char_data.get("skills", []):
            if s.get("skill_type") == "战技":
                skill = s
                break
        if not skill:
            result[idx] = (True, "")
            continue

        key = (char_name, skill.get("skill_id", ""))
        release_gates = ctx["release_gates"].get(key)

        # 豁免：跳过所有检查，直接允许释放
        if char_name in EXEMPT_CHARACTERS:
            result[idx] = (True, "")
            continue

        # 一次评估：检查技能的 release gate 是否能被队友触发
        if release_gates and any(
            _branch_can_trigger(branch, ctx["producers"], key)
            for branch in release_gates
        ):
            result[idx] = (False, "增强态优先")
            continue

        # 二次评估：检查技能产出的效果是否被任何增强态或 release gate 所依赖
        if not _skill_effects_needed_by_others(skill, ctx["release_gates"], key):
            result[idx] = (False, "效果无依赖")
            continue

        result[idx] = (True, "")

    return result


# ── 生成技能释放序列 ────────────────────────────────────────────────

# 普通战技 token 集合（不含 ult_/e/sleep_/normal_）
_NORMAL_SKILL_TOKENS = {"1", "2", "3", "4"}


def generate_skill_sequence(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据队伍编队直接生成允许的战技释放序列（生成式，不依赖用户配置）。

    自动构建 allowlist，仅返回被允许的数字战技 token，按索引升序排列。

    Args:
        team_members: 4 个角色名，索引 i 对应技能键 "i+1"
        characters:   角色数据（可选）

    Returns:
        生成的战技释放序列，如 ["1", "3"]
    """
    allowlist = build_skill_allowlist(team_members, characters)
    result = [str(i + 1) for i, (ok, _) in allowlist.items() if ok]
    return result if result else [str(i + 1) for i in range(len(team_members))]


def filter_skill_sequence(
    team_members: list[str],
    skill_sequence: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据自动技能列表过滤技能释放序列。

    保留原始序列中的非数字 token（ult_/e/sleep_/normal_ 等），
    仅对数字战技 token（1-4）根据允许列表进行过滤。

    Args:
        team_members:   4 个角色名，索引 i 对应技能键 "i+1"
        skill_sequence: 原始技能序列
        characters:     角色数据（可选）

    Returns:
        过滤后的技能释放序列
    """
    allowlist = build_skill_allowlist(team_members, characters)
    allowed_digits = {
        str(i + 1) for i, (ok, _) in allowlist.items() if ok
    }

    filtered = []
    for token in skill_sequence:
        if token in _NORMAL_SKILL_TOKENS:
            # 数字战技 token：仅保留允许列表中的
            if token in allowed_digits:
                filtered.append(token)
        else:
            # 非数字 token（ult_/e/sleep_/normal_ 等）：保留
            filtered.append(token)

    return filtered if filtered else list(skill_sequence)
