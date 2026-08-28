"""技能数据库验证脚本。

对比Wiki数据和解包数据，验证技能信息的准确性。
"""

import json
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def load_i18n_data():
    """加载i18n文本数据。"""
    i18n_path = project_root / "assets" / "data" / "i18n_texts" / "CN.json"
    with open(i18n_path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_reactions(i18n_data):
    """验证反应条件。"""
    reactions = {
        "R001_燃烧触发连携": {
            "keyword": "燃烧",
            "description": "当敌人进入燃烧状态时触发连携技",
            "wiki_source": "莱万汀连携技沸腾",
        },
        "R002_熔火层数触发追加攻击": {
            "keyword": "熔火",
            "description": "熔火层数达到4层时触发战技追加攻击",
            "wiki_source": "莱万汀战技焚灭",
        },
        "R003_冻结触发连携": {
            "keyword": "冻结",
            "description": "敌人处于冻结状态时触发连携技",
            "wiki_source": "伊冯连携技速冻仔·υ37",
        },
        "R004_法术附着消耗触发冻结": {
            "keyword": "附着",
            "description": "战技命中处于法术附着的敌人时消耗附着并施加冻结",
            "wiki_source": "伊冯战技冰冰弹·β型",
        },
        "R005_法术异常触发连携": {
            "keyword": "法术异常",
            "description": "敌人被施加法术异常时触发连携技",
            "wiki_source": "洁尔佩塔连携技矩阵位移",
        },
        "R006_破防增强法术脆弱": {
            "keyword": "破防",
            "description": "目标处于破防状态时增强终结技法术脆弱效果",
            "wiki_source": "洁尔佩塔终结技重力场",
        },
        "R007_受击触发连携": {
            "keyword": "受击",
            "description": "主控干员受到攻击后触发连携技",
            "wiki_source": "余烬连携技前线援护",
        },
        "R008_受击叠加攻击力": {
            "keyword": "攻击力",
            "description": "受到伤害后叠加攻击力层数",
            "wiki_source": "余烬天赋以铁还铁",
        },
        "R009_猛击碎甲消耗破防层数触发连携": {
            "keyword": "碎甲",
            "description": "敌人被猛击或碎甲消耗破防层数后触发连携技",
            "wiki_source": "骏卫连携技盈月邀击",
        },
        "R010_破防层数消耗恢复技力": {
            "keyword": "技力",
            "description": "战技命中敌人时消耗破防层数恢复技力",
            "wiki_source": "骏卫战技粉碎阵线",
        },
        "R011_铁誓消耗触发盾卫袭扰": {
            "keyword": "铁誓",
            "description": "终结技期间消耗铁誓召唤盾卫袭扰",
            "wiki_source": "骏卫终结技盾卫旗队，上前",
        },
    }

    print("=" * 60)
    print("反应条件验证报告")
    print("=" * 60)

    validated_count = 0
    for reaction_id, info in reactions.items():
        print(f"\n{reaction_id}:")
        print(f"  描述: {info['description']}")
        print(f"  Wiki来源: {info['wiki_source']}")

        found = False
        for k, v in i18n_data.items():
            v_str = str(v)
            if info["keyword"] in v_str and len(v_str) > 20:
                print(f"  解包数据: [验证通过] {k}")
                print(f"  内容: {v_str[:100]}..." if len(v_str) > 100 else f"  内容: {v_str}")
                found = True
                validated_count += 1
                break

        if not found:
            print(f"  解包数据: [未找到]")

    print("\n" + "=" * 60)
    print(f"验证完成: {validated_count}/{len(reactions)} 个反应条件在解包数据中找到")
    print("=" * 60)

    return validated_count


def validate_skills(i18n_data):
    """验证技能信息。"""
    skills = {
        "莱万汀": {
            "焚灭": "战技",
            "沸腾": "连携技",
            "黄昏": "终结技",
        },
        "伊冯": {
            "冰冰弹": "战技",
            "速冻仔": "连携技",
            "冷冻射手": "终结技",
        },
        "洁尔佩塔": {
            "引力模式": "战技",
            "矩阵位移": "连携技",
            "重力场": "终结技",
        },
        "余烬": {
            "进军": "战技",
            "前线援护": "连携技",
            "重燃誓约": "终结技",
        },
        "骏卫": {
            "粉碎阵线": "战技",
            "盈月邀击": "连携技",
            "盾卫旗队": "终结技",
        },
    }

    print("\n" + "=" * 60)
    print("技能信息验证报告")
    print("=" * 60)

    validated_count = 0
    total_count = 0

    for char_name, char_skills in skills.items():
        print(f"\n{char_name}:")
        for skill_name, skill_type in char_skills.items():
            total_count += 1
            found = False
            for k, v in i18n_data.items():
                v_str = str(v)
                if skill_name in v_str and len(v_str) > 10:
                    print(f"  {skill_name} ({skill_type}): [验证通过] {k}")
                    found = True
                    validated_count += 1
                    break
            if not found:
                print(f"  {skill_name} ({skill_type}): [未找到]")

    print("\n" + "=" * 60)
    print(f"验证完成: {validated_count}/{total_count} 个技能在解包数据中找到")
    print("=" * 60)

    return validated_count


def main():
    """主函数。"""
    print("加载i18n数据...")
    i18n_data = load_i18n_data()
    print(f"加载完成，共 {len(i18n_data)} 条记录")

    # 验证反应条件
    reactions_validated = validate_reactions(i18n_data)

    # 验证技能信息
    skills_validated = validate_skills(i18n_data)

    # 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)
    print(f"反应条件验证: {reactions_validated}/11")
    print(f"技能信息验证: {skills_validated}/15")
    print("=" * 60)


if __name__ == "__main__":
    main()
