from __future__ import annotations

import threading
import shutil
from pathlib import Path
from typing import Any

from ok import ConfigOption
from ok.util.config import Config
from ok.util.file import get_relative_path, read_json_file, write_json_file
from qfluentwidgets import FluentIcon

from src.icons import Icons
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
    icon=Icons.Keyboard
)
battle_config_option = ConfigOption(
    BATTLE_CONFIG_NAME,
    DEFAULT_BATTLE_CONFIG,
    description="全局战斗配置",
    config_description=BATTLE_CONFIG_DESCRIPTION,
    config_type=BATTLE_CONFIG_TYPE,
    icon=Icons.Battle
)
ensure_main_once_action_sleep_option = ConfigOption(
    ENSURE_MAIN_ONCE_ACTION_SLEEP_NAME,
    {"SingleActionWithDelay": 1.5},
    description="主界面单次动作后延迟",
    icon=FluentIcon.DATE_TIME
)
zip_line_config_option = ConfigOption(
    ZIP_LINE_CONFIG_NAME,
    ZIP_LINE_DEFAULT_CONFIG,
    description="滑索路线与距离序列配置",
    config_description=ZIP_LINE_CONFIG_DESCRIPTION,
    config_type=ZIP_LINE_CONFIG_TYPE,
    icon=Icons.Zipline
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
_BATTLE_KEY_MIGRATIONS = {
    # 历史全局战斗配置键名：后台结束战斗通知 → 完成通知（不限前后台）。
    "后台结束战斗通知": "完成通知",
}


def _same_type(value: Any, default_value: Any) -> bool:
    return isinstance(value, type(default_value))


def _coerce_legacy_value(key: str, value: Any, default_value: Any) -> Any:
    if key == "技能释放" and isinstance(default_value, list) and isinstance(value, str):
        # 旧格式为无逗号连续字符（如 "31" → ["3", "1"]）。
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


def _migrate_key_names_in_file(
    option_name: str, migrations: dict[str, str], defaults: dict[str, Any]
) -> None:
    """文件级键名迁移：旧键值复制到新键，旧键保留（回滚安全）。

    - 旧键存在且值非默认，新键缺失或为默认值 → 用旧值填充新键。
      这样即使文件里新键已被框架补全成空默认值，旧值也能搬入。
    - 反向（新键 → 旧键）：旧键缺失时补一份，保证回滚安全。

    使用本模块的 get_relative_path，避免跨模块补丁遗漏读写真实 configs 目录。
    """
    if not migrations:
        return

    config_file = get_relative_path("configs", f"{option_name}.json")
    config = read_json_file(config_file)
    if not isinstance(config, dict):
        return

    reverse = {v: k for k, v in migrations.items()}
    modified = False
    for json_key in list(config.keys()):
        if json_key in migrations:
            new_key = migrations[json_key]
            old_value = config[json_key]
            new_value = config.get(new_key)
            default_value = defaults.get(new_key)
            if old_value != default_value and (
                new_key not in config or new_value == default_value
            ):
                config[new_key] = old_value
                modified = True
        elif json_key in reverse:
            old_key = reverse[json_key]
            if old_key not in config:
                config[old_key] = config[json_key]
                modified = True

    if modified:
        write_json_file(config_file, config)


def _migrate_legacy_config_file(option: ConfigOption) -> None:
    """在框架 Config 构造之前，对配置文件做文件级迁移。

    先做键名复制（旧键值 → 新键，旧键保留），再从 legacy 任务配置文件
    收集旧值写入新键，最后写回文件。这样框架 Config 构造（verify_config
    会删除文件中不在 default 的键并写回）读到的是已迁移好的文件，
    避免「先删后迁移」导致旧值丢失。
    """
    state = _read_migration_state()
    if option.name == BATTLE_CONFIG_NAME:
        _backup_legacy_task_configs(state)

    # Battle Config 键名迁移：幂等执行（旧键值复制到新键，旧键保留），
    # 放在迁移状态标记检查之前，保证已迁移过的安装也能完成本次键名重命名。
    if option.name == BATTLE_CONFIG_NAME:
        _migrate_key_names_in_file(
            option.name, _BATTLE_KEY_MIGRATIONS, DEFAULT_BATTLE_CONFIG
        )

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

    # 1. 键名迁移：把配置文件中不在 default 的旧键值复制到新键（旧键保留，回滚安全）
    if option.name == ZIP_LINE_CONFIG_NAME:
        _migrate_key_names_in_file(
            option.name, _ZIP_LINE_KEY_MIGRATIONS, ZIP_LINE_DEFAULT_CONFIG
        )

    # 2. 读取当前配置文件（键名复制后）
    config_file = get_relative_path("configs", f"{option.name}.json")
    config = read_json_file(config_file)
    if not isinstance(config, dict):
        config = {}

    # 3. 从 legacy 任务配置文件收集旧值写入新键（仅当前值缺失或等于默认值时覆盖）
    values = (
        _collect_legacy_zip_line_values()
        if option.name == ZIP_LINE_CONFIG_NAME
        else _collect_legacy_values(option)
    )
    modified = False
    for key, value in values.items():
        default_value = option.default_config.get(key)
        if key not in config or config.get(key) == default_value:
            config[key] = value
            modified = True
    if modified:
        write_json_file(config_file, config)

    migrated_options.append(option.name)
    _write_migration_state(state)


def migrate_task_zip_line_values_to_global(task_class_name: str) -> None:
    """任务侧 load_config 时调用：在框架 verify_config 删除任务文件滑索键之前，
    把任务文件中的滑索键值转存到全局 Zip Line Config.json。

    背景：任务 default_config 不含滑索键，框架 Config 构造（verify_config）会
    丢弃并写回删除任务文件中不在 default 的键。若先让任务加载，全局侧
    _collect_legacy_zip_line_values 就再也读不到这些滑索值。

    本函数在 super().load_config()（触发框架删键）之前执行，把滑索值写入
    全局（setdefault 语义：全局已有非默认值则不覆盖）。不依赖迁移标记，
    因此迁移标记已打的历史场景也能兜底继承。
    """
    config_file = get_relative_path("configs", f"{task_class_name}.json")
    data = read_json_file(config_file)
    if not isinstance(data, dict):
        return

    candidates = {}
    for raw_key, value in data.items():
        key = _ZIP_LINE_KEY_MIGRATIONS.get(raw_key, raw_key)
        default_value = ZIP_LINE_DEFAULT_CONFIG.get(key)
        if key not in ZIP_LINE_DEFAULT_CONFIG:
            continue
        if type(value) is type(default_value) and value != default_value:
            candidates[key] = value
    if not candidates:
        return

    zlc = get_global_config(ZIP_LINE_CONFIG_NAME)
    for key, value in candidates.items():
        default_value = ZIP_LINE_DEFAULT_CONFIG.get(key)
        if key not in zlc or zlc.get(key) == default_value:
            zlc[key] = value


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
            # 先做文件级迁移，再让框架 Config 构造（verify_config 会删掉不在 default 的键）。
            # 这样旧键值在框架删除前就已复制到新键，避免「先删后迁移」导致旧值丢失。
            _migrate_legacy_config_file(option)
            config = Config(option.name, option.default_config, validator=option.validator)
            _CONFIGS[option.name] = config
        return config


def get_all_visible_configs():
    configs = []
    for option in GLOBAL_CONFIG_OPTIONS:
        if not option.name.startswith("_"):
            configs.append((option.name, get_global_config(option.name), option))
    return sorted(configs, key=lambda item: item[0])
