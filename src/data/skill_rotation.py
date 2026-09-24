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
- 主指标 = 该角色「战技」的暴击期望（crit_expect，含 5%/50% 基础暴击）；
- 基准数据缺失的角色（含未识别成员 "?"）视为 0 伤害，排在已知角色之后，
  同伤害按队位顺序稳定排列。
"""

from __future__ import annotations

import json
from pathlib import Path

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


def load_damage_baseline(path: Path | None = None) -> dict[str, float]:
    """加载基准数据，返回 {角色名: 战技暴击期望}。

    角色无战技（纯辅助）或字段缺失时取 non_crit 兜底，再兜 0。
    """
    global _cached_damage
    if _cached_damage is not None:
        return _cached_damage

    result: dict[str, float] = {}
    p = path or _BASELINE_FILE
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = []
        for entry in data if isinstance(data, list) else []:
            name = str(entry.get("character") or "").strip()
            if not name:
                continue
            best = 0.0
            for skill in entry.get("skills") or []:
                if skill.get("type") != "战技":
                    continue
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


def clear_cache() -> None:
    """清除缓存（测试用 / 基准数据更新后调用）。"""
    global _cached_damage
    _cached_damage = None


def _damage_sorted_slots(
    team_members: list[str],
    baseline: dict[str, float],
) -> list[int]:
    """按战技期望伤害降序返回槽位索引（0 基）。

    同伤害按队位升序稳定排列；无数据时即队位顺序。
    """
    slots = [
        (0.0 if name == "?" else float(baseline.get(name, 0.0)), i)
        for i, name in enumerate(team_members)
    ]
    slots.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i in slots]


def generate_damage_rotation(
    team_members: list[str],
    baseline: dict[str, float] | None = None,
) -> list[str]:
    """按战技期望伤害降序返回技能槽位 token 列表（"1"-"4"）。

    Args:
        team_members: 4 个角色名，索引 0-3 对应技能键 "1"-"4"（"?" 为未识别）。
        baseline: {角色名: 期望伤害}；None 时加载 damage_baseline.json。

    Returns:
        按伤害降序的槽位 token；全部未知/缺数据时退化为队位顺序。
    """
    if baseline is None:
        baseline = load_damage_baseline()
    return [str(i + 1) for i in _damage_sorted_slots(team_members, baseline)]


def generate_auto_rotation(
    team_members: list[str],
    baseline: dict[str, float] | None = None,
    include_ult: bool = True,
) -> list[str]:
    """生成可重复循环的自动排轴 token 序列。

    每轮循环 = 按伤害降序遍历队内各号位：
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
        baseline: {角色名: 期望伤害}；None 时加载 damage_baseline.json。
        include_ult: 是否把终结技（ult_N）排入轴。协议空间（开局全满）传 True，
            普通战斗（能量从零攒）传 False。

    Returns:
        循环轴 token 列表（执行器按 ``% len`` 无限循环）。
    """
    if baseline is None:
        baseline = load_damage_baseline()

    rotation: list[str] = []
    for seg, slot in enumerate(_damage_sorted_slots(team_members, baseline)):
        token = str(slot + 1)
        rotation.append(token)
        if include_ult:
            rotation.append(f"ult_{token}")
        if seg in _LINK_AFTER_SEGMENT:
            rotation.append("e")
        rotation.append(f"normal_{_SP_REGEN_SECONDS}")
    return rotation
