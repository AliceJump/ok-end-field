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

    def resolve_common_key(self, key: str) -> str:
        """解析通用部分的热键。
        
        先在DEFAULT_COMMON_KEYS中查找该键对应的配置键名，
        然后从用户配置中读取（如果有自定义），否则返回原键值。
        
        Args:
            key: 按键值（如 'q', 'e', 'f1' 等）
            
        Returns:
            实际要发送的按键值
            
        Example:
            manager = KeyConfigManager({'Map Key': 'shift+m'})
            actual_key = manager.resolve_common_key('m')  # 返回 'shift+m'
        """
        # 在默认键表中查找对应的配置键名
        config_key_name = None
        for key_name, key_value in DEFAULT_COMMON_KEYS.items():
            if key_value == key:
                config_key_name = key_name
                break

        # 从配置中查询（如果用户自定义了），否则使用传入的键值
        if config_key_name:
            return self.key_config.get(config_key_name, key)

        return key

    def resolve_industry_key(self, key: str) -> str:
        """解析集成工业部分的热键。
        
        先在DEFAULT_INDUSTRY_KEYS中查找该键对应的配置键名，
        然后从用户配置中读取（如果有自定义），否则返回原键值。
        
        Args:
            key: 按键值（如 'q', 'e', 'capslock' 等）
            
        Returns:
            实际要发送的按键值
            
        Example:
            manager = KeyConfigManager({'Place Belt Key': 'alt+e'})
            actual_key = manager.resolve_industry_key('e')  # 返回 'alt+e'
        """
        # 在默认键表中查找对应的配置键名
        config_key_name = None
        for key_name, key_value in DEFAULT_INDUSTRY_KEYS.items():
            if key_value == key:
                config_key_name = key_name
                break

        # 从配置中查询（如果用户自定义了），否则使用传入的键值
        if config_key_name:
            return self.key_config.get(config_key_name, key)

        return key

    def resolve_combat_key(self, key: str) -> str:
        """解析战斗专用部分的热键。
        
        先在DEFAULT_COMBAT_KEYS中查找该键对应的配置键名，
        然后从用户配置中读取（如果有自定义），否则返回原键值。
        
        Args:
            key: 按键值（如 'e' 等）
            
        Returns:
            实际要发送的按键值
            
        Example:
            manager = KeyConfigManager({'Link Skill Key': 'q'})
            actual_key = manager.resolve_combat_key('e')  # 返回 'q'
        """
        # 在默认键表中查找对应的配置键名
        config_key_name = None
        for key_name, key_value in DEFAULT_COMBAT_KEYS.items():
            if key_value == key:
                config_key_name = key_name
                break

        # 从配置中查询（如果用户自定义了），否则使用传入的键值
        if config_key_name:
            return self.key_config.get(config_key_name, key)

        return key
