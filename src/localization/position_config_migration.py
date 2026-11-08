"""Pre-load compatibility for retiring WS positioning without losing settings."""

import shutil
from pathlib import Path

from ok.util.file import read_json_file, write_json_file

from src.core.config_migration import _NO_MIGRATION
from src.core.paths import config_path


def migrate_item_position_value(config, key):
    if key not in config or config[key] == "统一定位":
        return _NO_MIGRATION
    return "统一定位"


def backup_retired_position_settings():
    """Keep one rollback copy before Config.verify_config removes old WS keys."""
    source = Path(config_path("Position Config.json"))
    if not source.is_file():
        return
    values = read_json_file(str(source))
    if not isinstance(values, dict) or not any(
        k in values for k in ("WS等待稳定秒数", "WS稳定最小位置数", "航点校准最小距离(米)")
    ):
        return
    backup = Path(config_path("position_source_migration_backup", "v1", source.name))
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)


def migrate_item_position_overrides(data):
    for tasks in (data.get("accounts") or {}).values():
        if not isinstance(tasks, dict):
            continue
        values = tasks.get("ItemNavigatorTask")
        if isinstance(values, dict) and "定位实现" in values:
            values["定位实现"] = "统一定位"
    return data


def migrate_visual_map_root():
    """Move a missing legacy default to packaged maps; retain user resources."""
    from src.core.PositionConfig import CONFIG_VISUAL_MAP_ROOT, DEFAULT_VISUAL_MAP_ROOT
    from src.localization.calibrated_map_store import CalibratedMapStore

    source = Path(config_path("Position Config.json"))
    if not source.is_file():
        return False
    values = read_json_file(str(source))
    if not isinstance(values, dict):
        return False
    current = values.get(CONFIG_VISUAL_MAP_ROOT)
    if not isinstance(current, str) or current.strip().replace("\\", "/").rstrip("/") != "assets/localization/maps":
        return False
    if CalibratedMapStore(current.strip()).available_maps():
        return False
    backup = Path(config_path("position_source_migration_backup", "v2", source.name))
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
    values[CONFIG_VISUAL_MAP_ROOT] = DEFAULT_VISUAL_MAP_ROOT
    write_json_file(str(source), values)
    return True
