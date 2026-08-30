# ==========================================================
# Battle Config Keys
# ==========================================================

KEY_SKILL_RELEASE = "技能释放"
KEY_START_SKILL_POINT = "启动技能点数"
KEY_COMPLETE_NOTIFY = "完成通知"
KEY_NO_NUMBER_OPERATION_INTERVAL = "无数字操作间隔"
KEY_BATTLE_INITIAL_WAIT = "进入战斗后的初始等待时间"

KEY_ENABLE_ROTATION = "启用排轴"
KEY_ROTATION_SEQUENCE = "排轴序列"

# 实时条件相关配置键
KEY_COND_ENABLED = "启用实时条件"
KEY_COND_SEQUENCE = "实时条件序列"

KEY_INSTANT_ULT = "立即释放终结技"
KEY_INSTANT_LINK = "立即释放连携技"

# 推荐技能
KEY_RECOMMEND_SKILL = "自动释放推荐技能"

# 自动技能列表
KEY_SKILL_ALLOWLIST = "自动技能列表"


# ==========================================================
# Config Name / Mode
# ==========================================================

BATTLE_CONFIG_NAME = "Battle Config"
BATTLE_CONFIG_MODE_KEY = "使用独立配置"


# ==========================================================
# Skill Release
# ==========================================================

SKILL_RELEASE_OPTIONS = [
    "1",
    "2",
    "3",
    "4",
]


DEFAULT_SKILL_RELEASE = [
    "1",
    "2",
    "3",
]


# ==========================================================
# Recommend Skill Regions
# ==========================================================

RECOMMEND_SKILL_REGIONS = [
    {
        "label": "批次1",
        "x": 0.820,
        "y": 0.898,
        "button_radius": 0.037,
        "effect_max_radius": 0.050,
    },
    {
        "label": "批次2",
        "x": 0.870,
        "y": 0.898,
        "button_radius": 0.037,
        "effect_max_radius": 0.050,
    },
    {
        "label": "批次3",
        "x": 0.920,
        "y": 0.898,
        "button_radius": 0.037,
        "effect_max_radius": 0.050,
    },
    {
        "label": "批次4",
        "x": 0.970,
        "y": 0.898,
        "button_radius": 0.037,
        "effect_max_radius": 0.050,
    },
]


# ==========================================================
# Ult Release Mode
# ==========================================================

KEY_ULT_RELEASE_MODE = "终结技释放方式"

ULT_RELEASE_MODE_HOLD = "长按技能按键"
ULT_RELEASE_MODE_ALT = "Alt + 技能按键"


# ==========================================================
# Default Battle Values
# ==========================================================

DEFAULT_ULT_RELEASE_MODE = ULT_RELEASE_MODE_HOLD

DEFAULT_START_SKILL_POINT = 2

DEFAULT_COMPLETE_NOTIFY = True

DEFAULT_NO_NUMBER_OPERATION_INTERVAL = 6

DEFAULT_BATTLE_INITIAL_WAIT = 3

DEFAULT_ENABLE_ROTATION = False

DEFAULT_ROTATION_SEQUENCE = "ult_2,1,e,ult_3,sleep_8"

DEFAULT_COND_ENABLED = False

DEFAULT_COND_SEQUENCE = []

DEFAULT_INSTANT_ULT = False

DEFAULT_INSTANT_LINK = False

DEFAULT_RECOMMEND_SKILL = False

DEFAULT_SKILL_ALLOWLIST = True


# ==========================================================
# Default Battle Config
# ==========================================================

DEFAULT_BATTLE_CONFIG = {
    KEY_ULT_RELEASE_MODE: DEFAULT_ULT_RELEASE_MODE,
    KEY_SKILL_RELEASE: DEFAULT_SKILL_RELEASE,
    KEY_START_SKILL_POINT: DEFAULT_START_SKILL_POINT,
    KEY_COMPLETE_NOTIFY: DEFAULT_COMPLETE_NOTIFY,
    KEY_NO_NUMBER_OPERATION_INTERVAL: DEFAULT_NO_NUMBER_OPERATION_INTERVAL,
    KEY_BATTLE_INITIAL_WAIT: DEFAULT_BATTLE_INITIAL_WAIT,
    KEY_ENABLE_ROTATION: DEFAULT_ENABLE_ROTATION,
    KEY_ROTATION_SEQUENCE: DEFAULT_ROTATION_SEQUENCE,
    KEY_COND_ENABLED: DEFAULT_COND_ENABLED,
    KEY_COND_SEQUENCE: DEFAULT_COND_SEQUENCE,
    KEY_INSTANT_ULT: DEFAULT_INSTANT_ULT,
    KEY_INSTANT_LINK: DEFAULT_INSTANT_LINK,
    KEY_RECOMMEND_SKILL: DEFAULT_RECOMMEND_SKILL,
    KEY_SKILL_ALLOWLIST: DEFAULT_SKILL_ALLOWLIST,
}


