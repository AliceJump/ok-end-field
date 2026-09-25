"""伤害优先自动排轴：按各角色战技的实际伤害排序，生成可重复循环的完整轴。

数据来源：``assets/data/damage_baseline.json``（scripts/skill-data/compute_damage_baseline.py
产出，官方 WIKI 满配口径：90 级 + 推荐武器基质 + 毕业装备，标准假人）。

两种产物：
- ``generate_damage_rotation``：战技槽位 token 列表（"1"-"4"），供普通模式循环释放；
- ``generate_auto_rotation``：完整循环轴，涵盖队内全部 1-4 号位的战技（数字键）、
  终结技（ult_N）与连携技窗口（e），段间以普通战斗填充段回技力，供排轴执行器
  ``% len`` 无限循环。终结技/连携未就绪时由执行端跳过推进，下一轮再试。
  ``include_ult=False`` 为冷启动轴（协议空间外的普通战斗：开局终结技不可用，
  不排 ult，由填充段兜底通道就绪后释放）。

排序规则（两者一致）：
- 主指标 = 队伍感知口径的循环期望（load_damage_baseline_for_team：满口径
  依赖不满足时回退保守口径；队伍有连击施加者时用满连击口径）；
- 资源喂养约束：满口径依赖某元素附着的角色（如提弗洛斯的满猎矢口径需
  消耗自然附着获得启示），可施加该元素附着的队友先于其出手，使首轮循环
  即吃满口径（_ordered_slots_with_dependencies，伤害降序为贪心权重）；
- 基准数据缺失的角色（含未识别成员 "?"）视为 0 伤害，排在已知角色之后，
  同伤害按队位顺序稳定排列。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.data.character_capabilities import load_character_capabilities

_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_FILE = _ROOT / "assets" / "data" / "damage_baseline.json"

# 填充段时长（秒）：技力全队共享（战技 100/个），恢复速率 ≈8/秒（12.5s/100，
# 社区值，见 docs/dev/combat-system/ROTATION_REQUIREMENTS.md P1-4）。
# 每段填充 ≈ 恢复一个战技的技力，整轮 SP 收支平衡（4 战技 400 = 4 段 x 12.5s x 8）。
_SP_REGEN_SECONDS = 12.5

# 在第几个角色段之后插入连携窗口（0 基）：连携免费且有就绪窗口，
# 每轮尝试 2 次，未就绪由执行端跳过。
_LINK_AFTER_SEGMENT = (0, 2)

_cached_damage: dict[str, float] | None = None
_cached_team_damage: dict[tuple, dict[str, float]] = {}
_cached_team_entries: dict[tuple, dict[str, dict]] = {}


def _read_entries(path: Path | None) -> list[dict]:
    """读取基准文件原始条目（损坏/缺失时返回空列表）。"""
    p = path or _BASELINE_FILE
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def load_damage_baseline(path: Path | None = None) -> dict[str, float]:
    """加载基准数据，返回 {角色名: 循环期望伤害}。

    优先取顶层 cycle_expect（战技完整伤害 + 连携 + 2x 普攻，含召唤物/多段
    机制修正，见 compute_damage_baseline.py）；缺失时回退单发战技暴击期望
    （non_crit 兜底），再兜 0。
    """
    global _cached_damage
    if _cached_damage is not None:
        return _cached_damage

    result: dict[str, float] = {}
    for entry in _read_entries(path):
        name = str(entry.get("character") or "").strip()
        if not name:
            continue
        raw_cycle = entry.get("cycle_expect")
        if raw_cycle is not None:
            try:
                result[name] = float(raw_cycle)
                continue
            except (TypeError, ValueError):
                pass
        best = 0.0
        for skill in entry.get("skills") or []:
            if skill.get("type") != "战技":
                continue
            value = skill.get("full_expect")
            if value is None:
                value = skill.get("crit_expect")
            if value is None:
                value = skill.get("non_crit")
            try:
                best = max(best, float(value or 0))
            except (TypeError, ValueError):
                continue
        result[name] = best
    _cached_damage = result
    return result


def load_team_baseline_entries(
    team_members: list[str],
    path: Path | None = None,
    capabilities: dict | None = None,
) -> dict[str, dict]:
    """队伍感知口径 + 满口径依赖约束（供依赖感知排序消费）。

    返回 {角色名: {"value": 该队伍构成下的排序期望,
                    "requires_attach": list[str] | None}}：

    - value 口径选择规则同 load_damage_baseline_for_team（满口径依赖
      不满足时回退保守口径；队伍有连击施加者时用满连击口径）；
    - requires_attach 仅当该角色满口径依赖被队伍满足时为元素列表——此时
      排序需保证「可施加该元素附着的成员」先于该角色出手（资源喂养约束，
      见 _ordered_slots_with_dependencies）；依赖不满足（已回退保守口径）
      或无依赖条目时为 None。

    Args:
        team_members: 队伍角色名列表（"?" 为未识别，忽略）。
        path: 基准文件路径；None 时用 damage_baseline.json。
        capabilities: 注入能力表（测试用）；None 时加载真实快照。

    Returns:
        仅含基准文件中存在的角色；结果按（队伍, 路径）缓存。
    """
    cache_key = (tuple(m or "?" for m in team_members), str(path) if path is not None else "")
    cached = _cached_team_entries.get(cache_key)
    if cached is not None:
        return cached

    full = load_damage_baseline(path)
    caps = capabilities if capabilities is not None else load_character_capabilities()
    team = [m for m in cache_key[0] if m != "?"]
    has_combo = any(
        (caps.get(m).combo_applier if caps.get(m) else False) for m in team
    )

    result: dict[str, dict] = {}
    for entry in _read_entries(path):
        name = str(entry.get("character") or "").strip()
        if not name:
            continue
        value = full.get(name, 0.0)
        requirement = entry.get("full_caliber_requires") or {}
        need_attach = requirement.get("attach")
        required_elements = [need_attach] if isinstance(need_attach, str) else list(need_attach or [])
        attach_ok = True
        if required_elements:
            attach_ok = any(
                any(
                    e in (caps.get(m).attach_elements if caps.get(m) else ())
                    for e in required_elements
                )
                for m in team
                if m != name
            )
        if not attach_ok:
            # 回退保守口径；保守口径不与满连击口径叠加（后者基于满口径计算）
            conservative = entry.get("cycle_expect_conservative")
            if conservative is not None:
                try:
                    value = float(conservative)
                except (TypeError, ValueError):
                    pass
            requires: list[str] | None = None
        else:
            requires = required_elements or None
            if has_combo:
                link4 = entry.get("cycle_expect_link4")
                if link4 is not None:
                    try:
                        value = float(link4)
                    except (TypeError, ValueError):
                        pass
        result[name] = {"value": value, "requires_attach": requires}
    _cached_team_entries[cache_key] = result
    return result


def load_damage_baseline_for_team(
    team_members: list[str],
    path: Path | None = None,
    capabilities: dict | None = None,
) -> dict[str, float]:
    """队伍感知口径的基准数据，返回 {角色名: 循环期望伤害}。

    在无队伍口径（load_damage_baseline，满口径）之上按队伍构成修正：

    - 满口径依赖回退：条目带 ``full_caliber_requires``（如提弗洛斯的
      ``{"attach": "自然"}``）且队伍（除自身外）没有可施加该元素附着的
      成员时，改用 ``cycle_expect_conservative``（保守口径）；
    - 满连击口径：队伍中有连击施加者（character_capabilities 识别，当前
      仅黎风）且角色未触发上述回退时，改用 ``cycle_expect_link4``
      （战技 x1.75 规划口径，见 compute_damage_baseline.py）。

    Args:
        team_members: 队伍角色名列表（"?" 为未识别，忽略）。
        path: 基准文件路径；None 时用 damage_baseline.json。
        capabilities: 注入能力表（测试用）；None 时加载真实快照。

    Returns:
        {角色名: 该队伍构成下的排序期望}；结果按（队伍, 路径）缓存。
    """
    cache_key = (tuple(m or "?" for m in team_members), str(path) if path is not None else "")
    cached = _cached_team_damage.get(cache_key)
    if cached is not None:
        return cached
    entries = load_team_baseline_entries(team_members, path=path, capabilities=capabilities)
    result = {name: entry["value"] for name, entry in entries.items()}
    _cached_team_damage[cache_key] = result
    return result


def clear_cache() -> None:
    """清除缓存（测试用 / 基准数据更新后调用）。"""
    global _cached_damage
    _cached_damage = None
    _cached_team_damage.clear()
    _cached_team_entries.clear()


def _ordered_slots_with_dependencies(
    team_members: list[str],
    entries: dict[str, dict],
    capabilities: dict | None,
) -> list[int]:
    """伤害降序 + 资源喂养约束的槽位排序（0 基槽位索引）。

    在伤害降序基础上满足约束：若角色 A 的满口径依赖某元素附着
    （entries[name]["requires_attach"] 非空），则可施加该元素附着的队友
    必须先于 A 出手——满倍率的前置资源由队友喂养时，喂养段先手可使首轮
    循环即吃满口径（否则首轮按保守口径空转）。贪心策略：每步在「约束已
    满足」的角色中取伤害最高者（伤害降序即贪心权重）；约束成环（现有
    数据不可达）时整体回退纯伤害序。

    同伤害按队位升序稳定排列；"?"/缺数据角色视为 0 伤害、无约束。
    显式传入 baseline 的调用方无依赖信息（requires_attach 全 None），
    退化为纯伤害降序。
    """
    caps = capabilities if capabilities is not None else load_character_capabilities()
    nodes = []
    for i, name in enumerate(team_members):
        entry = entries.get(name) or {}
        cap = caps.get(name) if caps else None
        nodes.append({
            "slot": i,
            "value": 0.0 if name == "?" else float(entry.get("value", 0.0)),
            "requires": entry.get("requires_attach") or [],
            "inflict": cap.attach_elements if cap else (),
        })
    nodes.sort(key=lambda nd: (-nd["value"], nd["slot"]))
    placed: list[dict] = []
    remaining = list(nodes)
    while remaining:
        for nd in remaining:
            req = nd["requires"]
            fed = (
                not req
                or any(e in nd["inflict"] for e in req)
                or any(e in p["inflict"] for p in placed for e in req)
            )
            if fed:
                placed.append(nd)
                remaining.remove(nd)
                break
        else:
            # 约束成环：回退剩余部分的伤害序（理论上不可达，防御性兜底）
            placed.extend(remaining)
            break
    return [nd["slot"] for nd in placed]


def _explicit_baseline_entries(
    team_members: list[str],
    baseline: dict[str, float],
) -> dict[str, dict]:
    """显式 baseline 转 entries（无依赖信息 → 纯伤害排序）。"""
    return {
        name: {
            "value": 0.0 if name == "?" else float(baseline.get(name, 0.0)),
            "requires_attach": None,
        }
        for name in team_members
    }


def generate_damage_rotation(
    team_members: list[str],
    baseline: dict[str, float] | None = None,
    *,
    path: Path | None = None,
    capabilities: dict | None = None,
) -> list[str]:
    """按伤害降序 + 资源喂养约束返回技能槽位 token 列表（"1"-"4"）。

    Args:
        team_members: 4 个角色名，索引 0-3 对应技能键 "1"-"4"（"?" 为未识别）。
        baseline: {角色名: 期望伤害}；None 时按队伍构成加载
            （load_damage_baseline_for_team 口径）并应用喂养约束；
            显式传入时只做伤害排序（无依赖信息、不查能力表）。
        path: 基准文件路径（仅 baseline 为 None 时生效）。
        capabilities: 注入能力表（测试用）；None 时加载真实快照。

    Returns:
        槽位 token 列表；全部未知/缺数据时退化为队位顺序。
    """
    if baseline is None:
        entries = load_team_baseline_entries(team_members, path=path, capabilities=capabilities)
    else:
        entries = _explicit_baseline_entries(team_members, baseline)
    slots = _ordered_slots_with_dependencies(team_members, entries, capabilities)
    return [str(i + 1) for i in slots]


def generate_auto_rotation(
    team_members: list[str],
    baseline: dict[str, float] | None = None,
    include_ult: bool = True,
    *,
    path: Path | None = None,
    capabilities: dict | None = None,
) -> list[str]:
    """生成可重复循环的自动排轴 token 序列。

    每轮循环 = 按伤害降序 + 喂养约束遍历队内各号位：
      [战技N] [ult_N]（每段，include_ult 时） + [e]（第 1、3 段后） + [normal_12.5]（每段后）

    - 战技（数字键）覆盖队内全部 1-4 号位，游戏内即「切到该号位释放战技」；
    - ult_N / e 由执行端就绪检测，未就绪跳过推进、下一轮循环再试，不卡轴；
    - normal_12.5 填充段保持普通战斗（普攻回技力 + 推荐技能/终结技兜底），
      时长 ≈ 一个战技的技力恢复，整轮 SP 收支平衡（见 _SP_REGEN_SECONDS 注释）。

    冷启动（include_ult=False）：不把终结技排入轴。适用于协议空间之外的
    普通战斗——开局终结技能量未满，轴上的 ult_N 会在前几轮循环全部空转。
    冷启动轴的终结技由填充段的 use_ult() 兜底通道在就绪后自动释放。

    Args:
        team_members: 4 个角色名，索引 0-3 对应队位 1-4（"?" 为未识别）。
        baseline: {角色名: 循环期望伤害}（排序口径，见
            load_damage_baseline_for_team）；None 时按队伍构成加载并应用
            喂养约束；显式传入时只做伤害排序（无依赖信息）。
        include_ult: 是否把终结技（ult_N）排入轴。协议空间（开局全满）传 True，
            普通战斗（能量从零攒）传 False。
        path: 基准文件路径（仅 baseline 为 None 时生效）。
        capabilities: 注入能力表（测试用）；None 时加载真实快照。

    Returns:
        循环轴 token 列表（执行器按 ``% len`` 无限循环）。
    """
    if baseline is None:
        entries = load_team_baseline_entries(team_members, path=path, capabilities=capabilities)
    else:
        entries = _explicit_baseline_entries(team_members, baseline)

    rotation: list[str] = []
    for seg, slot in enumerate(_ordered_slots_with_dependencies(team_members, entries, capabilities)):
        token = str(slot + 1)
        rotation.append(token)
        if include_ult:
            rotation.append(f"ult_{token}")
        if seg in _LINK_AFTER_SEGMENT:
            rotation.append("e")
        rotation.append(f"normal_{_SP_REGEN_SECONDS}")
    return rotation
