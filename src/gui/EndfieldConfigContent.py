"""Configuration visibility shared by entry buttons and native forms."""

from src.core.BattleConfig import BATTLE_CONFIG_MODE_KEY, DEFAULT_BATTLE_CONFIG
from src.image.enemy_health_probe import KEY_SAVE_ENEMY_PRESENCE_FRAMES


def visible_config_types(task, config, config_type):
    types = dict(config_type or {})
    if config is not None and BATTLE_CONFIG_MODE_KEY in config:
        for key in (*DEFAULT_BATTLE_CONFIG, BATTLE_CONFIG_MODE_KEY):
            types[key] = {**types.get(key, {}), "hidden": True}
    if task is not None and task.__class__.__name__ == "AutoCombatTask":
        types[KEY_SAVE_ENEMY_PRESENCE_FRAMES] = {"hidden": True}
    return types


def has_operation_row(default_config, task):
    """Same condition as the native ConfigContentMixin.add_buttons "Operation" row."""
    # 字段全部隐藏时，「重置配置」「添加开始菜单快捷方式」仍要能从配置抽屉进入
    return bool(default_config) or bool(getattr(task, "show_create_shortcut", False))


def has_task_configuration(task):
    """Match native root fields, including action-only fields and hidden metadata."""
    if has_operation_row(getattr(task, "default_config", None), task):
        return True
    config = getattr(task, "config", None)
    if not config:
        return False
    types = visible_config_types(task, config, getattr(task, "config_type", None))
    controlled = set()
    for metadata in types.values():
        if not isinstance(metadata, dict):
            continue
        for keys in metadata.get("sub_configs", {}).values():
            controlled.update([keys] if isinstance(keys, str) else (keys or []))
    for key in dict.fromkeys((*config, *types)):
        metadata = types.get(key, {})
        if key.startswith("_") or key in controlled:
            continue
        if isinstance(metadata, dict) and metadata.get("hidden", False):
            continue
        if key in config:
            return True
        if isinstance(metadata, dict) and (
            metadata.get("type") == "button"
            or ("type" not in metadata and ("buttons" in metadata or "callback" in metadata))
        ):
            return True
    return False
