# -*- coding: utf-8 -*-
import json, sys, re
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "data" / "character_skills"


def extract_effects_from_desc(desc: str) -> dict:
    """从描述中提取效果信息"""
    effects = {
        'attach_effects': [],
        'status_effects': [],
        'clear_effects': [],
        'enhancement_conditions': []
    }
    
    # 附着效果
    if '自然附着' in desc:
        effects['attach_effects'].append('ATTACH_NATURAL')
    if '寒冷附着' in desc:
        effects['attach_effects'].append('ATTACH_COLD')
    if '灼烧附着' in desc or '灼热附着' in desc:
        effects['attach_effects'].append('ATTACH_BURN')
    if '电磁附着' in desc:
        effects['attach_effects'].append('ATTACH_ELECTROMAGNETIC')
    if '导电' in desc:
        effects['attach_effects'].append('ATTACH_ELECTROMAGNETIC')
    
    # 状态效果
    if '击飞' in desc:
        effects['status_effects'].append('STATUS_HEAVY_HIT')
    if '冻结' in desc:
        effects['status_effects'].append('STATUS_FROZEN')
    if '缓速' in desc or '减速' in desc:
        effects['status_effects'].append('STATUS_SLOW')
    if '失衡' in desc:
        effects['status_effects'].append('STATUS_STAGGER')
    if '破防' in desc:
        effects['status_effects'].append('STATUS_BROKEN')
    
    # 清除效果
    if '消耗' in desc and '附着' in desc:
        effects['clear_effects'].append('CLEAR_ATTACH')
    if '吸收' in desc and '附着' in desc:
        effects['clear_effects'].append('CLEAR_ATTACH')
    
    # 脆弱效果
    if '物理脆弱' in desc:
        effects['status_effects'].append('VULN_PHYSICAL')
    if '法术脆弱' in desc:
        effects['status_effects'].append('VULN_ALL')
    if '寒冷脆弱' in desc:
        effects['status_effects'].append('VULN_COLD')
    
    # 治疗效果
    if '恢复' in desc and ('生命' in desc or '血量' in desc or 'HP' in desc):
        effects['status_effects'].append('BUFF_HEAL')
    if '治疗' in desc:
        effects['status_effects'].append('BUFF_HEAL')
    
    # 护盾效果
    if '护盾' in desc:
        effects['status_effects'].append('BUFF_SHIELD')
    
    # 伤害提升
    if '伤害提升' in desc or '伤害增加' in desc:
        effects['status_effects'].append('BUFF_DAMAGE_UP')
    if '物理伤害提升' in desc:
        effects['status_effects'].append('BUFF_PHYSICAL_UP')
    if '法术伤害提升' in desc:
        effects['status_effects'].append('BUFF_ALL_UP')
    
    return effects


def extract_stagger(desc: str) -> int:
    """提取失衡值"""
    match = re.search(r'(\d+)点失衡', desc)
    return int(match.group(1)) if match else 0


def update_skill_fields(skill: dict):
    """根据描述更新技能字段"""
    desc = skill.get('description', '')
    
    # 提取效果
    effects = extract_effects_from_desc(desc)
    
    # 合并效果（保留原有的，添加新的）
    old_effects = skill.get('effects', [])
    old_effect_ids = [e.get('effect_id') for e in old_effects]
    
    for e in effects['attach_effects']:
        if e not in old_effect_ids:
            old_effects.append({'effect_id': e, 'value': 1, 'duration': '', 'target': 'enemy'})
    
    for e in effects['status_effects']:
        if e not in old_effect_ids:
            old_effects.append({'effect_id': e, 'value': 1, 'duration': '', 'target': 'enemy'})
    
    skill['effects'] = old_effects
    
    # 提取失衡值（只在有明确数值时更新）
    stagger = extract_stagger(desc)
    if stagger > 0:
        skill['stagger_value'] = stagger
    
    return skill


def main():
    updated = 0
    
    for json_file in sorted(ASSETS_DIR.glob("*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        changed = False
        for skill in data.get('skills', []):
            old_effects = skill.get('effects', [])
            old_stagger = skill.get('stagger_value', 0)
            
            update_skill_fields(skill)
            
            if skill['effects'] != old_effects or skill['stagger_value'] != old_stagger:
                changed = True
                print(f"{json_file.stem}: {skill['skill_type']}")
                if skill['effects'] != old_effects:
                    print(f"  effects: {old_effects} -> {skill['effects']}")
                if skill['stagger_value'] != old_stagger:
                    print(f"  stagger: {old_stagger} -> {skill['stagger_value']}")
        
        if changed:
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            updated += 1
    
    print(f"\nUpdated {updated} files")


if __name__ == "__main__":
    main()
