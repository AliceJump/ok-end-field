#!/usr/bin/env python3
"""
角色技能数据生成脚本
从wiki爬取的原始JSON数据生成格式化的角色技能JSON文件
"""

import json
import re
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets" / "data" / "character_skills"
TMP_DIR = ROOT

# Wiki itemId -> game pinyin ID 映射
ITEM_ID_TO_GAME_ID = {
    "2117": "pu_qie_na",
    "2116": "ti_fu_luo_si",
    "1683": "li_nuo",
    "1675": "jue",
    "1280": "ka_miao",
    "1358": "mi_fu",
    "1132": "zhuang_fang_yi",
    "8": "luo_qian",
    "1130": "tang_tang",
    "157": "yi_feng",
    "159": "jie_er_pei_ta",
    "7": "lai_wan_ting",
    "89": "guan_li_yuan_nan",
    "156": "guan_li_yuan_nv",
    "131": "jun_wei",
    "24": "yu_jin",
    "4": "bie_li",
    "130": "li_feng",
    "11": "ai_er_dai_la",
    "5": "pei_li_ka",
    "12": "chen_qian_yu",
    "3": "lang_wei",
    "57": "hu_guang",
    "150": "sai_xi",
    "87": "a_lie_shi",
    "13": "da_pan",
    "88": "ai_wei_wen_na",
    "90": "zhou_xue",
    "95": "qiu_li",
    "93": "ai_te_la",
    "94": "ka_qi_er",
    "92": "ying_shi",
    "91": "an_ta_er",
}

# 文件名映射
FILENAME_MAP = {
    "pu_qie_na": "puqiena",
    "ti_fu_luo_si": "tifuluosi",
    "li_nuo": "liino",
    "jue": "lizhiyan",
    "ka_miao": "camille",
    "mi_fu": "mifu",
    "zhuang_fang_yi": "zhuangfy",
    "luo_qian": "wulfa",
    "tang_tang": "tangtang",
    "yi_feng": "yvonne",
    "jie_er_pei_ta": "aglina",
    "lai_wan_ting": "laevat",
    "guan_li_yuan_nan": "endmin_m",
    "guan_li_yuan_nv": "endmin_f",
    "jun_wei": "pograni",
    "yu_jin": "azrila",
    "bie_li": "lastrite",
    "li_feng": "lifeng",
    "ai_er_dai_la": "ardelia",
    "pei_li_ka": "pelica",
    "chen_qian_yu": "chenqianyu",
    "lang_wei": "wolfgd",
    "hu_guang": "ikut",
    "sai_xi": "seraph",
    "a_lie_shi": "deepfin",
    "da_pan": "dapan",
    "ai_wei_wen_na": "avywen",
    "zhou_xue": "aurora",
    "qiu_li": "karin",
    "ai_te_la": "whiten",
    "ka_qi_er": "meurs",
    "ying_shi": "bounda",
    "an_ta_er": "antal",
}


def clean_html_tags(text: str) -> str:
    """清理HTML标签和特殊Unicode转义序列"""
    if not text:
        return ""
    # 清理所有u003c...u003e标签
    text = re.sub(r'u003c.*?u003e', '', text)
    # 清理其他HTML标签
    text = re.sub(r'<[^>]+>', '', text)
    # 清理多余空白和换行
    text = re.sub(r'\n\s*\n', '\n', text)
    text = text.strip()
    return text


def extract_effects_from_description(description: str) -> dict:
    """从描述中提取各种效果"""
    effects = {
        "attach_effects": [],
        "status_effects": [],
        "enhancement_conditions": [],
        "clear_effects": [],
    }
    
    if not description:
        return effects
    
    # 清理描述文本
    cleaned_desc = clean_html_tags(description)
    
    # 施加附着效果
    attach_patterns = {
        r"施加寒冷附着|寒冷附着": "ATTACH_COLD",
        r"施加自然附着|自然附着": "ATTACH_NATURAL",
        r"施加灼热附着|灼热附着": "ATTACH_BURN",
        r"施加电磁附着|电磁附着": "ATTACH_ELECTROMAGNETIC",
        r"施加腐蚀|腐蚀": "ATTACH_CORRODE",
        r"强制施加冻结|强制冻结|施加冻结": "STATUS_FROZEN",
        r"施加缓速": "STATUS_SLOW",
        r"强制造成击飞|施加击飞": "STATUS_HEAVY_HIT",
        r"施加物理脆弱": "VULN_PHYSICAL",
        r"法术脆弱": "VULN_ALL",
        r"寒冷脆弱": "VULN_COLD",
        r"施加法术异常|法术异常": "STATUS_ABNORMAL",
        r"施加击退": "STATUS_KNOCKBACK",
        r"施加眩晕": "STATUS_STUN",
    }
    
    for pattern, effect_id in attach_patterns.items():
        if re.search(pattern, cleaned_desc):
            if effect_id not in effects["attach_effects"]:
                effects["attach_effects"].append(effect_id)
    
    # 清除附着效果
    clear_patterns = {
        r"消耗所有寒冷附着|消耗.*寒冷附着": "CLEAR_COLD",
        r"消耗所有自然附着|消耗.*自然附着": "CLEAR_NATURAL",
        r"消耗所有法术附着|消耗.*法术附着": "CLEAR_ALL",
        r"消耗所有腐蚀|消耗.*腐蚀": "CLEAR_CORRODE",
        r"消耗.*附着": "CLEAR_ATTACH",
    }
    
    for pattern, effect_id in clear_patterns.items():
        if re.search(pattern, cleaned_desc):
            if effect_id not in effects["clear_effects"]:
                effects["clear_effects"].append(effect_id)
    
    # 强化条件
    condition_patterns = [
        r"当有敌人(?:被施加|进入|处于)([^时]+)时可以发动",
        r"当[^时]+时可以发动",
        r"若目标处于([^，。]+)",
        r"命中处于([^，。]+)的敌人时",
    ]
    
    for pattern in condition_patterns:
        matches = re.findall(pattern, cleaned_desc)
        for match in matches:
            match = match.strip()
            if match and match not in effects["enhancement_conditions"]:
                effects["enhancement_conditions"].append(match)
    
    return effects


