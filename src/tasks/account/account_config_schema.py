"""Pure helpers shared by the Qt account tab and the web account tab.

无 Qt 依赖：供 ``src/gui/AccountConfigTab.py``（桌面）与
``src/gui/web_tabs.py``（浏览器）复用，避免两边逻辑漂移。
"""

from __future__ import annotations

import copy
from typing import Any

from src.tasks.account.account_scope_store import parse_account_list_text

# 任何任务下都不允许账号覆盖的配置键（与桌面端账号页一致）。
ALWAYS_HIDDEN_CONFIG_KEYS = {"多账户模式", "多账户独立配置", "账号列表"}


def parse_accounts(account_list_text: str) -> list[dict[str, str]]:
    """Parse account list text into structured account dictionaries with username and password."""
    accounts: list[dict[str, str]] = []
    seen = set()
    for entry in parse_account_list_text(account_list_text):
        username = str(entry.get("username", "")).strip()
        if username and username not in seen:
            seen.add(username)
            accounts.append({"username": username, "password": str(entry.get("password", ""))})
    return accounts


def resolve_account_key_by_username(overrides_data: dict[str, Any], username: str) -> str:
    """Resolve the internal account key from a username by looking up the account registry."""
    username = username.strip()
    if not username:
        return ""

    registry = overrides_data.get("account_registry") or {}
    for account_id, meta in registry.items():
        if not isinstance(account_id, str) or not isinstance(meta, dict):
            continue

        current_name = str(meta.get("username", "") or "").strip()
        if username == current_name:
            return account_id

    return ""


def account_name_by_key(overrides_data: dict[str, Any], account_key: str) -> str:
    """Get the account display name (username) from the internal account key."""
    if not account_key:
        return ""

    registry = overrides_data.get("account_registry") or {}
    meta = registry.get(account_key)
    if isinstance(meta, dict):
        username = str(meta.get("username", "") or "").strip()
        if username:
            return username
    return account_key


def is_supported_value(value: Any) -> bool:
    """Check if a value is a supported configuration type."""
    return isinstance(value, (bool, int, float, str, list))


def config_key_set(task: Any, attribute: str) -> set[str]:
    """Extract configuration keys from a task attribute, handling strings, lists, tuples, and sets."""
    value = getattr(task, attribute, None)
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(key) for key in value}
    return set()


def task_storage_name(task: Any) -> str:
    """Get the storage name for a task used in account override keys."""
    return str(getattr(task, "account_override_name", task.__class__.__name__))


def account_config_schema(task: Any, task_override: dict[str, Any]) -> dict[str, Any]:
    """Build the configuration schema for a task including default and account-specific settings."""
    schema = dict(task.default_config)
    extra_defaults = getattr(task, "account_config_defaults", None)
    if isinstance(extra_defaults, dict):
        schema.update(extra_defaults)

    whitelist = config_key_set(task, "account_config_whitelist")
    for key in whitelist:
        if key in schema:
            continue
        if key in task_override:
            schema[key] = task_override[key]
        elif key in task.config:
            schema[key] = dict.get(task.config, key)
    return schema


def account_config_rules(task: Any) -> tuple[set[str], set[str]]:
    """Determine configuration key blacklist and whitelist for account overrides."""
    blacklist = ALWAYS_HIDDEN_CONFIG_KEYS | config_key_set(task, "account_config_blacklist")
    whitelist = config_key_set(task, "account_config_whitelist")

    config_types = dict(task.config_type or {})
    config_types.update(getattr(task, "account_config_type", {}) or {})
    for key, type_meta in config_types.items():
        if not isinstance(type_meta, dict):
            continue
        if type_meta.get("type") == "button" or (
            "type" not in type_meta and ("buttons" in type_meta or "callback" in type_meta)
        ):
            blacklist.add(key)
        sub_configs = type_meta.get("sub_configs")
        if isinstance(sub_configs, dict):
            other_keys = sub_configs.get("其他配置", [])
            if isinstance(other_keys, str):
                blacklist.add(other_keys)
            elif isinstance(other_keys, (list, tuple, set)):
                blacklist.update(str(item) for item in other_keys)

    if "配置选择" in task.default_config or "配置选择" in config_types:
        whitelist.add("配置选择")

    whitelist -= blacklist
    return blacklist, whitelist


def account_config_base_value(task: Any, key: str, default_value: Any) -> Any:
    """Get the base configuration value for a key, falling back to default if not in task config."""
    if key in task.config:
        return dict.get(task.config, key, default_value)
    provider = getattr(task, "get_account_config_base_value", None)
    if callable(provider):
        return provider(key, default_value)
    return default_value


def coerce_like(base_value: Any, value: Any) -> Any:
    """Coerce a value to match the type of the base value, with fallback for incompatible types."""
    if base_value is None or value is None:
        return value

    if isinstance(base_value, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "on", "是", "开启"}:
                return True
            if text in {"false", "0", "no", "off", "否", "关闭"}:
                return False
        return base_value

    if isinstance(base_value, int) and not isinstance(base_value, bool):
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return base_value
        return base_value

    if isinstance(base_value, float):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return base_value
        return base_value

    if isinstance(base_value, list):
        return value if isinstance(value, list) else base_value

    if isinstance(base_value, str):
        return str(value)

    return value if isinstance(value, type(base_value)) else base_value


def build_virtual_config(
    task: Any,
    account_key: str,
    account_name: str,
    overrides_data: dict[str, Any],
    only_diff: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, Any], int]:
    """Build the editable values for one account/task pair.

    返回 ``(initial, defaults, editable_keys, base_values, total_supported_keys)``。
    ``initial`` 已按 ``only_diff`` 过滤；调用方自行渲染。
    """
    task_class = task_storage_name(task)
    accounts = overrides_data.get("accounts") or {}
    account_map = accounts.get(account_key, {})
    if account_name and (not isinstance(account_map, dict) or (not account_map and account_name in accounts)):
        legacy_account_map = accounts.get(account_name, {})
        if isinstance(legacy_account_map, dict):
            account_map = legacy_account_map
    task_override = account_map.get(task_class, {}) if isinstance(account_map, dict) else {}

    defaults: dict[str, Any] = {}
    initial: dict[str, Any] = {}
    base_values: dict[str, Any] = {}
    editable_keys: list[str] = []
    total_supported_keys = 0
    blacklist, whitelist = account_config_rules(task)

    for key, default_value in account_config_schema(task, task_override).items():
        forced = key in whitelist
        if key in blacklist:
            continue
        if str(key).startswith("_") and not forced:
            continue

        type_meta = task.config_type.get(key) if task.config_type else None
        account_config_type = getattr(task, "account_config_type", None)
        if isinstance(account_config_type, dict) and key in account_config_type:
            type_meta = account_config_type.get(key)
        if type_meta and type_meta.get("type") in {"global", "button"} and not forced:
            continue

        if not is_supported_value(default_value):
            continue

        total_supported_keys += 1

        base_value = account_config_base_value(task, key, default_value)
        override_value = task_override.get(key, base_value)
        value = coerce_like(base_value, override_value)

        if only_diff and value == base_value and not forced:
            continue

        defaults[key] = default_value
        initial[key] = copy.deepcopy(value)
        base_values[key] = copy.deepcopy(base_value)
        editable_keys.append(key)

    return initial, defaults, editable_keys, base_values, total_supported_keys
