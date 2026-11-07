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


def _split_regex_sequence(text: str) -> list[str]:
    """
    按顶层逗号拆分正则序列；字符集、量词、分组和转义里的逗号保留。
    """
    tokens = []
    start = 0
    square = brace = paren = 0
    class_first = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            if square:
                class_first = False
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if square:
            if char == "]" and not class_first:
                square = 0
            elif not (char == "^" and class_first):
                class_first = False
            continue
        if char == "[":
            square = 1
            class_first = True
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char in ",，" and not brace and not paren:
            token = text[start:index].strip()
            if token:
                tokens.append(token)
            start = index + 1
    token = text[start:].strip()
    if token:
        tokens.append(token)
    return tokens


def parse_sequence_list(raw_config: str | list | None) -> list[str]:
    """
    解析列表配置，按顶层逗号拆分列表项内的多条规则。

    字符集、量词、分组和转义里的逗号属于正则本身，不拆分。

    Example:
        ["刻写,助剂"] -> ["刻写", "助剂"]
        ["货品[甲,乙]", ".{2,4}"] -> ["货品[甲,乙]", ".{2,4}"]
    """
    if isinstance(raw_config, (list, tuple)):
        return [token for item in raw_config for token in _split_regex_sequence(str(item))]
    if raw_config is None:
        return []
    return _split_regex_sequence(str(raw_config))


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
