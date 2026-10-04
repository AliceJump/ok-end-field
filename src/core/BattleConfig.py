# ==========================================================
# Battle Config Keys
# ==========================================================

KEY_SKILL_RELEASE = "技能释放"
KEY_START_SKILL_POINT = "启动技能点数"
KEY_COMPLETE_NOTIFY = "完成通知"
KEY_NO_NUMBER_OPERATION_INTERVAL = "无数字操作间隔"
KEY_BATTLE_INITIAL_WAIT = "进入战斗后的初始等待时间"
KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY = "仅协议空间启用初始等待"

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

# 伤害优先排序（自动技能列表的子选项）
KEY_DAMAGE_ROTATION = "伤害优先排序"

# Independent timing mode takes precedence over every legacy strategy.
KEY_TIMING_ROTATION = "技能时间排轴"

# 旧 AutoCombat 的模式选择。旧布尔开关继续保留为内部兼容字段，
# UI 和运行时均以该单选模式为准，避免多个策略同时为 True。
KEY_LEGACY_COMBAT_MODE = "战斗模式"
LEGACY_COMBAT_MODE_NORMAL = "普通模式"
LEGACY_COMBAT_MODE_AUTO_FILTER = "自动技能列表"
LEGACY_COMBAT_MODE_DAMAGE = "伤害优先排轴"
LEGACY_COMBAT_MODE_ROTATION = "固定排轴"
LEGACY_COMBAT_MODE_CONDITIONAL = "实时条件"
LEGACY_COMBAT_MODE_OPTIONS = [
    LEGACY_COMBAT_MODE_NORMAL,
    LEGACY_COMBAT_MODE_AUTO_FILTER,
    LEGACY_COMBAT_MODE_DAMAGE,
    LEGACY_COMBAT_MODE_ROTATION,
    LEGACY_COMBAT_MODE_CONDITIONAL,
]

# 脉冲探针（独立诊断开关：观测推荐脉冲出现位置并落盘，不影响战斗行为）
KEY_PULSE_PROBE = "脉冲探针记录"


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
DEFAULT_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY = False

DEFAULT_ENABLE_ROTATION = False

DEFAULT_ROTATION_SEQUENCE = "ult_2,1,e,ult_3,sleep_8"

DEFAULT_COND_ENABLED = False

DEFAULT_COND_SEQUENCE = []

DEFAULT_INSTANT_ULT = False

DEFAULT_INSTANT_LINK = False

DEFAULT_RECOMMEND_SKILL = False

DEFAULT_SKILL_ALLOWLIST = True

DEFAULT_DAMAGE_ROTATION = True

# 旧默认组合为「自动技能列表=True + 伤害优先排序=True」。
# 新模式选择器保持相同行为，避免新安装默认策略发生变化。
DEFAULT_LEGACY_COMBAT_MODE = LEGACY_COMBAT_MODE_DAMAGE

# 脉冲探针默认开启：诊断数据采集不影响战斗，攒实战样本
DEFAULT_PULSE_PROBE = True


# ==========================================================
# Default Battle Config
# ==========================================================

DEFAULT_BATTLE_CONFIG = {
    KEY_TIMING_ROTATION: False,
    KEY_LEGACY_COMBAT_MODE: DEFAULT_LEGACY_COMBAT_MODE,
    KEY_ULT_RELEASE_MODE: DEFAULT_ULT_RELEASE_MODE,
    KEY_SKILL_RELEASE: DEFAULT_SKILL_RELEASE,
    KEY_START_SKILL_POINT: DEFAULT_START_SKILL_POINT,
    KEY_COMPLETE_NOTIFY: DEFAULT_COMPLETE_NOTIFY,
    KEY_NO_NUMBER_OPERATION_INTERVAL: DEFAULT_NO_NUMBER_OPERATION_INTERVAL,
    KEY_BATTLE_INITIAL_WAIT: DEFAULT_BATTLE_INITIAL_WAIT,
    KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY: DEFAULT_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY,
    KEY_ENABLE_ROTATION: DEFAULT_ENABLE_ROTATION,
    KEY_ROTATION_SEQUENCE: DEFAULT_ROTATION_SEQUENCE,
    KEY_COND_ENABLED: DEFAULT_COND_ENABLED,
    KEY_COND_SEQUENCE: DEFAULT_COND_SEQUENCE,
    KEY_INSTANT_ULT: DEFAULT_INSTANT_ULT,
    KEY_INSTANT_LINK: DEFAULT_INSTANT_LINK,
    KEY_RECOMMEND_SKILL: DEFAULT_RECOMMEND_SKILL,
    KEY_SKILL_ALLOWLIST: DEFAULT_SKILL_ALLOWLIST,
    KEY_DAMAGE_ROTATION: DEFAULT_DAMAGE_ROTATION,
    KEY_PULSE_PROBE: DEFAULT_PULSE_PROBE,
}


