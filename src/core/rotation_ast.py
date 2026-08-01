"""
rotation_ast

实时条件的数据模型、校验、条件求值与动作遍历。

主要功能：
- AST 规范化与校验（递归，非法节点丢弃并收集警告）
- 条件求值（原子 ult1~4 / link / skill>=N；组合 all / any / not）
- 动作遍历生成器（按条件求值产出动作 token，供执行器消费）

依赖：
    无外部依赖（纯 Python 标准库 re + typing）
    运行时由调用方注入 CondProbe（提供场上状态）
"""

import re
from typing import Iterator, Protocol


# 条件原子
_ULT_ATOMS = {"ult1", "ult2", "ult3", "ult4"}
_LINK_ATOM = "link"
_SKILL_RE = re.compile(r"^skill>=(\d+)$")
_SKILL_MIN, _SKILL_MAX = 1, 3  # 技力条最多 3 点 (get_skill_bar_count 的 bars 仅 3 项)

# 动作 token (与 battle_mixin._parse_skill_sequence 对齐)
_PLAIN_ACTIONS = {"1", "2", "3", "4", "e"}
_ULT_ACTION_RE = re.compile(r"^ult_([1-4])$")
_SLEEP_RE = re.compile(r"^sleep_(\d+(?:\.\d+)?)$")
_NORMAL_RE = re.compile(r"^normal_(\d+(?:\.\d+)?)$")


class CondProbe(Protocol):
    """条件求值所需的场上状态探针。

    运行时由 AutoCombatLogic 提供适配器（基于 task）；测试用 mock 实现。
    """

    def ult_available(self, n: int) -> bool:
        """对应 find_one("ult_" + str(n))。"""
        ...

    def link_available(self) -> bool:
        """对应 find_one("default_link_skill", threshold=0.7, ...)。"""
        ...

    def skill_count(self) -> int:
        """对应 get_skill_bar_count()（-1 表未检测到）。"""
        ...


# 动作 token 校验
def _valid_action(token: str) -> bool:
    """校验动作 token 合法性（与 battle_mixin._parse_skill_sequence 对齐）。"""
    if token in _PLAIN_ACTIONS:
        return True
    if _ULT_ACTION_RE.match(token):
        return True
    if _SLEEP_RE.match(token):
        return True
    m = _NORMAL_RE.match(token)
    if m:
        # normal_[n] 要求 n > 0（与 battle_mixin 一致，n<=0 视为非法）
        return float(m.group(1)) > 0
    return False


# 条件原子校验
def _valid_atom(atom: str) -> bool:
    """校验条件原子合法性。"""
    if atom in _ULT_ATOMS:
        return True
    if atom == _LINK_ATOM:
        return True
    m = _SKILL_RE.match(atom)
    if m:
        n = int(m.group(1))
        return _SKILL_MIN <= n <= _SKILL_MAX
    return False


# 规范化
def normalize_ast(data) -> tuple[list, list[str]]:
    """递归校验并规范化 AST。

    Args:
        data: 原始配置值（应为 list[Node]）。

    Returns:
        tuple[list, list[str]]: (clean_ast, warnings)。
            - clean_ast: 规范化后的 AST，非法节点已丢弃。
            - warnings: 被丢弃节点的原因列表（供调用方 log）。

    说明：
        - 顶层非 list → 返回 ([], ["..."])
        - 非法动作 token / 非法条件原子 / 未知结构 → 丢弃并记录
        - skill>=N 的 N 越界（<1 或 >3）→ 丢弃
        - Condition 的 then 缺失或非 list → 视为 []；else 缺失或非 list → 省略
        - 结构化动作 {"t": ...} 本期不支持 → 丢弃
    """
    warnings: list[str] = []

    if not isinstance(data, list):
        return [], [f"顶层必须为列表，实际为 {type(data).__name__}"]

    clean = []
    for node in data:
        normalized = _normalize_node(node, warnings)
        if normalized is not None:
            clean.append(normalized)
    return clean, warnings


def _normalize_node(node, warnings: list[str]):
    """规范化单个 Node（Action 或 Condition）。非法返回 None。"""
    # Action: 简写字符串
    if isinstance(node, str):
        if _valid_action(node):
            return node
        warnings.append(f"非法动作 token 已丢弃: {node!r}")
        return None

    # Condition / 结构化动作: dict
    if isinstance(node, dict):
        if "if" in node:
            return _normalize_condition(node, warnings)
        # 结构化动作 {"t": ...} 本期不支持
        warnings.append(f"不支持的结构化节点已丢弃: {node!r}")
        return None

    warnings.append(f"未知节点类型已丢弃: {node!r}")
    return None


