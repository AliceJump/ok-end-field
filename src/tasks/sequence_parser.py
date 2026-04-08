def parse_sequence(raw_config) -> list[str]:
    """
    统一解析逗号分隔字符串序列。

    兼容：
    - 中文逗号（，）
    - 前后空白
    - 空项（自动过滤）
    """
    if raw_config is None:
        return []

    normalized = str(raw_config).replace("，", ",")
    return [token.strip() for token in normalized.split(",") if token.strip()]


def parse_int_sequence(raw_config) -> list[int]:
    """
    将逗号分隔字符串序列解析为整数列表。

    说明：
    - 分割与清洗规则复用 parse_sequence
    - 非法整数字段保持原行为（抛 ValueError）
    """
    return [int(token) for token in parse_sequence(raw_config)]

