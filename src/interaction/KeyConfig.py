# 默认游戏通用热键映射表
DEFAULT_COMMON_KEYS = {
    # 通用部分 - 基础操作
    'Dodge Key': 'lshift',
    'Jump Key': 'space',
    'Interact Key': 'f',
    'Backpack Key': 'b',
    'Valuables Key': 'n',
    'Team Key': 'u',

    # 通用部分 - UI/信息
    'Operator Key': 'c',
    'Mission Key': 'j',
    'Track Key': 'v',
    'Map Key': 'm',
    'Baker Key': 'h',
    'Mail Key': 'k',
    'Handbook Key': 'f8',
    'Recruitment Key': 'f9',

    # 快捷工具
    'Quick Tool Key': 'r',
}

# 默认集成工业热键映射表
DEFAULT_INDUSTRY_KEYS = {
    'Industry Plan Key': 't',
    'Place Belt Key': 'e',
    'Place Pipeline Key': 'q',
    'Equipment List Key': 'z',
    'Overview Mode Key': 'capslock',
    'Storage Mode Key': 'x',
    'Area Build Key': 'y',
    'Blueprint Key': 'f1',
    'Product Icon Toggle Key': 'f4',
}

# 默认战斗专用热键映射表
DEFAULT_COMBAT_KEYS = {
    'Link Skill Key': 'e',  # 释放连携技
}

type_to_key_map = {
    'common': DEFAULT_COMMON_KEYS,
    'industry': DEFAULT_INDUSTRY_KEYS,
    'combat': DEFAULT_COMBAT_KEYS
}


class KeyConfigManager:
    """游戏热键配置管理器，负责替换逻辑"""

    def __init__(self, key_config: dict = None):
        """
        初始化热键配置管理器。
        
        Args:
            key_config: 用户自定义的热键配置字典（通常来自全局配置）
        """
        self.key_config = key_config or {}

    def update_config(self, key_config: dict):
        """更新用户配置。
        
        Args:
            key_config: 新的配置字典
        """
        self.key_config = key_config or {}

    def resolve_key(self, key: str, key_type: str = 'common') -> str:
        config_key_name = None

        default_map = type_to_key_map.get(key_type, {})

        for key_name, key_value in default_map.items():
            if key_value == key:
                config_key_name = key_name
                break

        if config_key_name:
            return self.key_config.get(config_key_name, key)

        return key
