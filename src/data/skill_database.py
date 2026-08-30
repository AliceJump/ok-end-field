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

from typing import Optional

from src.data.skill_types import (
    Character,
    Skill,
    SkillType,
    ElementType,
    ConditionType,
    SkillEnhancement,
    SkillReaction,
    SkillEffect,
    AutoReleaseRestriction,
    DetectionInfo,
)
from src.data.effects import EffectType

from src.data.character_skills import load_all_characters

# 导出类型以便其他模块使用
__all__ = [
    "Character",
    "Skill",
    "SkillType",
    "ElementType",
    "ConditionType",
    "SkillEnhancement",
    "SkillReaction",
    "SkillEffect",
    "EffectType",
    "AutoReleaseRestriction",
    "DetectionInfo",
    "SkillDatabase",
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

    def get_character(self, character_id: str) -> Optional[Character]:
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

    def get_restriction_for_skill(self, skill_id: str) -> Optional[AutoReleaseRestriction]:
        """获取技能的自动释放限制。"""
        for restriction in self.restrictions:
            if restriction.skill_id == skill_id:
                return restriction
        return None

    def get_detection_info_for_state(self, state: str) -> Optional[DetectionInfo]:
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
    """创建示例数据库。"""
    db = SkillDatabase()

    # 从JSON文件加载所有角色
    all_characters = load_all_characters()
    for char_id, char in all_characters.items():
        db.add_character(char)

    # 添加反应
    db.add_reaction(SkillReaction(
        reaction_id="R001",
        name="燃烧触发连携",
        trigger_condition="敌人进入燃烧状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R002",
        name="熔火层数触发追加攻击",
        trigger_condition="熔火层数达到4层",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_ADDITIONAL)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R003",
        name="冻结触发连携",
        trigger_condition="敌人处于冻结状态，主控干员重击命中",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R004",
        name="法术附着消耗触发冻结",
        trigger_condition="战技命中处于寒冷附着或自然附着的敌人",
        condition_type=ConditionType.AND,
        effects=[
            SkillEffect(effect_id=EffectType.CLEAR_ATTACH),
            SkillEffect(effect_id=EffectType.STATUS_FROZEN),
        ],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R005",
        name="法术异常触发连携",
        trigger_condition="敌人被施加法术异常",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R006",
        name="破防增强法术脆弱",
        trigger_condition="目标处于破防状态",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.VULN_ALL)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R007",
        name="受击触发连携",
        trigger_condition="主控干员受到攻击",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R008",
        name="受击叠加攻击力",
        trigger_condition="受到来自敌人的伤害",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.BUFF_ATTACK_UP, value=6)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R009",
        name="猛击/碎甲消耗破防层数触发连携",
        trigger_condition="敌人被猛击或碎甲消耗破防层数",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R010",
        name="破防层数消耗恢复技力",
        trigger_condition="战技命中敌人时消耗破防层数",
        condition_type=ConditionType.AND,
        effects=[
            SkillEffect(effect_id=EffectType.CONSUME_STACK),
            SkillEffect(effect_id=EffectType.BUFF_HEAL),
        ],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R011",
        name="铁誓消耗触发盾卫袭扰",
        trigger_condition="终结技期间消耗铁誓",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_SHIELD)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R012",
        name="灼热附着消耗触发连携",
        trigger_condition="敌人的灼热附着被消耗或吸收",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R013",
        name="衔火血翼盘桓触发爆裂",
        trigger_condition="衔火血翼盘桓的敌人被连携技命中",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_EXPLOSION)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R014",
        name="物理脆弱/碎甲触发连携",
        trigger_condition="处于物理脆弱或碎甲状态的敌人受到主控干员重击",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R015",
        name="连击消耗增强终结技",
        trigger_condition="终结技消耗连击",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_ADDITIONAL)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R016",
        name="破防+法术附着触发连携",
        trigger_condition="敌人同时处于破防和法术附着状态",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R017",
        name="3层破防触发连携",
        trigger_condition="敌人达到3层及以上破防",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R018",
        name="寒冷附着/法术爆发触发连携",
        trigger_condition="敌人被施加寒冷附着或受到法术爆发伤害",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R019",
        name="涡流消耗形成水龙卷",
        trigger_condition="战技消耗涡流",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.CONSUME_STACK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R020",
        name="破防触发连携（陈）",
        trigger_condition="敌人进入破防状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R021",
        name="4层破防触发连携（潘）",
        trigger_condition="敌人达到4层破防",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R022",
        name="终结技击败触发备料",
        trigger_condition="终结技击败敌人",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R023",
        name="冻结触发连携（埃特拉）",
        trigger_condition="敌人进入冻结状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R024",
        name="物理脆弱触发终结技击飞",
        trigger_condition="敌人处于物理脆弱状态",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.STATUS_HEAVY_HIT)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R025",
        name="聚焦+异常触发连携（安塔尔）",
        trigger_condition="被聚焦的敌人进入物理异常或法术附着状态",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R026",
        name="失衡/蓄力触发连携（卡契尔）",
        trigger_condition="敌人开始蓄力，或主控干员受到攻击后生命值低于40%",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R027",
        name="失衡触发连携（秋栗）",
        trigger_condition="敌人失衡或触发失衡节点",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R028",
        name="2层附着触发连携（萤石）",
        trigger_condition="敌人进入2层及以上寒冷附着或自然附着",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R029",
        name="炸弹引爆增强（萤石）",
        trigger_condition="终结技命中粘有自制炸弹的敌人",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_EXPLOSION)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R030",
        name="电磁附着消耗触发追加攻击（庄方宜）",
        trigger_condition="战技命中处于电磁附着或导电状态的敌人",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_ADDITIONAL)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R031",
        name="破甲消耗破防层数（管理员）",
        trigger_condition="战技命中处于破防状态的敌人",
        condition_type=ConditionType.AND,
        effects=[
            SkillEffect(effect_id=EffectType.CONSUME_ALL),
            SkillEffect(effect_id=EffectType.BUFF_DAMAGE_UP),
        ],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R032",
        name="法术附着消耗触发连携（庄方宜）",
        trigger_condition="敌人被施加法术附着",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R033",
        name="自然附着消耗触发爆发（诀）",
        trigger_condition="种子命中处于自然附着的敌人",
        condition_type=ConditionType.AND,
        effects=[
            SkillEffect(effect_id=EffectType.CLEAR_ATTACH),
            SkillEffect(effect_id=EffectType.BUFF_DAMAGE_UP),
        ],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R034",
        name="法术异常触发连携（诀）",
        trigger_condition="敌人被施加法术异常",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R035",
        name="法术附着触发连携（艾尔黛拉）",
        trigger_condition="敌人被施加自然附着",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R036",
        name="冻结触发连携（别礼）",
        trigger_condition="敌人处于冻结状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R037",
        name="法术附着消耗触发寒冷脆弱（别礼）",
        trigger_condition="战技命中处于法术附着的敌人",
        condition_type=ConditionType.AND,
        effects=[
            SkillEffect(effect_id=EffectType.CLEAR_ATTACH),
            SkillEffect(effect_id=EffectType.VULN_COLD),
        ],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R038",
        name="导电触发连携（梨诺）",
        trigger_condition="敌人处于导电状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R039",
        name="法术附着触发连携（梨诺）",
        trigger_condition="敌人被施加法术附着",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R040",
        name="冻结触发连携（昼雪）",
        trigger_condition="敌人处于冻结状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R041",
        name="重击触发连携（赛希）",
        trigger_condition="主控干员对敌人造成重击",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R042",
        name="法术附着触发连携（狼卫）",
        trigger_condition="敌人被施加法术附着",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R043",
        name="重击触发连携（佩丽卡）",
        trigger_condition="主控干员对敌人造成重击",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R044",
        name="导电消耗触发连携（弧光）",
        trigger_condition="敌人的导电状态被消耗",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R045",
        name="导电触发连携（弧光）",
        trigger_condition="敌人进入导电状态",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R046",
        name="法术异常消耗触发连携（阿列什）",
        trigger_condition="附近目标的法术异常或源石结晶被消耗",
        condition_type=ConditionType.ANY,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    db.add_reaction(SkillReaction(
        reaction_id="R047",
        name="电磁附着触发连携（艾维文娜）",
        trigger_condition="主控干员对处于电磁附着或导电状态的目标进行重击",
        condition_type=ConditionType.AND,
        effects=[SkillEffect(effect_id=EffectType.TRIGGER_LINK)],
    ))

    # 添加自动释放限制
    db.add_restriction(AutoReleaseRestriction(
        skill_id="laevat_skill",
        enhancement_source="熔火层数",
        should_forbid_normal_release=False,
        reason="熔火层数是自我触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="yvonne_skill",
        enhancement_source="法术附着消耗",
        should_forbid_normal_release=False,
        reason="法术附着消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="aglina_ultimate",
        enhancement_source="破防状态",
        should_forbid_normal_release=False,
        reason="破防状态是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="azrila_link",
        enhancement_source="主控干员受击",
        should_forbid_normal_release=False,
        reason="连携技是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="pograni_skill",
        enhancement_source="破防层数消耗",
        should_forbid_normal_release=False,
        reason="破防层数消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="camille_skill",
        enhancement_source="衔火血翼盘桓",
        should_forbid_normal_release=False,
        reason="衔火血翼盘桓是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="lifeng_ultimate",
        enhancement_source="连击消耗",
        should_forbid_normal_release=False,
        reason="连击消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="wulfa_link",
        enhancement_source="破防+法术附着",
        should_forbid_normal_release=False,
        reason="双状态触发是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="mifu_skill",
        enhancement_source="三段战技替换",
        should_forbid_normal_release=False,
        reason="战技替换是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="tangtang_skill",
        enhancement_source="涡流消耗",
        should_forbid_normal_release=False,
        reason="涡流消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="chen_link",
        enhancement_source="破防状态",
        should_forbid_normal_release=False,
        reason="破防触发是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="dapan_link",
        enhancement_source="4层破防",
        should_forbid_normal_release=False,
        reason="4层破防是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="dapan_ultimate",
        enhancement_source="备料状态",
        should_forbid_normal_release=False,
        reason="备料状态是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="etra_link",
        enhancement_source="冻结状态",
        should_forbid_normal_release=False,
        reason="冻结触发是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="antal_link",
        enhancement_source="聚焦+异常",
        should_forbid_normal_release=False,
        reason="聚焦+异常是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="meurs_link",
        enhancement_source="蓄力/低生命",
        should_forbid_normal_release=False,
        reason="蓄力/低生命是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="karin_link",
        enhancement_source="失衡",
        should_forbid_normal_release=False,
        reason="失衡触发是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="bounda_link",
        enhancement_source="2层附着",
        should_forbid_normal_release=False,
        reason="2层附着是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="bounda_ultimate",
        enhancement_source="炸弹引爆",
        should_forbid_normal_release=False,
        reason="炸弹引爆是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="zhuangfy_skill",
        enhancement_source="电磁附着消耗",
        should_forbid_normal_release=False,
        reason="电磁附着消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="endmin_skill",
        enhancement_source="破甲效果",
        should_forbid_normal_release=False,
        reason="破甲效果是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="lizhiyan_skill",
        enhancement_source="种子爆发",
        should_forbid_normal_release=False,
        reason="种子爆发是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="lastrite_skill",
        enhancement_source="寒冷脆弱",
        should_forbid_normal_release=False,
        reason="寒冷脆弱是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="lastrite_ultimate",
        enhancement_source="寒冷脆弱增强",
        should_forbid_normal_release=False,
        reason="寒冷脆弱增强是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="ardelia_ultimate",
        enhancement_source="自然领域",
        should_forbid_normal_release=False,
        reason="自然领域是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="aurora_skill",
        enhancement_source="护盾叠加",
        should_forbid_normal_release=False,
        reason="护盾叠加是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="seraph_skill",
        enhancement_source="支援晶体",
        should_forbid_normal_release=False,
        reason="支援晶体是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="seraph_ultimate",
        enhancement_source="寒冷增幅+自然增幅",
        should_forbid_normal_release=False,
        reason="增幅效果是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="ikut_skill",
        enhancement_source="导电消耗",
        should_forbid_normal_release=False,
        reason="导电消耗是条件触发，不影响普通释放逻辑",
    ))

    db.add_restriction(AutoReleaseRestriction(
        skill_id="deepfin_skill",
        enhancement_source="寒冷附着消耗",
        should_forbid_normal_release=False,
        reason="寒冷附着消耗是条件触发，不影响普通释放逻辑",
    ))

    # 添加检测信息
    db.add_detection_info(DetectionInfo(
        state="熔火层数",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="强化普攻",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="冻结状态",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="法术附着层数",
        detection_location="敌人状态栏",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="法术脆弱",
        detection_location="敌人状态栏",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="破防层数",
        detection_location="敌人状态栏",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="护盾状态",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="攻击力叠加",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="铁誓层数",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="士气激昂层数",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="衔火血翼盘桓",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="追猎状态",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="连击层数",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="爪印斫痕",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="三段战技",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="涡流数量",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="水龙卷数量",
        detection_location="区域效果",
        stability="中",
        recommended_method="区域效果检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="古老图形",
        detection_location="区域效果",
        stability="中",
        recommended_method="区域效果检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="备料状态",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="斩锋层数",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="聚焦标记",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="自制炸弹",
        detection_location="敌人身上",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="连击层数（秋栗）",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="虚弱状态",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="庇护状态",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="电磁增幅",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="灼热增幅",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="电磁附着",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="导电状态",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="寒冷脆弱",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="自然附着",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="自然领域",
        detection_location="区域效果",
        stability="中",
        recommended_method="区域效果检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="种子爆发",
        detection_location="区域效果",
        stability="中",
        recommended_method="区域效果检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="雷光连斩",
        detection_location="技能按钮区域",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="护盾状态（昼雪）",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="寒冷增幅",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="自然增幅",
        detection_location="角色状态栏",
        stability="高",
        recommended_method="固定ROI检测",
        observation_class="A类",
    ))

    db.add_detection_info(DetectionInfo(
        state="燃烧状态",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="雷枪存在",
        detection_location="区域效果",
        stability="中",
        recommended_method="区域效果检测",
        observation_class="B类",
    ))

    db.add_detection_info(DetectionInfo(
        state="电磁脆弱",
        detection_location="敌人头顶",
        stability="中",
        recommended_method="敌人状态检测",
        observation_class="B类",
    ))

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
