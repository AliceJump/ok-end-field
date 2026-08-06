from __future__ import annotations

import threading
import shutil
from pathlib import Path
from typing import Any

from ok import ConfigOption
from ok.util.config import Config
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.interaction.KeyConfig import DEFAULT_COMBAT_KEYS, DEFAULT_COMMON_KEYS, DEFAULT_INDUSTRY_KEYS
from src.core.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_NAME,
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
)
from src.data.delivery_area import DELIVERY_AREA_CONFIG
from src.data.world_map import STAGE_CATEGORY_ENERGY_POOLING, stages_dict


KEY_CONFIG_NAME = "Game Hotkey Config"
ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME = "Ensure Main Once Action Sleep"
ZIP_LINE_CONFIG_NAME = "Zip Line Config"
ZIP_LINE_SCROLL_KEY = "是否启用滚动放大视角"
ZIP_LINE_GROUP_KEY = "滑索配置分类"
ZIP_LINE_DELIVERY_GROUP = "送货滑索"
ZIP_LINE_GATHER_GROUP = "淤积点滑索"


def _zip_line_route_keys() -> list[str]:
    keys = []
    for area in DELIVERY_AREA_CONFIG.values():
        locations = area.get("delivery_locations", [])
        keys.extend(locations)
        keys.extend(f"通向{location}送货点" for location in locations)
        for targets in area.get("delivery_targets_by_location", {}).values():
            keys.extend(targets)
    keys.extend(stages_dict.get(STAGE_CATEGORY_ENERGY_POOLING, []))
    return list(dict.fromkeys(str(key) for key in keys if key))


ZIP_LINE_ROUTE_KEYS = _zip_line_route_keys()
ZIP_LINE_GATHER_KEYS = list(stages_dict.get(STAGE_CATEGORY_ENERGY_POOLING, []))
ZIP_LINE_DELIVERY_KEYS = [key for key in ZIP_LINE_ROUTE_KEYS if key not in ZIP_LINE_GATHER_KEYS]
ZIP_LINE_DEFAULT_CONFIG = {
    ZIP_LINE_SCROLL_KEY: False,
    **{key: "" for key in ZIP_LINE_ROUTE_KEYS},
    ZIP_LINE_GROUP_KEY: ZIP_LINE_DELIVERY_GROUP,
}
ZIP_LINE_CONFIG_DESCRIPTION = {
    ZIP_LINE_SCROLL_KEY: (
        "启用后在对齐滑索时会自动滚动放大视角\n"
        "可能会提高对齐成功率，但也可能导致对齐成功率下降较为明显\n"
        "建议启用此项时不要使用非白发或有白帽角色"
    ),
    ZIP_LINE_GROUP_KEY: "选择要显示的滑索配置分类。",
    **{key: "滑索距离序列，用逗号分隔。" for key in ZIP_LINE_ROUTE_KEYS},
}
ZIP_LINE_CONFIG_TYPE = {
    ZIP_LINE_GROUP_KEY: {
        "type": "drop_down",
        "options": [ZIP_LINE_DELIVERY_GROUP, ZIP_LINE_GATHER_GROUP],
        "sub_configs": {
            ZIP_LINE_DELIVERY_GROUP: [ZIP_LINE_SCROLL_KEY] + ZIP_LINE_DELIVERY_KEYS,
            ZIP_LINE_GATHER_GROUP: [ZIP_LINE_SCROLL_KEY] + ZIP_LINE_GATHER_KEYS,
        },
    },
}

key_config_option = ConfigOption(
    KEY_CONFIG_NAME,
    {**DEFAULT_COMMON_KEYS, **DEFAULT_INDUSTRY_KEYS, **DEFAULT_COMBAT_KEYS},
    description="游戏内快捷键配置",
)
battle_config_option = ConfigOption(
    BATTLE_CONFIG_NAME,
    DEFAULT_BATTLE_CONFIG,
    description="全局战斗配置",
    config_description=BATTLE_CONFIG_DESCRIPTION,
    config_type=BATTLE_CONFIG_TYPE,
)
ensure_main_once_action_sleep_option = ConfigOption(
    ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME,
    {"SingleActionWithDelay": 1.5},
    description="主界面单次动作后延迟",
)
zip_line_config_option = ConfigOption(
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_DEFAULT_CONFIG,
    description="滑索路线与距离序列配置",
    config_description=ZIP_LINE_CONFIG_DESCRIPTION,
    config_type=ZIP_LINE_CONFIG_TYPE,
)

