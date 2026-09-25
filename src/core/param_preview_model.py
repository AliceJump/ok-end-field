"""任务卡「小眼睛」参数概要弹层的归属模型（纯函数，无 Qt 依赖）。

从 ok-script-toolkit 扩展 ``media/console/taskCard.js`` 的 ``groupPopContent``
移植，数据源换成 ok-script 框架的任务对象：

- ``config``           运行期配置（键序即展示序）
- ``default_config``   出厂默认值
- ``config_type``      键 → 类型元数据（``sub_configs`` / ``options`` / ``hidden`` / 按钮）
- ``default_config_group``  静态分组（组名 → 字段列表），配合 ``find_group_selector``
  识别 ``register_config_groups`` 生成的分组下拉

归属语义（与扩展逐条对齐，见设计文档
``ok-script-toolkit/.workbuddy/design/eye-popup-design.md`` 1.1 节）：

1. 分组吸收显隐：组名字段自身 sub_configs 的子项吸收进组 children。
2. 渲染顺序：静态组 → 根条件组 → 其他参数；一个字段全弹层只出现一次。
3. 只有「根显隐源」开条件组：被控字段绝不自己开组。
4. 条件组结构：组头 = 父字段显示名 +「显隐组」徽标；组内每条规则 =
   规则行（值 → N 项）+ 受控字段列表。
5. 嵌套 cap 2：第 3 层起拍平为「字段行 + 条件摘要徽标」。
6. 整组吸收则跳过：条件组受控字段全被静态组吸收 → 不建组，父字段落回其他参数。
7. 其他参数：未归属任何组/条件链的字段。
"""

from __future__ import annotations

# 条件组最大嵌套层数；第 3 层起拍平为字段行 + 条件摘要徽标
COND_NEST_CAP = 2


