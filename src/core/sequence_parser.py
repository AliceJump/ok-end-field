def parse_sequence(raw_config: str | list | None) -> list[str]:
    """
    统一解析逗号分隔字符串序列或列表。

    Args:
        raw_config: 字符串、列表或 None。

    Returns:
        list[str]: 解析后非空、去前后空白的 token 列表。

    兼容：
    - 列表格式：["a", "b", "c"] 或 ["a "]（去前后空白）
    - 字符串格式：
      - 逗号分隔："a, b, c" 或 "a，b，c"
      - 中文逗号（，）
      - 前后空白
      - 空项（自动过滤）

    Example:
        ["a", " b ", "c"] -> ["a", "b", "c"]
        " a， b, ,c " -> ["a", "b", "c"]
    """
    if raw_config is None:
        return []

    # 如果是列表，直接转换为字符串列表并去空白
    if isinstance(raw_config, (list, tuple)):
        return [str(item).strip() for item in raw_config if str(item).strip()]

    normalized = str(raw_config).replace("，", ",")
    return [token.strip() for token in normalized.split(",") if token.strip()]


def parse_sequence_list(raw_config: str | list | None) -> list[str]:
    """
    解析列表配置，列表项内的逗号也作为分隔符。

    列表型配置可能由整串粘贴产生，一项内包含多条逗号分隔的规则。

    Example:
        ["刻写,助剂", "理智"] -> ["刻写", "助剂", "理智"]
    """
    if isinstance(raw_config, (list, tuple)):
        return [token for item in raw_config for token in parse_sequence(item)]
    return parse_sequence(raw_config)


def parse_int_sequence(raw_config: str | None) -> list[int]:
    """
    将逗号分隔字符串序列解析为整数列表。

    Args:
        raw_config: 任意可转换为字符串的配置值（可为 None）。

    Returns:
        list[int]: 解析后的整数列表。

    说明：
    - 分割与清洗规则复用 parse_sequence
    - 非法整数字段保持原行为（抛 ValueError）

    Raises:
        ValueError: 当任一 token 不能转换为整数时抛出。

    Example:
        " 36，14, ,108 " -> [36, 14, 108]
    """
    return [int(token) for token in parse_sequence(raw_config)]
