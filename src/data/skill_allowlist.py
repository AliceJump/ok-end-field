"""自动技能列表：根据队伍 4 角色的增强链依赖，自动判定哪些战技有意义释放。

三阶段闭包算法：
  1. 建立"当前已存在效果集"（非战技强化态闭包）
  2. 处理"已经可以触发"的战技强化态（战技强化态闭包）
  3. 处理剩余战技，检查 effects 是否对强化链有贡献

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
    "STATUS_SHATTER",  # 碎甲
    "STATUS_HEAVY_STRIKE",  # 猛击
}

_CATEGORY_SPELL_ANOMALIES = {
    "STATUS_CORROSION",
    "STATUS_FROZEN",
    "STATUS_CONDUCTING",
    "STATUS_BURNING",
}

# 豁免名单：这些角色的战技跳过所有检查，直接允许释放
EXEMPT_CHARACTERS: set[str] = {
    "梨诺"  # 梨诺战技无消耗
}
NO_ALLOW_CHARACTERS: set[str] = {
    "余烬"  # 余烬战技基本无用
}

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


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _effect_id(effect) -> str:
    """提取效果 ID。"""
    if isinstance(effect, str):
        return effect
    if isinstance(effect, dict):
        return effect.get("effect_id", "")
    return ""


def _produced_effect_id(effect) -> str:
    """只把实际施加/增加的效果计为 producer（排除消费效果）。"""
    effect_id = _effect_id(effect)
    if not effect_id:
        return ""
    if isinstance(effect, dict):
        count = effect.get("count", 1)
        if count is not None and count <= 0:
            return ""
    return effect_id


def _expanded_produced_effect_ids(effect) -> tuple[str, ...]:
    """展开正向产出效果的依赖 ID，并保留原始类别效果。"""
    effect_id = _produced_effect_id(effect)
    if not effect_id:
        return ()
    if effect_id in _CATEGORY_SPELL_ANOMALIES:
        return effect_id, "STATUS_SPELL_ANOMALY"
    return (effect_id,)


def _register_effect_producer(
    effect_producers: dict[str, list[tuple[str, str]]],
    effect_id: str,
    key: tuple[str, str],
) -> None:
    """登记效果生产者，同一技能仅登记一次。"""
    producers = effect_producers.setdefault(effect_id, [])
    if key not in producers:
        producers.append(key)


def _parse_trigger_groups(trigger_condition: dict | str) -> list[dict]:
    """从 trigger_condition 中解析触发条件组。

    返回 [{"operator": "all"|"any", "effects": set[str]}, ...]
    每组是一个独立的条件判断，组间隐含 AND 关系。

    只解析 dict 格式且包含 all/any 的静态门控。
    简单列表格式（如 ['A']）被视为动态触发条件，不解析。
    """
    if isinstance(trigger_condition, str):
        return []

    effects = trigger_condition.get("effects", {})
    if not isinstance(effects, dict):
        return []

    groups: list[dict] = []
    for operator in ("all", "any"):
        effect_list = effects.get(operator) or []
        effect_set = {_effect_id(e) for e in effect_list if _effect_id(e)}
        if effect_set or (operator == "all" and effects.get(operator) == []):
            groups.append({"operator": operator, "effects": effect_set})

    return groups


def _is_trigger_satisfied(trigger_groups: list[dict], current_effects: set[str]) -> bool:
    """判断触发条件组是否被当前 effects 满足。

    组间隐含 AND 关系：每组都必须满足。
    "all" 组：所有效果都必须在 current_effects 中。
    "any" 组：至少一个效果在 current_effects 中。
    """
    return all(
        current_effects >= group["effects"] if group["operator"] == "all" else bool(current_effects & group["effects"])
        for group in trigger_groups
    )


def _is_non_skill_enhancement(skill_type: str) -> bool:
    """判断技能类型是否为"非战技"（连携技、终结技等）。"""
    return skill_type in {"连携技", "终结技", "天赋", "潜能"}


def _is_active_skill(skill_type: str) -> bool:
    """判断技能类型是否为"战技"。"""
    return skill_type == "战技"


def _merge_new_effects(current_effects: set[str], enhancement_effects: list[str]) -> bool:
    """合并强化态产出，返回是否新增了效果。"""
    previous_count = len(current_effects)
    current_effects.update(enhancement_effects)
    return len(current_effects) != previous_count


def _apply_non_skill_enhancements(ctx: dict, current_effects: set[str]) -> bool:
    """扫描一次非战技强化态，并合并当前可触发分支的产出。"""
    changed = False
    for enhancements in ctx["enhancement_triggers"].values():
        for enhancement in enhancements:
            if _is_active_skill(enhancement["skill_type"]):
                continue
            trigger_groups = enhancement["trigger_groups"]
            if trigger_groups and _is_trigger_satisfied(trigger_groups, current_effects):
                changed |= _merge_new_effects(current_effects, enhancement["effects"])
    return changed


def _is_self_dependency(ctx: dict, key: tuple[str, str], trigger_groups: list[dict]) -> bool:
    """判断触发组内所有效果是否都只能由当前技能自身产出。"""
    producers_by_effect = ctx.get("effect_producers", {})
    return all(
        not any(producer_key != key for producer_key in producers_by_effect.get(effect_id, []))
        for group in trigger_groups
        for effect_id in group["effects"]
    )


def _apply_skill_enhancement(
    ctx: dict,
    key: tuple[str, str],
    enhancement: dict,
    current_effects: set[str],
    forbidden_skills: set[tuple[str, str]],
) -> bool:
    """处理单个可触发的战技强化态，返回闭包状态是否变化。"""
    if not _is_active_skill(enhancement["skill_type"]):
        return False
    trigger_groups = enhancement["trigger_groups"]
    if not trigger_groups or not _is_trigger_satisfied(trigger_groups, current_effects):
        return False

    changed = False
    if not _is_self_dependency(ctx, key, trigger_groups) and key not in forbidden_skills:
        forbidden_skills.add(key)
        changed = True
    return _merge_new_effects(current_effects, enhancement["effects"]) or changed


def _apply_skill_enhancements(
    ctx: dict,
    current_effects: set[str],
    forbidden_skills: set[tuple[str, str]],
) -> bool:
    """扫描一次战技强化态，并汇总禁止项和新增效果。"""
    changed = False
    for key, enhancements in ctx["enhancement_triggers"].items():
        for enhancement in enhancements:
            changed |= _apply_skill_enhancement(
                ctx,
                key,
                enhancement,
                current_effects,
                forbidden_skills,
            )
    return changed


def _has_break_chain_contribution(skill_effects: set[str]) -> bool:
    """判断技能产出是否属于破防强化链。"""
    return bool(skill_effects & _SHRED_STACK_PRODUCERS)


def _contributes_to_active_enhancement(
    skill_effects: set[str],
    enhancement_triggers: dict,
    current_effects: set[str],
) -> bool:
    """判断技能产出是否命中任一当前可激活强化态的触发组。"""
    for enhancements in enhancement_triggers.values():
        for enhancement in enhancements:
            trigger_groups = enhancement["trigger_groups"]
            if not trigger_groups or not _is_trigger_satisfied(trigger_groups, current_effects):
                continue
            if any(skill_effects & group["effects"] for group in trigger_groups):
                return True
    return False


# ── 核心逻辑：三阶段闭包算法 ────────────────────────────────────────────────


def _build_team_skill_context(
    team_members: list[str],
    characters: dict[str, dict],
) -> dict:
    """扫描队伍所有技能，建立基础数据结构。

    Returns:
        {
            "team_skills": {(char_name, skill_id): skill_dict},
            "skill_types": {(char_name, skill_id): skill_type},
            "skill_effects": {(char_name, skill_id): [effect_id, ...]},
            "enhancement_triggers": {
                (char_name, skill_id): [{"trigger_groups": [...], "effects": [effect], "skill_type": str}, ...]
            },
            "effect_producers": {effect_id: [(char_name, skill_id), ...]},
        }
    """
    team_set = set(team_members)

    team_skills: dict[tuple[str, str], dict] = {}
    skill_types: dict[tuple[str, str], str] = {}
    skill_effects: dict[tuple[str, str], list[str]] = {}
    enhancement_triggers: dict[tuple[str, str], list[dict]] = {}
    effect_producers: dict[str, list[tuple[str, str]]] = {}

    for name, cdata in characters.items():
        if name not in team_set:
            continue
        for s in cdata.get("skills", []):
            skill_id = s.get("skill_id", "")
            key = (name, skill_id)
            team_skills[key] = s
            skill_types[key] = s.get("skill_type", "")

            # 收集技能产出的效果
            effects = []
            for eff in s.get("effects") or []:
                for effect_id in _expanded_produced_effect_ids(eff):
                    effects.append(effect_id)
                    _register_effect_producer(effect_producers, effect_id, key)
            skill_effects[key] = effects

            # 收集增强态的触发条件和产出效果
            enhancements = []
            for enh in s.get("enhancements") or []:
                trigger = enh.get("trigger_condition", {})
                if not isinstance(trigger, dict):
                    continue
                trigger_groups = _parse_trigger_groups(trigger)
                enh_effects = []
                for eff in enh.get("effects") or []:
                    for eid in _expanded_produced_effect_ids(eff):
                        enh_effects.append(eid)
                        _register_effect_producer(effect_producers, eid, key)
                if trigger_groups or enh_effects:
                    enhancements.append(
                        {
                            "trigger_groups": trigger_groups,
                            "effects": enh_effects,
                            "skill_type": s.get("skill_type", ""),
                        }
                    )
            if enhancements:
                enhancement_triggers[key] = enhancements

    return {
        "team_skills": team_skills,
        "skill_types": skill_types,
        "skill_effects": skill_effects,
        "enhancement_triggers": enhancement_triggers,
        "effect_producers": effect_producers,
    }


def _closure_non_skill_enhancements(ctx: dict) -> set[str]:
    """第一阶段：建立"当前已存在效果集"（非战技强化态闭包）。

    初始：队伍内所有技能产出的效果
    循环：遍历所有「战技以外」的强化态触发条件，
          如果触发条件被当前 effects 满足，将该强化态产生的 effects 加入总集
    直到：不再产生新 effects
    """
    current_effects: set[str] = set()
    for effects in ctx["skill_effects"].values():
        current_effects.update(effects)

    while _apply_non_skill_enhancements(ctx, current_effects):
        pass

    return current_effects


def _closure_skill_enhancements(ctx: dict, current_effects: set[str]) -> tuple[set[str], set[tuple[str, str]]]:
    """第二阶段：处理"已经可以触发"的战技强化态。

    遍历所有战技的强化态：
    - 如果触发条件被当前 effects 满足 → 该战技禁止无条件释放
    - 同时将强化态产生的 effects 加入总 effects
    - 继续循环直到稳定

    特殊处理：如果增强态依赖的效果只由该战技自己产出（自我依赖），
    则不禁止该战技，因为增强态的触发需要战技先施放。

    Returns:
        (最终 effects 集合, 禁止释放的战技 key 集合)
    """
    forbidden_skills: set[tuple[str, str]] = set()

    while _apply_skill_enhancements(ctx, current_effects, forbidden_skills):
        pass

    return current_effects, forbidden_skills


def _filter_remaining_skills(
    ctx: dict,
    current_effects: set[str],
    forbidden_skills: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """第三阶段：处理剩余战技，检查 effects 是否对强化链有贡献。

    对于没有被第二阶段禁止的战技：
    - 检查其产出的效果是否对某个**当前已可激活的强化态**有贡献
    - 有贡献 → 保留
    - 无贡献 → 踢出
    """
    allowed_skills: set[tuple[str, str]] = set()

    for key, skill_type in ctx["skill_types"].items():
        if not _is_active_skill(skill_type):
            continue

        if key in forbidden_skills:
            continue

        skill_effects = set(ctx["skill_effects"].get(key, []))
        if not skill_effects:
            continue

        # 检查战技产出的效果是否与任何已可激活的强化态的触发条件有交集
        # 破防链特殊规则：
        # 破防、击飞、倒地、碎甲、猛击统一视为同一条物理强化链。
        has_contribution = _has_break_chain_contribution(skill_effects)
        if not has_contribution:
            has_contribution = _contributes_to_active_enhancement(
                skill_effects,
                ctx["enhancement_triggers"],
                current_effects,
            )

        if has_contribution:
            allowed_skills.add(key)

    return allowed_skills


def build_skill_allowlist(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> dict[int, tuple[bool, str]]:
    """判断队伍中每个角色的战技是否允许手动释放。

    三阶段闭包算法：
      1. 建立"当前已存在效果集"（非战技强化态闭包）
      2. 处理"已经可以触发"的战技强化态（战技强化态闭包）
      3. 处理剩余战技，检查 effects 是否对强化链有贡献

    Args:
        team_members: 4 个角色名，索引 0-3 对应技能键 "1"-"4"
        characters:   角色数据字典（可选，默认加载）

    Returns:
        {位置索引: (是否允许, 阻止原因)}
    """
    if characters is None:
        characters = load_characters()

    if not characters:
        return dict.fromkeys(range(len(team_members)), (True, ""))

    # ── 第零阶段：建立基础数据结构 ──
    ctx = _build_team_skill_context(team_members, characters)

    # ── 第一阶段：非战技强化态闭包 ──
    current_effects = _closure_non_skill_enhancements(ctx)

    # ── 第二阶段：战技强化态闭包 ──
    current_effects, forbidden_skills = _closure_skill_enhancements(ctx, current_effects)

    # ── 第三阶段：筛选剩余战技 ──
    allowed_skills = _filter_remaining_skills(ctx, current_effects, forbidden_skills)

    # ── 兜底：如果允许集合为空，返回全部战技 ──
    all_active_skills = {key for key, stype in ctx["skill_types"].items() if _is_active_skill(stype)}
    if not allowed_skills:
        allowed_skills = all_active_skills

    # ── 转换为输出格式 ──
    result: dict[int, tuple[bool, str]] = {}
    for idx, char_name in enumerate(team_members):
        char_data = characters.get(char_name)
        if not char_data:
            result[idx] = (True, "")
            continue

        skill = None
        for s in char_data.get("skills", []):
            if s.get("skill_type") == "战技":
                skill = s
                break
        if not skill:
            result[idx] = (True, "")
            continue

        key = (char_name, skill.get("skill_id", ""))

        if char_name in EXEMPT_CHARACTERS:
            result[idx] = (True, "")
            continue
        if char_name in NO_ALLOW_CHARACTERS:
            result[idx] = (False, "战技基本无用")
            continue

        if key in forbidden_skills:
            result[idx] = (False, "增强态优先")
        elif key in allowed_skills:
            result[idx] = (True, "")
        else:
            result[idx] = (False, "效果无依赖")

    return result


# ── 生成技能释放序列 ────────────────────────────────────────────────

_NORMAL_SKILL_TOKENS = {"1", "2", "3", "4"}


def generate_skill_sequence(
    team_members: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据队伍编队直接生成允许的战技释放序列。"""
    allowlist = build_skill_allowlist(team_members, characters)
    result = [str(i + 1) for i, (ok, _) in allowlist.items() if ok]
    return result if result else [str(i + 1) for i in range(len(team_members))]


def filter_skill_sequence(
    team_members: list[str],
    skill_sequence: list[str],
    characters: dict[str, dict] | None = None,
) -> list[str]:
    """根据自动技能列表过滤技能释放序列。"""
    allowlist = build_skill_allowlist(team_members, characters)
    allowed_digits = {str(i + 1) for i, (ok, _) in allowlist.items() if ok}

    filtered = []
    for token in skill_sequence:
        if token in _NORMAL_SKILL_TOKENS:
            if token in allowed_digits:
                filtered.append(token)
        else:
            filtered.append(token)

    return filtered if filtered else list(skill_sequence)