def _as_key_list(value):
    """sub_configs 的值归一成 key 列表（单个 str / 列表 / 元组 / None）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _button_config(meta):
    """对齐框架 ConfigContentMixin.__is_button_config 的按钮判定。"""
    if not isinstance(meta, dict):
        return False
    if meta.get("type") == "button":
        return True
    return "type" not in meta and ("buttons" in meta or "callback" in meta)


def find_group_selector(config_type, declared_groups):
    """识别 ``register_config_groups`` 生成的分组下拉（与探针同源）。

    条件：type=drop_down、options 与 sub_configs 的规则键完全覆盖声明分组，
    且每条规则的子项与声明分组一致。返回 (下拉键名, 规则分组表)。
    """
    if not isinstance(config_type, dict):
        return None, {}
    for key, meta in config_type.items():
        if not isinstance(meta, dict) or meta.get("type") != "drop_down":
            continue
        options = meta.get("options")
        rules = meta.get("sub_configs")
        if not isinstance(options, (list, tuple)) or not isinstance(rules, dict):
            continue
        normalized = {str(name): _as_key_list(children) for name, children in rules.items()}
        if not options or not declared_groups:
            continue
        if not all(
            normalized.get(str(group_name)) == list(children) for group_name, children in declared_groups.items()
        ):
            continue
        if not all(str(option) in normalized for option in options):
            continue
        return str(key), normalized
    return None, {}


def rule_value_label(choice, is_bool, tr):
    """规则值的显示名：bool 强制翻「开/关」，其余走 tr(option)（对齐下拉控件）。"""
    if isinstance(choice, bool):
        return tr("开") if choice else tr("关")
    text = str(choice)
    if is_bool and text.lower() in ("true", "false"):
        return tr("开") if text.lower() == "true" else tr("关")
    return tr(text)


def build_param_preview(config, config_type, default_config, default_config_group, tr):
    """从任务配置构建弹层内容模型；无可展示内容时返回 None。

    返回结构（供 GUI 渲染，与展示控件解耦）::

        {
            "blocks": [
                {"type": "static", "name": 组名, "count": n, "nodes": [...]},
                {"type": "cond",   "name": 父字段显示名, "tag": "显隐组",
                 "rules": [{"label": 值, "count": n, "nodes": [...]}]},
                {"type": "others", "name": "其他参数"/"参数", "count": n, "nodes": [...]},
            ],
        }
        node = {"type": "item", "key", "label", "badge": None | 条件摘要}
             | {"type": "static", "name", "count", "nodes"}   # 嵌套静态组
             | {"type": "cond", ...}                          # 嵌套条件组
    """
    config = config if isinstance(config, dict) else {}
    config_type = config_type if isinstance(config_type, dict) else {}
    default_config = default_config if isinstance(default_config, dict) else {}

    # ── 静态分组：default_config_group + 分组下拉（find_group_selector 同源）──
    groups = {}
    for group_name, children in (default_config_group or {}).items():
        groups[str(group_name)] = [str(key) for key in _as_key_list(children)]
    _selector, selector_groups = find_group_selector(config_type, groups)
    groups.update(selector_groups)

    # ── 字段集合：config → default_config → config_type 的键序，过滤不可展示项 ──
    pure_group_labels = {name for name in groups if name not in default_config and name not in config}
    fields = []
    fields_by_key = {}
    for key in dict.fromkeys([*config.keys(), *default_config.keys(), *config_type.keys()]):
        key = str(key)
        if key in fields_by_key or key.startswith("_") or key in pure_group_labels:
            continue
        meta = config_type.get(key)
        if isinstance(meta, dict) and meta.get("hidden"):
            continue
        if _button_config(meta):
            continue
        if config.get(key) is None and default_config.get(key) is None and not isinstance(meta, dict):
            continue
        node = {"key": key, "rules": cond_rules_of(key, config_type, config, default_config, tr)}
        fields.append(node)
        fields_by_key[key] = node
    if not fields:
        return None

    def label_of(key):
        return tr(key)

    # ── 规则 1：分组吸收显隐（组名字段自身的 sub_configs 子项并入组）──
    for group_name, children in groups.items():
        field = fields_by_key.get(group_name)
        if field is None:
            continue
        declared = set(children)
        for rule in field["rules"]:
            for key in rule["keys"]:
                if key not in declared:
                    children.append(key)
                    declared.add(key)

    group_children = {key for children in groups.values() for key in children}

    # ── 递归收集全部显隐受控字段（防环）：被控字段不再自己开条件组 ──
    controlled_all = set()

    def collect_controlled(key, seen):
        field = fields_by_key.get(key)
        if field is None:
            return
        for rule in field["rules"]:
            for child in rule["keys"]:
                if child not in fields_by_key or child in seen:
                    continue
                if child not in controlled_all:
                    controlled_all.add(child)
                    collect_controlled(child, seen | {child})

    for field in fields:
        if field["rules"]:
            collect_controlled(field["key"], {field["key"]})

    # ── 条件组父字段 = 根显隐源 ──
    cond_parents = [
        field
        for field in fields
        if field["key"] not in groups
        and field["key"] not in group_children
        and field["key"] not in controlled_all
        and field["rules"]
    ]
    cond_parent_keys = {field["key"] for field in cond_parents}

    orphan_count = sum(
        1
        for field in fields
        if field["key"] not in groups
        and field["key"] not in group_children
        and field["key"] not in controlled_all
        and field["key"] not in cond_parent_keys
    )
    if not groups and not cond_parents and not orphan_count:
        return None

    rendered = set()  # 全弹层去重：一个字段只渲染一次

    def cond_badge(rules):
        """拍平徽标摘要：值→受控字段显示名，多规则分号连接。"""
        parts = []
        for rule in rules:
            names = "、".join(label_of(key) for key in rule["keys"])
            parts.append(f"{rule['label']}→{names}")
        return "；".join(parts)

    def render_item(key, nodes, depth):
        # 被 groups / sub_configs 引用的键可能是 hidden、button 或三处皆无的
        # 悬空引用（fields_by_key 里没有）——按「悬空引用跳过」语义跳过不渲染
        if key in rendered or key not in fields_by_key:
            return
        rendered.add(key)
        node = {"type": "item", "key": key, "label": label_of(key), "badge": None}
        rules = fields_by_key[key]["rules"]
        if rules and depth >= COND_NEST_CAP:
            node["badge"] = cond_badge(rules)
        nodes.append(node)
        if rules and depth < COND_NEST_CAP:
            append_cond_group(key, rules, nodes, depth)

    def append_cond_group(key, rules, nodes, depth):
        """一个父字段 = 一个条件组；受控字段已全部归属 → 整组跳过返回 False。"""
        has_pending = any(child in fields_by_key and child not in rendered for rule in rules for child in rule["keys"])
        if not has_pending:
            return False
        rendered.add(key)
        block = {"type": "cond", "key": key, "name": label_of(key), "tag": tr("显隐组"), "rules": []}
        for rule in rules:
            sub_nodes = []
            for child in rule["keys"]:
                render_item(child, sub_nodes, depth + 1)
            block["rules"].append({"label": rule["label"], "count": len(sub_nodes), "nodes": sub_nodes})
        nodes.append(block)
        return True

    def append_static_group(group_name, nodes, depth, seen):
        children = groups.get(group_name, [])
        block = {"type": "static", "name": label_of(group_name), "count": 0, "nodes": []}
        for key in children:
            # 自引用组防环：跳过递归但仍渲染字段行（组名本体可见且只出现一次）
            if key in groups:
                if key in seen:
                    render_item(key, block["nodes"], depth)
                else:
                    append_static_group(key, block["nodes"], depth + 1, seen | {key})
            else:
                render_item(key, block["nodes"], depth)
        block["count"] = len(block["nodes"])
        nodes.append(block)

    blocks = []

    # 静态组：嵌套子组随父组渲染；自引用不算被嵌套
    nested = {key for group_name, children in groups.items() for key in children if key in groups and key != group_name}
    for group_name in groups:
        if group_name not in nested:
            append_static_group(group_name, blocks, 0, {group_name})

    # 条件组：只给根显隐源开组
    skipped_cond_parents = []
    for field in cond_parents:
        if not append_cond_group(field["key"], field["rules"], blocks, 0):
            skipped_cond_parents.append(field["key"])

    # 其他参数：未渲染的剩余字段 + 被整组跳过的条件源
    others = [
        field["key"]
        for field in fields
        if field["key"] not in rendered
        and field["key"] not in groups
        and (field["key"] not in cond_parent_keys or field["key"] in skipped_cond_parents)
    ]
    if others:
        block = {"type": "others", "name": tr("其他参数") if groups else tr("参数"), "count": len(others), "nodes": []}
        for key in others:
            render_item(key, block["nodes"], 0)
        blocks.append(block)

    return {"blocks": blocks}


def cond_rules_of(key, config_type, config, default_config, tr):
    """单字段的显隐规则 → [{label, keys}]（对齐扩展 condRulesOf）。"""
    meta = config_type.get(key) if isinstance(config_type, dict) else None
    if not isinstance(meta, dict):
        return []
    sub_configs = meta.get("sub_configs")
    if not isinstance(sub_configs, dict) or not sub_configs:
        return []
    current = config.get(key, default_config.get(key)) if isinstance(config, dict) else None
    is_bool = isinstance(current, bool) or isinstance(
        default_config.get(key) if isinstance(default_config, dict) else None, bool
    )
    rules = []
    for choice, keys in sub_configs.items():
        key_list = [str(child) for child in _as_key_list(keys)]
        if not key_list:
            continue
        rules.append({"label": rule_value_label(choice, is_bool, tr), "keys": key_list})
    return rules
