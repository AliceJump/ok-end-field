"""通用文本匹配工具。"""

def edit_distance(left: str, right: str, max_distance: int | None = None) -> int | None:
    """计算 Levenshtein 距离，插入、删除和替换一个字符的代价均为 1。

    Args:
        left: 待比较的字符串。
        right: 待比较的另一个字符串。
        max_distance: 允许的最大编辑距离，须为非负整数；None 表示不限制。

    Returns:
        阈值内的实际编辑距离（包含 0），超限时返回 None。
        未设置阈值时返回完整编辑距离。
    """
    # 让较短的 left 对应滚动数组，将额外空间限制为 O(min(m, n))。
    if len(left) > len(right):
        return edit_distance(right, left, max_distance=max_distance)
    # 长度差是编辑距离的下界，超限时无需继续计算。
    if max_distance is not None and len(right) - len(left) > max_distance:
        return None
    previous = list(range(len(left) + 1))
    for i, right_char in enumerate(right, 1):
        current = [i]
        for j, left_char in enumerate(left, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        # 当前 right 前缀与 left 任意前缀的距离都超限时，可以提前结束：
        # 后续行的最小值不会减小，因此最终距离也必然超限。
        if max_distance is not None and min(current) > max_distance:
            return None
        previous = current
    distance = previous[-1]
    return distance if max_distance is None or distance <= max_distance else None


