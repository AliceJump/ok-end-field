"""自定义 tab 的 web（浏览器）实现。

桌面端的自定义 tab（``src/gui/GlobalConfigTab.py``、``src/gui/AccountConfigTab.py``）
基于 Qt；本模块用 ok-script 的 ``WebCustomTab`` 机制提供对应的浏览器页面：

- ``GlobalConfigWebTab`` —— 全局配置（战斗/键位/基础/滑索配置）
- ``AccountWebTab`` —— 账号配置（账号列表、账号任务覆盖、地图同步 content）

前端资产在 ``assets/web-tabs/<id>/``（ES module，导出 ``mount(host, api)``），
由框架静态挂载到 ``/task-tabs/<id>/assets/``，数据通过
``POST /api/task-tabs/<id>/query|action/<operation>`` 交换。

启动方式：config 里注册 ``web_tabs`` 并以 web GUI 启动
（见 ``src/config.py`` 的 ``OK_GUI`` 环境变量说明）。
"""

from __future__ import annotations

import copy
from typing import Any

from ok.task.web import WebCustomTab, WebTabConfig, task_tab_action, task_tab_query

from src.core.global_config_store import GLOBAL_CONFIG_GROUPS, get_all_visible_configs, get_global_config
from src.tasks.account.account_config_schema import (
    account_name_by_key,
    build_virtual_config,
    coerce_like,
    is_supported_value,
    parse_accounts,
    resolve_account_key_by_username,
    task_storage_name,
)
from src.tasks.account.account_scope_store import (
    get_account_map_content,
    load_overrides,
    remove_account_task_overrides,
    set_account_map_content,
    set_account_task_overrides,
    sync_account_list_text,
    update_overrides,
)


def _control_for(default_value: Any, type_meta: Any) -> tuple[str | None, Any]:
    """把配置项映射为 web 控件类型；返回 ``(control, options)``，None 表示不可编辑。"""
    if not isinstance(type_meta, dict):
        type_meta = {}
    kind = type_meta.get("type")
    if kind == "drop_down":
        return "select", list(type_meta.get("options", []))
    if kind == "cascade_drop_down":
        # 桌面端由 cascade_dropdown_patch 渲染的分类级联下拉（web 用 optgroup）。
        return "cascade", type_meta
    if kind == "cond_sequence_editor":
        # 桌面端由 conditional_rotation_patch 渲染的实时条件编辑器。
        return "cond_sequence", None
    if kind in {"button", "global"}:
        return None, None
    if isinstance(default_value, bool):
        return "switch", None
    if isinstance(default_value, (int, float)):
        return "number", None
    if isinstance(default_value, list):
        return "list", type_meta.get("options_available")
    if isinstance(default_value, str):
        return "text", None
    return None, None


def _normalize_sub_configs(sub_configs: Any) -> dict[str, list[str]]:
    """把 sub_configs 的键统一成字符串（bool → "true"/"false"），供前端比较。"""
    if not isinstance(sub_configs, dict):
        return {}
    result: dict[str, list[str]] = {}
    for value, keys in sub_configs.items():
        key = ("true" if value else "false") if isinstance(value, bool) else str(value)
        if isinstance(keys, str):
            result[key] = [keys]
        elif isinstance(keys, (list, tuple, set)):
            result[key] = [str(item) for item in keys]
    return result