GLOBAL_CONFIG_OPTIONS = [
    key_config_option,
    battle_config_option,
    ensure_main_once_action_sleep_option,
    zip_line_config_option,
]

_LOCK = threading.Lock()
_CONFIGS: dict[str, Config] = {}
_OPTIONS = {option.name: option for option in GLOBAL_CONFIG_OPTIONS}
_MIGRATION_MARKER = "global_config_store_v2_task_scoped"
_MIGRATION_STATE_PATH = get_relative_path("configs", "_global_config_migrations.json")
_MIGRATION_BACKUP_DIR = get_relative_path("configs", "global_config_migration_backup")
_BATTLE_LEGACY_TASK_CONFIGS = ["DailyTask", "AutoCombatTask", "BattleTask"]
_ZIP_LINE_LEGACY_TASK_CONFIGS = ["DeliveryTask", "DailyTask", "BattleTask"]
_ZIP_LINE_ACCOUNT_MIGRATION_MARKER = "zip_line_account_overrides_v1"
_ZIP_LINE_KEY_MIGRATIONS = {
    # 历史任务配置中的固定键名，保留明确映射以确保迁移稳定。
    "通向送货点": "通向武陵城送货点",
    "通向送货点试验园区": "通向试验园区送货点",
}


def _same_type(value: Any, default_value: Any) -> bool:
    return isinstance(value, type(default_value))


def _coerce_legacy_value(key: str, value: Any, default_value: Any) -> Any:
    if key == "技能释放" and isinstance(default_value, list) and isinstance(value, str):
        skills = [char for char in value if char.strip()]
        return skills or default_value
    return value


def _read_migration_state() -> dict[str, Any]:
    state = read_json_file(_MIGRATION_STATE_PATH)
    return state if isinstance(state, dict) else {}


def _write_migration_state(state: dict[str, Any]) -> None:
    write_json_file(_MIGRATION_STATE_PATH, state)