# 顶层字段只负责定义唯一归属。任务级「使用独立配置」仅引用这些根节点，
# 其余字段由「技能时间排轴」或「战斗模式」继续向下展开，避免同一配置
# 同时被多个父节点引用。
BATTLE_ROOT_CONFIGS = [
    KEY_TIMING_ROTATION,
    KEY_ULT_RELEASE_MODE,
    KEY_COMPLETE_NOTIFY,
    KEY_BATTLE_INITIAL_WAIT,
    KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY,
]

# 当前分支时间排轴关闭后，所有旧 AutoCombat 模式都共用的配置。
LEGACY_COMBAT_SHARED_CONFIGS = [
    KEY_LEGACY_COMBAT_MODE,
    KEY_NO_NUMBER_OPERATION_INTERVAL,
    KEY_PULSE_PROBE,
]

# 每个模式只挂自己的专属配置。旧模式内部确实会在 fallback / normal_[n]
# 路径读取普通循环配置，但这些不是该模式的主配置；保留已存值作为兼容，
# 不再重复挂到多个模式分支，避免 UI 归属冲突。
LEGACY_COMBAT_MODE_SUB_CONFIGS = {
    LEGACY_COMBAT_MODE_NORMAL: [
        KEY_SKILL_RELEASE,
        KEY_START_SKILL_POINT,
        KEY_RECOMMEND_SKILL,
    ],
    LEGACY_COMBAT_MODE_AUTO_FILTER: [],
    LEGACY_COMBAT_MODE_DAMAGE: [],
    LEGACY_COMBAT_MODE_ROTATION: [
        KEY_ROTATION_SEQUENCE,
    ],
    LEGACY_COMBAT_MODE_CONDITIONAL: [
        KEY_COND_SEQUENCE,
        KEY_INSTANT_ULT,
        KEY_INSTANT_LINK,
    ],
}


# ==========================================================
# Config UI Type
# ==========================================================

BATTLE_CONFIG_TYPE = {
    # 当前分支主开发模式。开启后旧 AutoCombat 配置整块隐藏；
    # 关闭后只展开一个旧模式选择器和旧模式公共项。
    KEY_TIMING_ROTATION: {
        "sub_configs": {
            False: LEGACY_COMBAT_SHARED_CONFIGS,
        },
    },
    KEY_LEGACY_COMBAT_MODE: {
        "type": "drop_down",
        "options": LEGACY_COMBAT_MODE_OPTIONS,
        "sub_configs": LEGACY_COMBAT_MODE_SUB_CONFIGS,
    },
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
    KEY_ROTATION_SEQUENCE: {},
    KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY: {},
    KEY_COND_SEQUENCE: {
        "type": "cond_sequence_editor",
    },
    KEY_RECOMMEND_SKILL: {},
    KEY_PULSE_PROBE: {},
    # 旧模式布尔开关只作为持久化/兼容字段保留，不再直接出现在 UI。
    KEY_ENABLE_ROTATION: {"hidden": True},
    KEY_COND_ENABLED: {"hidden": True},
    KEY_SKILL_ALLOWLIST: {"hidden": True},
    KEY_DAMAGE_ROTATION: {"hidden": True},
}


# ==========================================================
# Config Description
# ==========================================================

