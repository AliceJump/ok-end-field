#!/usr/bin/env python3
"""
角色技能数据校验脚本

检查 assets/data/character_skills/*.json 中的 effects 与 description 一致性：
- 每种 effect_id 的描述中必须包含对应的关键词，否则报警
- 用法:
    python scripts/validate_character_skills.py              # 校验所有角色
    python scripts/validate_character_skills.py pelica       # 校验指定角色
    python scripts/validate_character_skills.py --fix        # 自动修复（移除不匹配的effect）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# effect_id 前缀 → 描述中必须出现的关键词（至少命中一个）
EFFECT_KEYWORD_RULES: dict[str, list[str]] = {
    # 元素附着
    "ATTACH_COLD":             ["寒冷附着", "冻结附着"],
    "ATTACH_BURN":             ["灼热附着", "燃烧附着"],
    "ATTACH_ELECTROMAGNETIC":  ["电磁附着", "导电附着"],
    "ATTACH_NATURAL":          ["自然附着", "腐蚀附着"],

    # 法术异常状态
    "STATUS_FROZEN":      ["冻结"],
    "STATUS_BURNING":     ["燃烧"],
    "STATUS_CONDUCTING":  ["导电"],
    "STATUS_CORROSION":   ["腐蚀"],
    "STATUS_HEAVY_HIT":   ["击飞"],
    "STATUS_KNOCKDOWN":   ["倒地"],
    "STATUS_SHRED":       ["破防"],
    "STATUS_SHATTER":     ["碎甲"],
    "STATUS_HEAVY_STRIKE":["猛击"],
    "STATUS_SLOW":        ["减速", "缓速"],
    "STATUS_STAGGER":     ["失衡"],

    # 脆弱
    "VULN_ALL":            ["法术脆弱", "脆弱", "虚弱"],
    "VULN_PHYSICAL":       ["物理脆弱"],
    "VULN_COLD":           ["寒冷脆弱"],
    "VULN_BURN":           ["灼热脆弱"],
    "VULN_ELECTROMAGNETIC":["电磁脆弱"],
    "VULN_NATURAL":        ["自然脆弱"],

    # 增益
    "BUFF_HEAL":          ["治疗"],
    "BUFF_SHIELD":        ["护盾"],
    "BUFF_DAMAGE_UP":     ["伤害提高", "伤害提升", "伤害增加", "伤害强化"],
    "BUFF_ATTACK_UP":     ["攻击力提升", "攻击力增加", "攻击力提高"],
    "BUFF_CRIT_RATE_UP":  ["暴击率"],
    "BUFF_CRIT_DMG_UP":   ["暴击伤害"],

    # 减益
    "DEBUFF_DEF_DOWN":    ["防御力降低", "防御降低"],
    "DEBUFF_SPEED_DOWN":  ["减速"],
    "DEBUFF_HEAL_DOWN":   ["治疗降低"],

    # 触发
    "TRIGGER_ADDITIONAL": ["追加攻击"],
    "TRIGGER_HEAL":       ["治疗", "恢复"],
    "TRIGGER_SHIELD":     ["护盾"],

    # 清除
    "CLEAR_COLD":    ["冻结"],
    "CLEAR_NATURAL": ["腐蚀"],
    "CLEAR_FROZEN":  ["冻结", "消耗"],

    # 机制
    "MECH_VACUUM":        ["聚怪", "吸引"],
    "STATUS_SPELL_INFLICT":["法术附着"],
    "STATUS_SPELL_BURST":  ["法术爆发"],
    "STATUS_SPELL_ANOMALY":["法术异常"],
}

# 不需要关键词校验的效果（被动/系统状态，描述中不一定直接提及）
SKIP_EFFECTS = {
    "STATUS_BROKEN",       # 破防状态（系统内部）
}


def validate_file(filepath: Path, fix: bool = False) -> list[dict]:
    """校验单个角色技能文件，返回问题列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    char_name = data.get("name", filepath.stem)
    issues = []
    modified = False

    for skill in data.get("skills", []):
        desc = skill.get("description", "")
        skill_name = skill.get("name", "?")
        new_effects = []

        for eff in skill.get("effects", []):
            eid = eff.get("effect_id", "")
            if eid in SKIP_EFFECTS or eid not in EFFECT_KEYWORD_RULES:
                new_effects.append(eid if isinstance(eid, dict) else eff)
                continue

            required_keywords = EFFECT_KEYWORD_RULES[eid]
            matched = any(kw in desc for kw in required_keywords)

            if not matched:
                issue = {
                    "file": filepath.name,
                    "char": char_name,
                    "skill": skill_name,
                    "effect": eid,
                    "required": required_keywords,
                    "desc_excerpt": desc[:100],
                }
                issues.append(issue)

                if fix:
                    modified = True
                    # 不加入 new_effects → 移除该 effect
                    continue

            new_effects.append(eff)

        if fix and modified:
            skill["effects"] = new_effects

    if fix and modified:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    return issues


def main():
    parser = argparse.ArgumentParser(description="校验角色技能 effects 与 description 一致性")
    parser.add_argument("characters", nargs="*", help="指定角色文件名（不含.json），不指定则校验全部")
    parser.add_argument("--fix", action="store_true", help="自动修复：移除描述中无关键词的effect")
    parser.add_argument("--data-dir", default="assets/data/character_skills", help="数据目录")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"❌ 数据目录不存在: {data_dir}", file=sys.stderr)
        sys.exit(1)

    if args.characters:
        files = [data_dir / f"{c}.json" for c in args.characters]
        files = [f for f in files if f.exists()]
    else:
        files = sorted(data_dir.glob("*.json"))

    all_issues = []
    for fp in files:
        issues = validate_file(fp, fix=args.fix)
        all_issues.extend(issues)

    if all_issues:
        print(f"\n⚠️  发现 {len(all_issues)} 个问题：\n")
        for i, iss in enumerate(all_issues, 1):
            print(f"  {i}. {iss['file']} | {iss['char']}.{iss['skill']}")
            print(f"     effect: {iss['effect']}")
            print(f"     要求关键词: {', '.join(iss['required'])}")
            print(f"     描述: {iss['desc_excerpt']}")
            print()

        if args.fix:
            print("✅ 已自动修复（移除不匹配的effect）")
        else:
            print("💡 运行 --fix 可自动修复")
        sys.exit(1)
    else:
        print("✅ 所有角色技能数据校验通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