def _backup_legacy_task_configs(state: dict[str, Any]) -> None:
    backup_marker = f"{_MIGRATION_MARKER}_backup"
    if state.get(backup_marker):
        return

    backup_dir = Path(_MIGRATION_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for task_config_name in _BATTLE_LEGACY_TASK_CONFIGS:
        source_path = Path(get_relative_path("configs", f"{task_config_name}.json"))
        if source_path.is_file():
            shutil.copy2(source_path, backup_dir / source_path.name)

    state[backup_marker] = True
    _write_migration_state(state)


def _iter_legacy_config_data(option: ConfigOption):
    if option.name == BATTLE_CONFIG_NAME:
        task_config_names = _BATTLE_LEGACY_TASK_CONFIGS
    else:
        task_config_names = []

    for task_config_name in task_config_names:
        config_path = Path(get_relative_path("configs", f"{task_config_name}.json"))
        data = read_json_file(str(config_path))
        if isinstance(data, dict):
            mtime = config_path.stat().st_mtime if config_path.is_file() else -1
            yield data, mtime


def _iter_legacy_zip_line_task_data():
    """遍历 legacy 任务配置文件中的滑索键值（不含账号覆盖，避免账号路线污染全局迁移）。"""
    for task_config_name in _ZIP_LINE_LEGACY_TASK_CONFIGS:
        config_path = Path(get_relative_path("configs", f"{task_config_name}.json"))
        data = read_json_file(str(config_path))
        if isinstance(data, dict):
            yield data, config_path.stat().st_mtime if config_path.is_file() else -1


def _collect_legacy_values(option: ConfigOption) -> dict[str, Any]:
    candidates_by_key: dict[str, list[tuple[float, Any]]] = {}
    for data, mtime in _iter_legacy_config_data(option) or []:
        for key, default_value in option.default_config.items():
            if key not in data:
                continue
            value = _coerce_legacy_value(key, data.get(key), default_value)
            if _same_type(value, default_value) and value != default_value:
                candidates_by_key.setdefault(key, []).append((mtime, value))

    legacy_values = {}
    for key, candidates in candidates_by_key.items():
        legacy_values[key] = max(candidates, key=lambda item: item[0])[1]
    return legacy_values


def _collect_legacy_zip_line_values() -> dict[str, Any]:
    candidates_by_key: dict[str, list[tuple[float, Any]]] = {}
    for data, mtime in _iter_legacy_zip_line_task_data() or []:
        for raw_key, value in data.items():
            key = _ZIP_LINE_KEY_MIGRATIONS.get(raw_key, raw_key)
            default_value = ZIP_LINE_DEFAULT_CONFIG.get(key)
            if key not in ZIP_LINE_DEFAULT_CONFIG:
                continue
            if type(value) is type(default_value) and value != default_value:
                candidates_by_key.setdefault(key, []).append((mtime, value))
    return {
        key: max(candidates, key=lambda item: item[0])[1]
        for key, candidates in candidates_by_key.items()
    }


def _migrate_legacy_zip_line_account_overrides() -> None:
    """Copy per-task legacy routes into the shared per-account namespace."""
    from src.tasks.account.account_scope_store import update_overrides

    def apply(data):
        accounts = data.get("accounts") or {}
        if not isinstance(accounts, dict):
            return data
        for account_tasks in accounts.values():
            if not isinstance(account_tasks, dict):
                continue
            candidates: dict[str, list[Any]] = {}
            for task_name in _ZIP_LINE_LEGACY_TASK_CONFIGS:
                task_config = account_tasks.get(task_name, {})
                if not isinstance(task_config, dict):
                    continue
                for raw_key, value in task_config.items():
                    key = _ZIP_LINE_KEY_MIGRATIONS.get(raw_key, raw_key)
                    default_value = ZIP_LINE_DEFAULT_CONFIG.get(key)
                    if key == ZIP_LINE_GROUP_KEY or key not in ZIP_LINE_DEFAULT_CONFIG:
                        continue
                    if type(value) is type(default_value):
                        candidates.setdefault(key, []).append(value)
            migrated = {}
            for key, values in candidates.items():
                default_value = ZIP_LINE_DEFAULT_CONFIG[key]
                migrated[key] = next(
                    (value for value in values if value != default_value),
                    values[0],
                )
            if migrated:
                shared = account_tasks.setdefault(ZIP_LINE_CONFIG_NAME, {})
                if not isinstance(shared, dict):
                    shared = {}
                    account_tasks[ZIP_LINE_CONFIG_NAME] = shared
                for key, value in migrated.items():
                    shared.setdefault(key, value)
        return data

    update_overrides(apply)


def _migrate_legacy_task_config(config: Config, option: ConfigOption) -> None:
    state = _read_migration_state()
    if option.name == BATTLE_CONFIG_NAME:
        _backup_legacy_task_configs(state)

    migrated_options = state.setdefault(_MIGRATION_MARKER, [])
    if not isinstance(migrated_options, list):
        migrated_options = []
        state[_MIGRATION_MARKER] = migrated_options
    if option.name == ZIP_LINE_CONFIG_NAME and not state.get(_ZIP_LINE_ACCOUNT_MIGRATION_MARKER):
        _migrate_legacy_zip_line_account_overrides()
        state[_ZIP_LINE_ACCOUNT_MIGRATION_MARKER] = True
        _write_migration_state(state)
    if option.name in migrated_options:
        return

    values = (
        _collect_legacy_zip_line_values()
        if option.name == ZIP_LINE_CONFIG_NAME
        else _collect_legacy_values(option)
    )
    for key, value in values.items():
        if config.get(key) == option.default_config.get(key):
            config[key] = value

    migrated_options.append(option.name)
    _write_migration_state(state)


def get_global_config(name: str) -> Config:
    with _LOCK:
        option = _OPTIONS.get(name)
        if option is None:
            for config in _CONFIGS.values():
                if name in config:
                    return config
            raise RuntimeError(f"Can not find config {name}")

        config = _CONFIGS.get(option.name)
        if config is None:
            config = Config(option.name, option.default_config, validator=option.validator)
            _migrate_legacy_task_config(config, option)
            _CONFIGS[option.name] = config
        return config


def get_all_visible_configs():
    configs = []
    for option in GLOBAL_CONFIG_OPTIONS:
        if not option.name.startswith("_"):
            configs.append((option.name, get_global_config(option.name), option))
    return sorted(configs, key=lambda item: item[0])
