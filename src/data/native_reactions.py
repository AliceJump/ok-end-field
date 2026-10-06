"""Client SkillSetting rows; reaction levels select rows, not invented multipliers."""

from __future__ import annotations

from src.data.native_gameplay import native_asset, native_number, native_record


def reaction_parameters():
    settings = native_asset("SkillSetting")
    rows = {r["key"]: tuple(r["values"]) for r in settings["spellInflictionDataList"]}
    result = {}
    from src.data.skill_timing import load_skill_timings

    buff = native_record(load_skill_timings(), "buff_physical_no_guard")["data"]
    parameters = {r["key"]: r["valueDouble"] for r in buff["blackboard"] if not r["valueStr"]}
    duration = native_number(buff["duration"], parameters)
    if duration is None:
        raise ValueError("Missing native physical-pool lifetime")
    result["physical.shred.duration"] = duration
    for effect, key in (("STATUS_HEAVY_HIT", "击飞伤害"), ("STATUS_KNOCKDOWN", "倒地伤害"),
                        ("STATUS_HEAVY_STRIKE", "猛击伤害"), ("STATUS_SHATTER", "碎甲伤害")):
        for level, value in enumerate(rows[key], 1):
            result[f"physical.{effect}.multiplier.{level}"] = value
    for level, value in enumerate(rows["碎甲物理伤害提高"], 1):
        result[f"physical.STATUS_SHATTER.damage_taken.{level}"] = value
    for level, value in enumerate(rows["碎甲持续时间"], 1):
        result[f"physical.STATUS_SHATTER.duration.{level}"] = value
    return result


def reaction_enhancement(key, arts_strength):
    """Native Attributes.GetPhysicalAndSpellInflictionEnhancedValue arithmetic.

    Linear returns A*x; InverseProportion returns A*x/(B+x). The callers
    multiply by one plus that enhancement. Method IDs, byte-window hashes and
    the checked arithmetic are recorded in native-combat-execution-audit.md.
    """
    settings = native_asset("SkillSetting")
    row = next(r for r in settings["physicalAndSpellInflictionEnhanceFormulaList"] if r["key"] == key)
    if row["formulaType"] == 1:
        return row["paramA"] * arts_strength
    if row["formulaType"] == 2:
        return row["paramA"] * arts_strength / (row["paramB"] + arts_strength)
    if row["formulaType"] == 0:
        return 0.0
    raise ValueError("Unknown native reaction enhancement formula")
