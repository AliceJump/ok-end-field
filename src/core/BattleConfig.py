# 实时条件相关配置键（供 BattleMixin / ConditionalRotationPanel / AutoCombatLogic 引用）
KEY_COND_ENABLED = "启用实时条件"
KEY_COND_SEQUENCE = "实时条件序列"
KEY_INSTANT_ULT = "立即释放终结技"
KEY_INSTANT_LINK = "立即释放连携技"
KEY_ROTATION_SEQUENCE = "排轴序列"

# 默认战斗通用配置
DEFAULT_BATTLE_CONFIG = {
    "技能释放": ["1", "2", "3"],
    "启动技能点数": 2,
    "完成通知": True,
    "无数字操作间隔": 6,
    "进入战斗后的初始等待时间": 3,
    "启用排轴": False,
    KEY_ROTATION_SEQUENCE: "ult_2,1,e,ult_3,sleep_8",
    KEY_COND_ENABLED: False,
    KEY_COND_SEQUENCE: [],
    KEY_INSTANT_ULT: False,
    KEY_INSTANT_LINK: False,
}

BATTLE_CONFIG_NAME = "Battle Config"
BATTLE_CONFIG_MODE_KEY = "战斗配置"
BATTLE_CONFIG_MODE_GLOBAL = "使用全局配置"
BATTLE_CONFIG_MODE_INDEPENDENT = "使用独立配置"
BATTLE_CONFIG_TYPE = {
    "技能释放": {
        "options_available": ["1", "2", "3", "4"],
        "allow_duplication": False,
    },
    KEY_COND_ENABLED: {"type": "conditional_rotation"},
    KEY_COND_SEQUENCE: {"hidden": True},
    KEY_INSTANT_ULT: {"hidden": True},
    KEY_INSTANT_LINK: {"hidden": True},
}
BATTLE_CONFIG_DESCRIPTION = {
    "技能释放": (
        "按列表顺序自动循环释放「战技」。\n"
        "可从 1/2/3/4 中选择并排序，至少保留一个。"
    ),
    "启动技能点数": (
        "当「技力条」达到该数值时，\n"
        "开始执行技能序列。取值范围1-3。"
    ),
    "完成通知": "战斗结束后发送系统通知。",
    "无数字操作间隔": (
        "战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n"
        "取值不小于1。"
    ),
    "进入战斗后的初始等待时间": "进入战斗后开始自动操作前的等待秒数。",
    "启用排轴": (
        "是否启用排轴功能。\n"
        "启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n"
        "当排轴失败时回退到非排轴状态。"
    ),
    KEY_ROTATION_SEQUENCE: (
        "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\n"
        "normal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。"
    ),
    KEY_COND_ENABLED: (
        "根据实时情况释放技能\n"
        "启用时自动忽略排轴配置"
    ),
    KEY_COND_SEQUENCE: (
        ""
    ),
    KEY_INSTANT_ULT: (
        "在没有运行任何条件动作时生效\n"
        "当终结技可释放时立刻释放终结技"
    ),
    KEY_INSTANT_LINK: (
        "在没有运行任何条件动作时生效\n"
        "当连携技可释放时立刻释放连携技"
    ),
}


class BattleConfigManager:
    def __init__(self, battle_config: dict | None = None):
        self.battle_config = battle_config or {}

    def update_config(self, battle_config: dict):
        self.battle_config = battle_config or {}

    def get(self, key: str, default=None):
        return self.battle_config.get(key, DEFAULT_BATTLE_CONFIG.get(key, default))