def _build_items(
    defaults: dict[str, Any],
    values: dict[str, Any],
    base_values: dict[str, Any],
    config_description: dict[str, Any] | None,
    config_type: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """把一组配置键值渲染为 web 前端的 item 描述列表。"""
    description = config_description if isinstance(config_description, dict) else {}
    types = config_type if isinstance(config_type, dict) else {}
    items: list[dict[str, Any]] = []
    for key, default_value in defaults.items():
        type_meta = types.get(key)
        control, options = _control_for(default_value, type_meta)
        if control is None:
            continue
        item: dict[str, Any] = {
            "key": key,
            "control": control,
            "value": values.get(key, default_value),
            "default": default_value,
        }
        if control == "select":
            item["options"] = options
        if control == "cascade":
            item["options"] = options.get("options", {}) if isinstance(options, dict) else {}
            labels = options.get("labels", {}) if isinstance(options, dict) else {}
            if labels:
                item["labels"] = labels
        if control == "list" and options:
            item["options_available"] = options
        if control == "cond_sequence":
            # 实时条件 AST 列表，前端提供结构化编辑器；值原样下发。
            item["value"] = copy.deepcopy(values.get(key, default_value))
        if isinstance(type_meta, dict):
            sub_configs = _normalize_sub_configs(type_meta.get("sub_configs"))
            if sub_configs:
                item["sub_configs"] = sub_configs
        text = description.get(key)
        if text:
            item["description"] = str(text)
        if key in base_values:
            item["base_value"] = base_values[key]
        items.append(item)
    return items


class GlobalConfigWebTab(WebCustomTab):
    """web 端「全局配置」页，对应桌面端 ``GlobalConfigTab``。"""

    web_tab = WebTabConfig(
        id="global-config",
        name="全局配置",
        asset_dir="assets/web-tabs/global-config",
        icon="settings",
        position="scroll",
        add_after_default_tabs=False,
    )

    def _config_payload(self, name: str, config: Any, option: Any) -> dict[str, Any]:
        values = {key: config.get(key, default) for key, default in option.default_config.items()}
        return {
            "name": name,
            "description": option.description,
            "items": _build_items(
                option.default_config,
                values,
                {},
                option.config_description,
                option.config_type,
            ),
        }

    @task_tab_query("schema")
    def get_schema(self) -> dict[str, Any]:
        """返回分组后的全局配置 schema 与当前值。"""
        visible = {name: (config, option) for name, config, option in get_all_visible_configs()}
        shown: set[str] = set()
        groups: list[dict[str, Any]] = []
        for group_name, config_names in GLOBAL_CONFIG_GROUPS.items():
            entries = []
            for config_name in config_names:
                found = visible.get(config_name)
                if found is None:
                    continue
                config, option = found
                shown.add(config_name)
                entries.append(self._config_payload(config_name, config, option))
            if entries:
                groups.append({"name": group_name, "configs": entries})

        others = [
            self._config_payload(name, config, option)
            for name, (config, option) in visible.items()
            if name not in shown
        ]
        if others:
            groups.append({"name": "其他配置", "configs": others})
        return {"groups": groups}

    @task_tab_action("set")
    def set_value(self, payload: dict[str, Any]) -> dict[str, Any]:
        """写入一个配置项并持久化。payload: {config, key, value}。"""
        config_name = str(payload.get("config", ""))
        key = str(payload.get("key", ""))
        value = payload.get("value")
        if not config_name or not key:
            raise ValueError("config and key are required")
        config = get_global_config(config_name)
        default_value = config.get_default(key)
        if default_value is None and key not in config.default:
            raise ValueError(f"Unknown config key: {config_name}/{key}")
        option = next((opt for name, _cfg, opt in get_all_visible_configs() if name == config_name), None)
        type_meta = option.config_type.get(key) if option is not None and option.config_type else None
        control, extra = _control_for(default_value, type_meta)
        if control is None:
            raise ValueError(f"Config key is not editable: {config_name}/{key}")
        if control == "cascade":
            # 分类级联下拉：值必须出现在某个分类的选项里。
            allowed = {
                str(value)
                for values in (extra.get("options", {}) if isinstance(extra, dict) else {}).values()
                for value in values
            }
            if str(value) not in allowed:
                raise ValueError(f"Invalid option for {config_name}/{key}: {value!r}")
        elif control == "cond_sequence":
            # 实时条件 AST：清洗非法节点后再落盘（与桌面端 ConditionalRotationPanel 一致）。
            from src.core.rotation_ast import normalize_ast

            cleaned, _warnings = normalize_ast(value if isinstance(value, list) else [])
            value = cleaned
        else:
            value = coerce_like(default_value, value)
            if not is_supported_value(value) or type(value) is not type(default_value):
                raise ValueError(f"Invalid value type for {config_name}/{key}")
        config[key] = value  # Config.__setitem__ 校验并自动保存
        return {"ok": True, "value": config.get(key)}

    @task_tab_action("reset")
    def reset_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把一个全局配置整体恢复默认。payload: {config}。"""
        config_name = str(payload.get("config", ""))
        config = get_global_config(config_name)
        config.reset_to_default()
        return {"ok": True}


class AccountWebTab(WebCustomTab):
    """web 端「账号配置」页，对应桌面端 ``AccountConfigTab``。"""

    web_tab = WebTabConfig(
        id="account-config",
        name="账号配置",
        asset_dir="assets/web-tabs/account-config",
        position="scroll",
        add_after_default_tabs=False,
    )

    def _collect_tasks(self) -> list[Any]:
        """收集支持多账号配置的任务，附加全局滑索/键位代理（与桌面端一致）。"""
        from src.gui.AccountConfigTab import GlobalKeyConfigProxy, GlobalZipLineConfigProxy

        tasks: list[Any] = []
        seen: set[str] = set()
        for task in list(getattr(self.executor, "onetime_tasks", [])) + list(
            getattr(self.executor, "trigger_tasks", [])
        ):
            if not getattr(task, "support_multi_account", False):
                continue
            class_name = task_storage_name(task)
            if class_name in seen:
                continue
            seen.add(class_name)
            tasks.append(task)
        tasks.append(GlobalZipLineConfigProxy)
        tasks.append(GlobalKeyConfigProxy)
        return tasks

    def _find_task(self, task_class: str) -> Any:
        for task in self._collect_tasks():
            if task_storage_name(task) == task_class:
                return task
        raise ValueError(f"Unknown task: {task_class}")

    def _account_entries(self, overrides_data: dict[str, Any]) -> list[dict[str, str]]:
        """构建账号选择项（去重、解析显示名），逻辑与桌面端 rebuild_account_selector 对齐。"""
        raw_items: list[tuple[str, str]] = []
        for entry in parse_accounts(str(overrides_data.get("account_list_text", "") or "")):
            username = str(entry.get("username", "")).strip()
            if not username:
                continue
            raw_items.append((resolve_account_key_by_username(overrides_data, username) or username, username))

        for account_key in (overrides_data.get("accounts") or {}):
            raw_items.append((str(account_key), account_name_by_key(overrides_data, str(account_key))))

        for account_key in (overrides_data.get("map_contents") or {}):
            raw_items.append((str(account_key), account_name_by_key(overrides_data, str(account_key))))

        entries: list[dict[str, str]] = []
        seen: set[str] = set()
        used_display: set[str] = set()
        for account_key, account_name in raw_items:
            if not account_key or account_key in seen:
                continue
            seen.add(account_key)
            display = account_name or account_key
            if display in used_display:
                display = f"{display} ({account_key[-6:]})"
            used_display.add(display)
            entries.append({"key": account_key, "name": account_name or account_key, "display": display})
        return entries

    def _task_entries(self) -> list[dict[str, str]]:
        return [
            {"class": task_storage_name(task), "name": str(getattr(task, "name", task_storage_name(task)))}
            for task in self._collect_tasks()
        ]

    @task_tab_query("overview")
    def get_overview(self) -> dict[str, Any]:
        """返回账号列表、任务列表与地图同步 content。"""
        overrides_data = load_overrides(force=True)
        map_contents = overrides_data.get("map_contents") or {}
        return {
            "account_list_text": str(overrides_data.get("account_list_text", "") or ""),
            "accounts": self._account_entries(overrides_data),
            "tasks": self._task_entries(),
            "map_contents": {str(key): str(value or "") for key, value in map_contents.items()},
        }

    @task_tab_query("task_config")
    def get_task_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        """返回某账号在某任务下的可编辑配置项。payload: {account_key, account_name, task, only_diff}。"""
        account_key = str(payload.get("account_key", "") or "")
        account_name = str(payload.get("account_name", "") or "")
        task_class = str(payload.get("task", "") or "")
        only_diff = bool(payload.get("only_diff", False))
        if not account_key:
            raise ValueError("account_key is required")
        task = self._find_task(task_class)

        overrides_data = load_overrides(force=True)
        initial, defaults, editable_keys, base_values, total = build_virtual_config(
            task,
            account_key,
            account_name,
            overrides_data,
            only_diff=only_diff,
        )

        config_description = dict(task.config_description or {})
        config_description.update(getattr(task, "account_config_description", {}) or {})
        config_type = dict(task.config_type or {})
        config_type.update(getattr(task, "account_config_type", {}) or {})
        config_type = {key: value for key, value in config_type.items() if key in editable_keys}
        filtered_defaults = {key: defaults[key] for key in editable_keys}

        return {
            "task": task_class,
            "account_key": account_key,
            "account_name": account_name_by_key(overrides_data, account_key) or account_name,
            "count": len(editable_keys),
            "total": total,
            "items": _build_items(
                filtered_defaults,
                initial,
                base_values,
                config_description,
                config_type,
            ),
        }

    @task_tab_action("save_account_list")
    def save_account_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        """保存账号列表（每行一个账号名/手机号）。payload: {text}。"""
        summary = sync_account_list_text(str(payload.get("text", "") or "").strip())
        return {"ok": True, "summary": summary}

    @task_tab_action("save_task_overrides")
    def save_task_overrides(self, payload: dict[str, Any]) -> dict[str, Any]:
        """保存当前账号的任务覆盖（完整快照语义）。payload: {account_key, task, values}。"""
        account_key = str(payload.get("account_key", "") or "")
        task_class = str(payload.get("task", "") or "")
        values = payload.get("values")
        if not account_key or not task_class:
            raise ValueError("account_key and task are required")
        if not isinstance(values, dict):
            raise ValueError("values must be an object")
        task = self._find_task(task_class)

        overrides_data = load_overrides(force=True)
        initial, _defaults, editable_keys, _base, _total = build_virtual_config(
            task, account_key, "", overrides_data, only_diff=False
        )
        full_config = {key: copy.deepcopy(value) for key, value in initial.items()}
        for key in editable_keys:
            if key in values:
                full_config[key] = copy.deepcopy(values[key])

        set_account_task_overrides(account_key, task_class, full_config)
        return {"ok": True}

    @task_tab_action("save_map_content")
    def save_map_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        """保存账号的地图同步 content。payload: {account_key, content}。"""
        account_key = str(payload.get("account_key", "") or "")
        if not account_key:
            raise ValueError("account_key is required")
        set_account_map_content(account_key, str(payload.get("content", "") or ""))
        return {"ok": True}

    @task_tab_action("clear_task_override")
    def clear_task_override(self, payload: dict[str, Any]) -> dict[str, Any]:
        """清空当前账号某任务的覆盖。payload: {account_key, task}。"""
        account_key = str(payload.get("account_key", "") or "")
        task_class = str(payload.get("task", "") or "")
        if not account_key or not task_class:
            raise ValueError("account_key and task are required")
        remove_account_task_overrides(account_key, task_class)
        return {"ok": True}

    @task_tab_action("clear_account_overrides")
    def clear_account_overrides(self, payload: dict[str, Any]) -> dict[str, Any]:
        """清空当前账号全部任务覆盖。payload: {account_key}。"""
        account_key = str(payload.get("account_key", "") or "")
        if not account_key:
            raise ValueError("account_key is required")

        def clear_account(latest):
            accounts = latest.get("accounts", {})
            if account_key in accounts:
                accounts.pop(account_key, None)
            elif account_name_by_key(latest, account_key) in accounts:
                accounts.pop(account_name_by_key(latest, account_key), None)
            return latest

        update_overrides(clear_account)
        return {"ok": True}

    @task_tab_query("map_content")
    def get_map_content(self, payload: dict[str, Any]) -> dict[str, Any]:
        """读取账号的地图同步 content。payload: {account_key, account_name}。"""
        account_key = str(payload.get("account_key", "") or "")
        if not account_key:
            raise ValueError("account_key is required")
        account_name = str(payload.get("account_name", "") or "")
        return {"content": get_account_map_content(account_key, account_name=account_name)}