BATTLE_CONFIG_DESCRIPTION = {
    KEY_TIMING_ROTATION: ("独立实验模式，优先于其他战斗策略开关。\n利用实时监测和本地技能时间数据安排出技。"),
    KEY_LEGACY_COMBAT_MODE: (
        "仅在「技能时间排轴」关闭时显示。\n选择原有 AutoCombat 的执行模式；同一时刻只启用一种模式。"
    ),
    KEY_ULT_RELEASE_MODE: "配置终结技的释放方式",
    KEY_SKILL_RELEASE: ("按列表顺序自动循环释放「战技」。\n可从 1/2/3/4 中选择并排序，至少保留一个。"),
    KEY_START_SKILL_POINT: ("当「技力条」达到该数值时，\n开始执行技能序列。取值范围1-3。"),
    KEY_COMPLETE_NOTIFY: "战斗结束后发送系统通知。",
    KEY_NO_NUMBER_OPERATION_INTERVAL: ("战斗中周期触发锁敌+向前闪避的最小间隔秒数。\n取值不小于1。"),
    KEY_BATTLE_INITIAL_WAIT: "进入战斗后开始自动操作前的等待秒数。",
    KEY_BATTLE_INITIAL_WAIT_PROTOCOL_ONLY: (
        "开启后，初始等待时间只在协议空间生效；普通战斗检测到战斗 UI 后立即开始自动操作。"
        "协议空间优先通过开局全队终结技就绪判定，并保留左上角撤离按钮作为兜底。"
    ),
    KEY_ENABLE_ROTATION: (
        "是否启用排轴功能。\n启用后会根据「排轴序列」配置的顺序优先释放对应角色的技能，\n当排轴失败时回退到非排轴状态。"
    ),
    KEY_ROTATION_SEQUENCE: (
        "仅接受"
        "'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,"
        "sleep_[n],normal_[n]'"
        "这些值的逗号分隔字符串。\n"
        "normal_[n] 表示临时切换为普通战斗模式 n 秒，"
        "期间按「技能释放」顺序自动出技。"
    ),
    KEY_COND_ENABLED: ("根据实时情况释放技能\n启用时自动忽略排轴配置"),
    KEY_COND_SEQUENCE: "",
    KEY_INSTANT_ULT: ("在没有运行任何条件动作时生效\n当终结技可释放时立刻释放终结技"),
    KEY_INSTANT_LINK: ("在没有运行任何条件动作时生效\n当连携技可释放时立刻释放连携技"),
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
    KEY_DAMAGE_ROTATION: (
        "「自动技能列表」开启时生效。\n"
        "按各角色战技的实际伤害（官方 WIKI 满配基准、暴击期望）降序，\n"
        "生成覆盖队内 1-4 号位的可重复循环自动排轴：\n"
        "战技 → 终结技 → 普攻回技力填充段，并按就绪窗口尝试连携技；\n"
        "终结技/连携未就绪时自动跳过，循环继续，不卡轴。\n"
        "自动识别协议空间（战斗画面左上角「撤离」按钮）：\n"
        "协议空间开局终结技全满，轴含终结技；普通战斗（冷启动）\n"
        "轴不含终结技，就绪后由填充段兜底自动释放。\n"
        "注意：与增强链过滤互斥（二选一）——关闭本项时「自动技能列表」\n"
        "按增强链闭包过滤「技能释放」序列；开启本项时改为伤害排序自动排轴。"
    ),
    KEY_PULSE_PROBE: (
        "独立诊断探针：战斗中观测技能按钮区域出现白色脉冲（官方推荐\n"
        "释放时机）的位置与时间，追加记录到\n"
        "「configs/pulse_probe_log.jsonl」，用于统计哪些强化态/技能\n"
        "有官方脉冲提示。\n"
        "只记录不按键，不影响任何战斗行为；关闭后停止记录。"
    ),
}


# ==========================================================
# Legacy mode migration / compatibility
# ==========================================================


def infer_legacy_combat_mode(config: dict | None) -> str:
    """Infer the single legacy mode from the historical strategy booleans.

    Priority intentionally matches AutoCombatLogic's old dispatch order:
    realtime conditions > fixed rotation > auto list/damage > normal.
    """
    data = config if isinstance(config, dict) else {}
    if data.get(KEY_COND_ENABLED, DEFAULT_COND_ENABLED):
        return LEGACY_COMBAT_MODE_CONDITIONAL
    if data.get(KEY_ENABLE_ROTATION, DEFAULT_ENABLE_ROTATION):
        return LEGACY_COMBAT_MODE_ROTATION
    if data.get(KEY_SKILL_ALLOWLIST, DEFAULT_SKILL_ALLOWLIST):
        if data.get(KEY_DAMAGE_ROTATION, DEFAULT_DAMAGE_ROTATION):
            return LEGACY_COMBAT_MODE_DAMAGE
        return LEGACY_COMBAT_MODE_AUTO_FILTER
    return LEGACY_COMBAT_MODE_NORMAL


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
