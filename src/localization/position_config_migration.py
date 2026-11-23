"""Pre-load compatibility for retiring WS positioning without losing settings."""

import logging
import os
import shutil
import tempfile
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


def _atomic_backup(source, backup):
    backup.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{backup.name}.",
            suffix=".tmp",
            dir=backup.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        shutil.copy2(source, temporary)
        os.replace(temporary, backup)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def migrate_visual_map_root():
    """Move known default layouts to the packaged navigation data root."""
    from src.core.PositionConfig import CONFIG_VISUAL_MAP_ROOT, DEFAULT_VISUAL_MAP_ROOT
    from src.localization.calibrated_map_store import CalibratedMapStore

    source = Path(config_path("Position Config.json"))
    if not source.is_file():
        return False
    values = read_json_file(str(source))
    if not isinstance(values, dict):
        return False
    current = values.get(CONFIG_VISUAL_MAP_ROOT)
    if not isinstance(current, str):
        return False
    normalized = current.strip().replace("\\", "/").rstrip("/")
    if normalized == "assets/nav-grid-data/localization/maps":
        pass
    elif normalized == "assets/localization/maps":
        try:
            # Keep the legacy path only when the runtime could load it; the runtime
            # requires the prepared package, so a manifest alone is not enough.
            # available_maps() only reads the manifest; can_load() also requires
            # the prepared package, matching how the runtime loads maps.
            store = CalibratedMapStore(current.strip())
            if store.has_valid_base_metadata() and (
                not store.require_prepared or store.has_prepared(propagate_os=True)
            ):
                return False
        except (FileNotFoundError, NotADirectoryError, ValueError):
            pass
        except OSError:
            logging.getLogger(__name__).warning(
                "Cannot inspect visual resources; retaining the configured directory", exc_info=True
            )
            return False
    else:
        return False
    backup = Path(config_path("position_source_migration_backup", "v3", source.name))
    if not backup.exists():
        try:
            _atomic_backup(source, backup)
        except OSError:
            logging.getLogger(__name__).warning(
                "Cannot back up Position Config before visual map migration; keeping the original value",
                exc_info=True,
            )
            return False
    values[CONFIG_VISUAL_MAP_ROOT] = DEFAULT_VISUAL_MAP_ROOT
    write_json_file(str(source), values)
    return True


def migrate_grid_navigation_root():
    """Move the retired nav-grid-data root default without touching custom paths."""
    from src.core.GridNavConfig import CONFIG_GRID_DIR, DEFAULT_GRID_NAV_CONFIG

    source = Path(config_path("Nav Config.json"))
    if not source.is_file():
        return False
    values = read_json_file(str(source))
    if not isinstance(values, dict):
        return False
    current = values.get(CONFIG_GRID_DIR)
    if not isinstance(current, str) or current.strip().replace("\\", "/").rstrip("/") != "assets/nav-grid-data":
        return False
    backup = Path(config_path("nav_data_layout_migration", "v1", source.name))
    if not backup.exists():
        try:
            _atomic_backup(source, backup)
        except OSError:
            # A failed backup must not break config loading; keep the original value.
            logging.getLogger(__name__).warning(
                "Cannot back up Nav Config before grid root migration; keeping the original value", exc_info=True
            )
            return False
    values[CONFIG_GRID_DIR] = DEFAULT_GRID_NAV_CONFIG[CONFIG_GRID_DIR]
    write_json_file(str(source), values)
    return True
