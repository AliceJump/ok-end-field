# 实时条件相关配置键（供 BattleMixin / ConditionalRotationPanel / AutoCombatLogic 引用）
KEY_COND_ENABLED = "启用实时条件"
KEY_COND_SEQUENCE = "实时条件序列"
KEY_INSTANT_ULT = "立即释放终结技"
KEY_INSTANT_LINK = "立即释放连携技"
KEY_ROTATION_SEQUENCE = "排轴序列"

# 推荐技能（白色圆周脉冲触发战技）相关
KEY_RECOMMEND_SKILL = "自动释放推荐技能"

# 推荐技能按钮四区域预设（与 TestCircularPulseDetect 调试任务共用同一数据源）。
# x / y 为按钮中心归一化坐标（pixel / 宽、pixel / 高）；
# button_radius / effect_max_radius 为半径（pixel / 短边）。
RECOMMEND_SKILL_REGIONS = [
    {"label": "批次1", "x": 0.820, "y": 0.898, "button_radius": 0.037, "effect_max_radius": 0.050},
    {"label": "批次2", "x": 0.870, "y": 0.898, "button_radius": 0.037, "effect_max_radius": 0.050},
    {"label": "批次3", "x": 0.920, "y": 0.898, "button_radius": 0.037, "effect_max_radius": 0.050},
    {"label": "批次4", "x": 0.970, "y": 0.898, "button_radius": 0.037, "effect_max_radius": 0.050},
]

# 终结技释放方式（供 BattleMixin.use_ult 选择释放方式）
KEY_ULT_RELEASE_MODE = "终结技释放方式"
ULT_RELEASE_MODE_HOLD = "长按技能按键"
ULT_RELEASE_MODE_ALT = "Alt + 技能按键"

# 默认战斗通用配置
DEFAULT_BATTLE_CONFIG = {
    KEY_ULT_RELEASE_MODE: ULT_RELEASE_MODE_HOLD,
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
    KEY_RECOMMEND_SKILL: False,
}

BATTLE_CONFIG_NAME = "Battle Config"
BATTLE_CONFIG_MODE_KEY = "使用独立配置"
BATTLE_CONFIG_TYPE = {
    KEY_ULT_RELEASE_MODE: {
        "type": "drop_down",
        "options": [ULT_RELEASE_MODE_HOLD, ULT_RELEASE_MODE_ALT],
    },
    "技能释放": {
        "options_available": ["1", "2", "3", "4"],
        "allow_duplication": False,
    },
    "启用排轴": {"sub_configs": {True: [KEY_ROTATION_SEQUENCE]}},
    KEY_ROTATION_SEQUENCE: {},
    KEY_COND_ENABLED: {"type": "conditional_rotation"},
    KEY_COND_SEQUENCE: {"hidden": True},
    KEY_INSTANT_ULT: {"hidden": True},
    KEY_INSTANT_LINK: {"hidden": True},
    KEY_RECOMMEND_SKILL: {},
}
BATTLE_CONFIG_DESCRIPTION = {
    KEY_ULT_RELEASE_MODE: "配置终结技的释放方式",
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
    KEY_RECOMMEND_SKILL: (
        "自动优先释放推荐技能。\n"
        "技能按钮出现白圈（游戏推荐释放时机）时，自动按下对应技能键，\n"
        "每个白圈周期按一次；优先级仅次于连携技。"
    ),
}


class BattleConfigManager:
    """Manages battle configuration with fallback to default values."""
    def __init__(self, battle_config: dict | None = None):
        self.battle_config = battle_config or {}

    def update_config(self, battle_config: dict):
        """Update the battle configuration with a new dictionary."""
        self.battle_config = battle_config or {}

    def get(self, key: str, default=None):
        """Get a configuration value with fallback to default battle config."""
        return self.battle_config.get(key, DEFAULT_BATTLE_CONFIG.get(key, default))