def parse_skill(skill_data: dict) -> dict:
    """解析单个技能数据"""
    # 直接从JSON字段提取
    description = skill_data.get("description", "")
    
    # 提取效果
    extracted_effects = extract_effects_from_description(description)
    
    result = {
        "name": skill_data.get("name", ""),
        "skill_type": skill_data.get("skill_type", ""),
        "description": description,
        "attach_effects": extracted_effects["attach_effects"],
        "status_effects": extracted_effects["status_effects"],
        "clear_effects": extracted_effects["clear_effects"],
        "enhancement_conditions": extracted_effects["enhancement_conditions"],
    }
    
    return result


def parse_character(char_data: dict) -> dict:
    """解析单个角色数据"""
    item_id = char_data.get("itemId", "")
    game_id = ITEM_ID_TO_GAME_ID.get(item_id, item_id)
    
    skills = []
    for skill_data in char_data.get("skills", []):
        skill = parse_skill(skill_data)
        skills.append(skill)
    
    # 尝试从已有角色文件中复用元数据，避免覆盖为默认空值
    existing_file = ASSETS_DIR / f"{game_id}.json"
    existing_meta = {}
    if existing_file.exists():
        with open(existing_file, encoding='utf-8') as ef:
            existing_meta = json.load(ef)

    result = {
        "character_id": game_id,
        "wiki_item_id": item_id,
        "name": char_data.get("name", ""),
        "star": char_data.get("star") or existing_meta.get("star", 0),
        "element": char_data.get("element") or existing_meta.get("element", ""),
        "profession": char_data.get("profession") or existing_meta.get("profession", ""),
        "weapon_type": char_data.get("weapon_type") or existing_meta.get("weapon_type", ""),
        "skills": skills,
    }
    
    return result


def generate_character_file(char_data: dict) -> str:
    """生成单个角色的JSON文件"""
    game_id = char_data["character_id"]
    filename = FILENAME_MAP.get(game_id, game_id)
    filepath = ASSETS_DIR / f"{filename}.json"
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(char_data, f, ensure_ascii=False, indent=4)
    
    return filepath.name


def generate_summary(all_chars: list[dict]) -> dict:
    """生成汇总信息"""
    summary = {
        "total_characters": len(all_chars),
        "elements": {},
        "attach_skills": {
            "ATTACH_COLD": [],
            "ATTACH_NATURAL": [],
            "ATTACH_BURN": [],
            "ATTACH_ELECTROMAGNETIC": [],
        },
        "clear_skills": {
            "CLEAR_COLD": [],
            "CLEAR_NATURAL": [],
            "CLEAR_ALL": [],
        },
    }
    
    for char in all_chars:
        element = char["element"]
        summary["elements"][element] = summary["elements"].get(element, 0) + 1
        
        for skill in char["skills"]:
            for attach in skill["attach_effects"]:
                if attach in summary["attach_skills"]:
                    summary["attach_skills"][attach].append({
                        "character": char["name"],
                        "skill": skill["name"],
                        "skill_type": skill["skill_type"],
                    })
            
            for clear in skill["clear_effects"]:
                if clear in summary["clear_skills"]:
                    summary["clear_skills"][clear].append({
                        "character": char["name"],
                        "skill": skill["name"],
                        "skill_type": skill["skill_type"],
                    })
    
    return summary


def main():
    # 读取爬取的原始数据
    input_file = TMP_DIR / "tmp_all_characters_full.json"
    with open(input_file, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    print(f"Loaded {len(raw_data)} characters from {input_file.name}")
    
    # 确保输出目录存在
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 解析并生成每个角色的数据
    all_chars = []
    generated_files = []
    for char_data in raw_data:
        parsed = parse_character(char_data)
        all_chars.append(parsed)
        filename = generate_character_file(parsed)
        generated_files.append(filename)
    
    # 生成汇总信息
    summary = generate_summary(all_chars)
    
    # 保存汇总信息
    summary_file = TMP_DIR / "tmp_character_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=4)
    
    # 输出结果
    print(f"\nGenerated {len(generated_files)} character files:")
    for f in sorted(generated_files):
        print(f"  - {f}")
    
    print("\nSummary:")
    print(f"  Total characters: {summary['total_characters']}")
    print(f"  Elements: {summary['elements']}")
    
    print("\nAttach skills (能施加附着的技能):")
    for attach_type, skills in summary["attach_skills"].items():
        if skills:
            print(f"\n  {attach_type}:")
            for s in skills:
                print(f"    - {s['character']}: {s['skill']} ({s['skill_type']})")
    
    print("\nClear skills (能消耗附着的技能):")
    for clear_type, skills in summary["clear_skills"].items():
        if skills:
            print(f"\n  {clear_type}:")
            for s in skills:
                print(f"    - {s['character']}: {s['skill']} ({s['skill_type']})")


if __name__ == "__main__":
    main()
