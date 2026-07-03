from __future__ import annotations

import threading
import shutil
from pathlib import Path
from typing import Any

from ok import ConfigOption
from ok.util.config import Config
from ok.util.file import get_relative_path, read_json_file, write_json_file

from src.interaction.KeyConfig import DEFAULT_COMBAT_KEYS, DEFAULT_COMMON_KEYS, DEFAULT_INDUSTRY_KEYS
from src.tasks.BattleConfig import (
    BATTLE_CONFIG_DESCRIPTION,
    BATTLE_CONFIG_NAME,
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
)


KEY_CONFIG_NAME = "Game Hotkey Config"
ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME = "Ensure Main Once Action Sleep"

key_config_option = ConfigOption(
    KEY_CONFIG_NAME,
    {**DEFAULT_COMMON_KEYS, **DEFAULT_INDUSTRY_KEYS, **DEFAULT_COMBAT_KEYS},
    description="In Game Hotkey Config",
)
battle_config_option = ConfigOption(
    BATTLE_CONFIG_NAME,
    DEFAULT_BATTLE_CONFIG,
    description="Battle Config",
    config_description=BATTLE_CONFIG_DESCRIPTION,
    config_type=BATTLE_CONFIG_TYPE,
)
ensure_main_once_action_sleep_option = ConfigOption(
    ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME,
    {"SingleActionWithDelay": 1.5},
    description="Ensure Main Once Action Sleep",
)

GLOBAL_CONFIG_OPTIONS = [
    key_config_option,
    battle_config_option,
    ensure_main_once_action_sleep_option,
]

_LOCK = threading.Lock()
_CONFIGS: dict[str, Config] = {}
_OPTIONS = {option.name: option for option in GLOBAL_CONFIG_OPTIONS}
_MIGRATION_MARKER = "global_config_store_v2_task_scoped"
_MIGRATION_STATE_PATH = get_relative_path("configs", "_global_config_migrations.json")
_MIGRATION_BACKUP_DIR = get_relative_path("configs", "global_config_migration_backup")
_BATTLE_LEGACY_TASK_CONFIGS = ["DailyTask", "AutoCombatTask", "BattleTask"]


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


def _migrate_legacy_task_config(config: Config, option: ConfigOption) -> None:
    state = _read_migration_state()
    if option.name == BATTLE_CONFIG_NAME:
        _backup_legacy_task_configs(state)

    migrated_options = state.setdefault(_MIGRATION_MARKER, [])
    if not isinstance(migrated_options, list):
        migrated_options = []
        state[_MIGRATION_MARKER] = migrated_options
    if option.name in migrated_options:
        return

    for key, value in _collect_legacy_values(option).items():
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
