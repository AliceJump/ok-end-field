"""伤害优先技能释放序列：按各角色战技的实际伤害排序技能槽位。

数据来源：``assets/data/damage_baseline.json``（scripts/skill-data/compute_damage_baseline.py
产出，官方 WIKI 满配口径：90 级 + 推荐武器基质 + 毕业装备，标准假人）。

排序规则：
- 主指标 = 该角色「战技」的暴击期望（crit_expect，含 5%/50% 基础暴击）；
- 基准数据缺失的角色（含未识别成员 "?"）视为 0 伤害，排在已知角色之后，
  同伤害按队位顺序稳定排列；
- 只排序不过滤：被增强链接管的战技过滤仍由 ``skill_allowlist`` 负责，
  两者通过战斗配置组合使用。

与 ``skill_allowlist.generate_skill_sequence`` 的接口保持一致，
便于在 ``AutoCombatLogic`` 中按配置切换。
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_BASELINE_FILE = _ROOT / "assets" / "data" / "damage_baseline.json"

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

    slots = [
        (str(i + 1), 0.0 if name == "?" else float(baseline.get(name, 0.0)), i)
        for i, name in enumerate(team_members)
    ]
    # 伤害降序；同伤害按队位升序（稳定，保证无数据时即队位顺序）
    slots.sort(key=lambda t: (-t[1], t[2]))
    return [token for token, _, _ in slots]
