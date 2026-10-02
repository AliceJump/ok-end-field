"""角色技能强化/反应依赖数据库。

基于明日方舟：终末地Wiki数据整理，用于自动化技能释放决策。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到 sys.path 以便直接运行
_root = str(Path(__file__).resolve().parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


from src.data.character_skills import load_all_characters
from src.data.effects import EffectType
from src.data.skill_types import (
    AutoReleaseRestriction,
    Character,
    ConditionType,
    DetectionInfo,
    ElementType,
    Skill,
    SkillEffect,
    SkillEnhancement,
    SkillReaction,
    SkillType,
)

# 导出类型以便其他模块使用
__all__ = [
    "AutoReleaseRestriction",
    "Character",
    "ConditionType",
    "DetectionInfo",
    "EffectType",
    "ElementType",
    "Skill",
    "SkillDatabase",
    "SkillEffect",
    "SkillEnhancement",
    "SkillReaction",
    "SkillType",
    "create_example_database",
]


class SkillDatabase:
    """技能数据库。"""

    def __init__(self):
        self.characters: dict[str, Character] = {}
        self.reactions: list[SkillReaction] = []
        self.restrictions: list[AutoReleaseRestriction] = []
        self.detection_infos: list[DetectionInfo] = []

    def add_character(self, character: Character) -> None:
        """添加角色。"""
        self.characters[character.character_id] = character

    def add_reaction(self, reaction: SkillReaction) -> None:
        """添加反应。"""
        self.reactions.append(reaction)

    def add_restriction(self, restriction: AutoReleaseRestriction) -> None:
        """添加自动释放限制。"""
        self.restrictions.append(restriction)

    def add_detection_info(self, info: DetectionInfo) -> None:
        """添加检测信息。"""
        self.detection_infos.append(info)

    def get_character(self, character_id: str) -> Character | None:
        """获取角色。"""
        return self.characters.get(character_id)

    def get_skills_by_type(self, skill_type: SkillType) -> list[Skill]:
        """按类型获取技能。"""
        result = []
        for character in self.characters.values():
            for skill in character.skills:
                if skill.skill_type == skill_type:
                    result.append(skill)
        return result

    def get_enhanced_skills(self) -> list[Skill]:
        """获取所有有强化态的技能。"""
        result = []
        for character in self.characters.values():
            for skill in character.skills:
                if skill.has_enhancement:
                    result.append(skill)
        return result

    def get_reactions_by_element(self) -> list[SkillReaction]:
        """按元素获取反应。"""
        return []

    def get_restriction_for_skill(self, skill_id: str) -> AutoReleaseRestriction | None:
        """获取技能的自动释放限制。"""
        for restriction in self.restrictions:
            if restriction.skill_id == skill_id:
                return restriction
        return None

    def get_detection_info_for_state(self, state: str) -> DetectionInfo | None:
        """获取状态的检测信息。"""
        for info in self.detection_infos:
            if info.state == state:
                return info
        return None


# 预定义的检测位置（基于项目现有代码）
DETECTION_LOCATIONS = {
    "skill_button": "技能按钮区域",
    "ultimate_button": "终结技按钮区域",
    "link_skill_button": "连携技按钮区域",
    "character_status": "角色状态栏",
    "enemy头顶": "敌人头顶标识",
    "battle_target": "战斗目标状态",
}

# 预定义的检测方法
DETECTION_METHODS = {
    "fixed_roi": "固定ROI检测",
    "template_matching": "模板匹配",
    "color_detection": "颜色检测",
    "pulse_detection": "脉冲检测",
    "ocr": "OCR识别",
}


def create_example_database() -> SkillDatabase:
    """兼容入口：只加载 canonical 角色技能数据，不再注入旧手写语义。

    旧版本在这里维护了一套独立的 R001~R047 reaction、自动释放限制和
    检测信息；其中包含把“恢复技力”误写成 BUFF_HEAL、已废弃的“种子爆发”
    等规则。当前 canonical 真源已经迁到 character_skills/*.json、
    effects.py 与 effect_semantics.py。为避免双真源重新污染机制模拟，
    本兼容入口只返回由 JSON 加载的角色。
    """
    db = SkillDatabase()
    for char_id, char in load_all_characters().items():
        db.add_character(char)
    return db


if __name__ == "__main__":
    # 测试数据库
    db = create_example_database()
    print(f"角色数量: {len(db.characters)}")
    print(f"反应数量: {len(db.reactions)}")
    print(f"限制数量: {len(db.restrictions)}")
    print(f"检测信息数量: {len(db.detection_infos)}")

    # 打印角色信息
    for char_id, char in db.characters.items():
        print(f"\n角色: {char.name} ({char_id})")
        print(f"  星级: {char.star}")
        print(f"  元素: {char.element.value}")
        print(f"  职业: {char.profession}")
        print(f"  技能数量: {len(char.skills)}")
        for skill in char.skills:
            print(f"    - {skill.name} ({skill.skill_type.value})")
            if skill.has_enhancement:
                print(f"      强化态: {skill.enhancement.name}")
