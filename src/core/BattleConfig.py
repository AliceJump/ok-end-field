# 默认战斗通用配置
DEFAULT_BATTLE_CONFIG = {
    "技能释放": ["1", "2", "3"],
    "启动技能点数": 2,
    "后台结束战斗通知": True,
    "无数字操作间隔": 6,
    "进入战斗后的初始等待时间": 3,
    "启用排轴": False,
    "排轴序列": "ult_2,1,e,ult_3,sleep_8",
}

BATTLE_CONFIG_NAME = "Battle Config"
BATTLE_CONFIG_TYPE = {
    "技能释放": {
        "options_available": ["1", "2", "3", "4"],
        "allow_duplication": False,
    },
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
    "后台结束战斗通知": "后台运行时，战斗结束后发送通知。",
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
    "排轴序列": (
        "仅接受'1,2,3,4,ult_1,ult_2,ult_3,ult_4,e,sleep_[n],normal_[n]'这些值的逗号分隔字符串，\n"
        "normal_[n] 表示临时切换为普通战斗模式 n 秒，期间按「技能释放」顺序自动出技。"
    ),
}


class BattleConfigManager:
    def __init__(self, battle_config: dict = None):
        self.battle_config = battle_config or {}

    def update_config(self, battle_config: dict):
        self.battle_config = battle_config or {}

    def get(self, key: str, default=None):
        return self.battle_config.get(key, DEFAULT_BATTLE_CONFIG.get(key, default))