def _normalize_condition(node: dict, warnings: list[str]):
    """规范化 Condition 节点 {"if":..., "then":[...], "else"?:[...]}。

    if 非法 → 丢弃整个 Condition。then/else 递归规范化。
    """
    cond = _normalize_cond(node["if"], warnings)
    if cond is None:
        warnings.append(f"条件非法，整个条件块已丢弃: {node!r}")
        return None

    then_raw = node.get("then", [])
    if not isinstance(then_raw, list):
        warnings.append(f"then 非列表，已视为 []: {then_raw!r}")
        then_raw = []
    then_clean = []
    for child in then_raw:
        n = _normalize_node(child, warnings)
        if n is not None:
            then_clean.append(n)

    result = {"if": cond, "then": then_clean}

    if "else" in node:
        else_raw = node["else"]
        if not isinstance(else_raw, list):
            warnings.append(f"else 非列表，已忽略: {else_raw!r}")
        else:
            else_clean = []
            for child in else_raw:
                n = _normalize_node(child, warnings)
                if n is not None:
                    else_clean.append(n)
            result["else"] = else_clean

    return result


def _normalize_cond(cond, warnings: list[str]):
    """规范化条件 Cond。非法返回 None。"""
    # 原子
    if isinstance(cond, str):
        if _valid_atom(cond):
            return cond
        warnings.append(f"非法条件原子已丢弃: {cond!r}")
        return None

    # 组合
    if isinstance(cond, dict):
        if "all" in cond:
            subs = cond["all"]
            if not isinstance(subs, list):
                warnings.append("all 非列表，已丢弃整个 all")
                return None
            cleaned = []
            for c in subs:
                n = _normalize_cond(c, warnings)
                if n is not None:
                    cleaned.append(n)
            if not cleaned:
                warnings.append("all 内无有效条件，已丢弃整个 all")
                return None
            return {"all": cleaned}
        if "any" in cond:
            subs = cond["any"]
            if not isinstance(subs, list):
                warnings.append(f"any 非列表，已视为 []: {subs!r}")
                subs = []
            cleaned = []
            for c in subs:
                n = _normalize_cond(c, warnings)
                if n is not None:
                    cleaned.append(n)
            return {"any": cleaned}
        if "not" in cond:
            inner = _normalize_cond(cond["not"], warnings)
            if inner is None:
                warnings.append("not 内部条件非法，已丢弃整个 not")
                return None
            return {"not": inner}

    warnings.append(f"未知条件结构已丢弃: {cond!r}")
    return None


# 条件求值
def eval_cond(cond, probe: CondProbe) -> bool:
    """递归求值条件。

    未知结构保守返回 False（不触发动作）。
    """
    if isinstance(cond, str):
        return _eval_atom(cond, probe)
    if isinstance(cond, dict):
        if "all" in cond:
            return all(eval_cond(c, probe) for c in cond["all"])
        if "any" in cond:
            return any(eval_cond(c, probe) for c in cond["any"])
        if "not" in cond:
            return not eval_cond(cond["not"], probe)
    return False


def _eval_atom(atom: str, probe: CondProbe) -> bool:
    """求值原子条件。"""
    if atom in _ULT_ATOMS:
        return probe.ult_available(int(atom[3:]))
    if atom == _LINK_ATOM:
        return probe.link_available()
    m = _SKILL_RE.match(atom)
    if m:
        n = int(m.group(1))
        if _SKILL_MIN <= n <= _SKILL_MAX:
            return probe.skill_count() >= n
    return False


# 动作遍历生成器
def iter_actions(nodes: list, probe: CondProbe) -> Iterator[str]:
    """递归遍历 AST，按条件求值产出动作 token。

    - 顺序遍历 list[Node]
    - Action(str) → yield token
    - Condition → eval_cond(if) 选 then/else 分支，递归 yield from
    - 未知结构 → 忽略

    Example:
        >>> # 当 probe.link_available() 为 True 时
        >>> list(iter_actions(["1", {"if": "link", "then": ["e"]}], probe))
        ['1', 'e']
    """
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict) and "if" in node:
            branch = node["then"] if eval_cond(node["if"], probe) else node.get("else", [])
            yield from iter_actions(branch, probe)
        # else: 忽略未知结构