BATTLE_GROUP_CONFIGS = {
    KEY_SKILL_ALLOWLIST: [
        # 基础技能循环
        KEY_SKILL_RELEASE,
        # 排轴
        KEY_ENABLE_ROTATION,
        # 实时条件（其子项：KEY_COND_SEQUENCE, KEY_INSTANT_ULT, KEY_INSTANT_LINK 已由 conditional_rotation 面板管理）
        KEY_COND_ENABLED,
        # 推荐技能
        KEY_RECOMMEND_SKILL,
    ]
}
# ==========================================================
# Config UI Type
# ==========================================================

BATTLE_CONFIG_TYPE = {
    KEY_ULT_RELEASE_MODE: {
        "type": "drop_down",
        "options": [
            ULT_RELEASE_MODE_HOLD,
            ULT_RELEASE_MODE_ALT,
        ],
    },
    KEY_SKILL_RELEASE: {
        "options_available": SKILL_RELEASE_OPTIONS,
        "allow_duplication": False,
    },
    KEY_ENABLE_ROTATION: {"sub_configs": {True: [KEY_ROTATION_SEQUENCE]}},
    KEY_ROTATION_SEQUENCE: {},
    KEY_COND_ENABLED: {
        "sub_configs": {
            True: [KEY_INSTANT_ULT, KEY_INSTANT_LINK, KEY_COND_SEQUENCE],
        },
    },
    KEY_COND_SEQUENCE: {
        "type": "cond_sequence_editor",
    },
    KEY_RECOMMEND_SKILL: {},
    KEY_SKILL_ALLOWLIST: {
        "sub_configs": {False: BATTLE_GROUP_CONFIGS[KEY_SKILL_ALLOWLIST]}
    },
}


# ==========================================================
# Config Description
# ==========================================================

BATTLE_CONFIG_DESCRIPTION = {
    KEY_ULT_RELEASE_MODE: "配置终结技的释放方式",
    KEY_SKILL_RELEASE: (
        "按列表顺序自动循环释放「战技」。\n" "可从 1/2/3/4 中选择并排序，至少保留一个。"
    ),
    KEY_START_SKILL_POINT: (
        "当「技力条」达到该数值时，\n" "开始执行技能序列。取值范围1-3。"
    ),
    KEY_COMPLETE_NOTIFY: "战斗结束后发送系统通知。",
    KEY_NO_NUMBER_OPERATION_INTERVAL: (
        "战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n" "取值不小于1。"
    ),
    KEY_BATTLE_INITIAL_WAIT: "进入战斗后开始自动操作前的等待秒数。",
    KEY_ENABLE_ROTATION: (
        "是否启用排轴功能。\n"
        "启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n"
        "当排轴失败时回退到非排轴状态。"
    ),
    KEY_ROTATION_SEQUENCE: (
        "仅接受"
        "'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,"
        "sleep_[n],normal_[n]'"
        "这些值的逗号分隔字符串。\n"
        "normal_[n] 表示临时切换为普通战斗模式 n 秒，"
        "期间按「技能释放」顺序自动出技。"
    ),
    KEY_COND_ENABLED: ("根据实时情况释放技能\n" "启用时自动忽略排轴配置"),
    KEY_COND_SEQUENCE: "",
    KEY_INSTANT_ULT: (
        "在没有运行任何条件动作时生效\n" "当终结技可释放时立刻释放终结技"
    ),
    KEY_INSTANT_LINK: (
        "在没有运行任何条件动作时生效\n" "当连携技可释放时立刻释放连携技"
    ),
    KEY_RECOMMEND_SKILL: (
        "自动优先释放推荐技能。\n"
        "技能按钮出现白圈（游戏推荐释放时机）时，"
        "自动按下对应技能键，\n"
        "每个白圈周期按一次；优先级仅次于连携技。"
    ),
    KEY_SKILL_ALLOWLIST: (
        "根据队伍角色的增强链依赖，自动过滤「技能释放」序列。\n"
        "启用后，战斗开始时自动识别左下角 4 个头像，\n"
        "跳过被增强机制接管的战技，"
        "只保留有意义释放的战技。"
    ),
}


# ==========================================================
# Config Manager
# ==========================================================


class BattleConfigManager:
    """Manages battle configuration with fallback to default values."""

    def __init__(self, battle_config: dict | None = None):
        self.battle_config = battle_config or {}

    def update_config(self, battle_config: dict):
        """Update the battle configuration with a new dictionary."""
        self.battle_config = battle_config or {}

    def get(self, key: str, default=None):
        """Get a configuration value with fallback to default battle config."""
        return self.battle_config.get(
            key,
            DEFAULT_BATTLE_CONFIG.get(key, default),
        )
