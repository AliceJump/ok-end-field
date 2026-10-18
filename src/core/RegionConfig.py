from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from ok import ConfigOption
from ok.util.config import Config
from ok.util.file import read_json_file, write_json_file

from src.core.paths import config_path
from src.data.world_map import areas_list
from src.icons import Icons

REGION_CONFIG_NAME = "Daily Region Config"
REGION_CONFIG_MODE_KEY = "使用独立地区配置"
REGION_SCOPE_MODE_KEY = "地区范围模式"
REGION_SCOPE_AREAS_KEY = "地区列表"

REGION_SCOPE_EXCLUDE = "排除所选地区"
REGION_SCOPE_INCLUDE = "仅执行所选地区"
REGION_SCOPE_MODES = [REGION_SCOPE_EXCLUDE, REGION_SCOPE_INCLUDE]

DEFAULT_REGION_CONFIG = {
    REGION_SCOPE_MODE_KEY: REGION_SCOPE_EXCLUDE,
    REGION_SCOPE_AREAS_KEY: [],
}

REGION_CONFIG_DESCRIPTION = {
    REGION_SCOPE_MODE_KEY: (
        "「排除所选地区」会执行当前及未来新增的全部地区，但跳过列表中的地区；「仅执行所选地区」只执行列表中的地区。"
    ),
    REGION_SCOPE_AREAS_KEY: "选择要排除或仅执行的地区；地区列表由当前主数据自动生成。",
}

REGION_CONFIG_TYPE = {
    REGION_SCOPE_MODE_KEY: {
        "type": "drop_down",
        "options": REGION_SCOPE_MODES,
    },
    REGION_SCOPE_AREAS_KEY: {
        "type": "multi_selection",
        "options": list(areas_list),
    },
}

region_config_option = ConfigOption(
    REGION_CONFIG_NAME,
    DEFAULT_REGION_CONFIG,
    description="全局地区执行范围",
    config_description=REGION_CONFIG_DESCRIPTION,
    config_type=REGION_CONFIG_TYPE,
    icon=Icons.Navigation,
)

_REGION_CONFIG: Config | None = None
_MIGRATION_BACKUP_VERSION = "v1"


def get_region_config() -> Config:
    """Return the persisted global region configuration."""
    global _REGION_CONFIG
    if _REGION_CONFIG is None:
        _REGION_CONFIG = Config(REGION_CONFIG_NAME, DEFAULT_REGION_CONFIG)
    return _REGION_CONFIG


def resolve_region_areas(
    mode: str,
    selected_areas: Iterable[str] | None,
    available_areas: Iterable[str] | None = None,
) -> list[str]:
    """Resolve a stable ordered region list from include/exclude semantics."""
    available = list(areas_list if available_areas is None else available_areas)
    selected = {str(area) for area in (selected_areas or [])}
    if mode == REGION_SCOPE_INCLUDE:
        return [area for area in available if area in selected]
    return [area for area in available if area not in selected]


def _backup_file_once(source_path: str, backup_name: str) -> None:
    source = Path(source_path)
    if not source.is_file():
        return
    backup_dir = Path(config_path("region_scope_migration_backup", _MIGRATION_BACKUP_VERSION))
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / backup_name
    if not target.exists():
        shutil.copy2(source, target)


def _migrate_region_segment(segment: dict, legacy_areas: Iterable[str]) -> bool:
    """Convert old per-area booleans into one independent region-scope policy."""
    legacy_areas = list(legacy_areas)
    present = [area for area in legacy_areas if area in segment and isinstance(segment.get(area), bool)]
    if not present:
        return False

    changed = False
    disabled = [area for area in present if segment.get(area) is False]
    has_new_scope = any(
        key in segment
        for key in (
            REGION_CONFIG_MODE_KEY,
            REGION_SCOPE_MODE_KEY,
            REGION_SCOPE_AREAS_KEY,
        )
    )
    if disabled and not has_new_scope:
        segment[REGION_CONFIG_MODE_KEY] = True
        segment[REGION_SCOPE_MODE_KEY] = REGION_SCOPE_EXCLUDE
        segment[REGION_SCOPE_AREAS_KEY] = disabled
        changed = True

    # Legacy booleans used to gate only the trade branch. After the selected
    # regions have been preserved above, normalize them so they cannot keep
    # silently blocking buy/sell after the new shared scope is edited.
    for area in present:
        if segment.get(area) is not True:
            segment[area] = True
            changed = True
    return changed


def migrate_legacy_region_scope(task_class_name: str, legacy_areas: Iterable[str] | None = None) -> None:
    """Migrate task JSON and account overrides before framework verification."""
    legacy_areas = list(areas_list if legacy_areas is None else legacy_areas)
    task_path = config_path(f"{task_class_name}.json")
    data = read_json_file(task_path)
    if isinstance(data, dict) and any(area in data for area in legacy_areas):
        _backup_file_once(task_path, f"{task_class_name}.json")
        if _migrate_region_segment(data, legacy_areas):
            write_json_file(task_path, data)

    from src.tasks.account.account_scope_store import get_store_path, update_overrides

    override_path = get_store_path()
    override_data = read_json_file(override_path)
    accounts = override_data.get("accounts", {}) if isinstance(override_data, dict) else {}
    has_legacy_override = any(
        isinstance(task_map, dict)
        and isinstance(task_map.get(task_class_name), dict)
        and any(area in task_map[task_class_name] for area in legacy_areas)
        for task_map in accounts.values()
    )
    if has_legacy_override:
        _backup_file_once(override_path, Path(override_path).name)

    def apply(store):
        accounts = store.get("accounts") or {}
        if not isinstance(accounts, dict):
            return store
        for task_map in accounts.values():
            if not isinstance(task_map, dict):
                continue
            segment = task_map.get(task_class_name)
            if isinstance(segment, dict):
                _migrate_region_segment(segment, legacy_areas)
        return store

    if has_legacy_override:
        update_overrides(apply)
