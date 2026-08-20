"""配置键迁移：在 load_config 前将 JSON 文件中的旧 key 重命名/转换为新 key。

每个任务或 mixin 定义自己的迁移类属性，BaseEfTask.load_config 走 MRO 自动收集并调用：

- ``config_key_migrations = {旧key: 新key}``：纯键名复制（值原样同步）。
- ``config_value_migrations = {新key: 转换函数}``：值转换迁移。
  转换函数由本模块的辅助函数（如 merge_bool_options / legacy_bool_switch_to_list）
  生成，签名 ``def transform(config: dict, new_key: str) -> object``，
  返回 _NO_MIGRATION 表示无需迁移。

用法
----
class MyMixin:
    config_key_migrations = {
        "旧key": "新key",
    }
    config_value_migrations = {
        "新key": merge_bool_options({"选项A": "旧布尔键A"}),
    }
"""

from ok.util.file import get_relative_path, read_json_file, write_json_file


# 哨兵：转换函数返回它表示「无需迁移 / 不生成新键」。
_NO_MIGRATION = object()


def migrate_config_file_keys(task_class_name: str, migrations: dict[str, str]) -> None:
    """扫描 JSON 中的每个 key，到 migrations 中双向查找并同步。

    对 JSON 中的每个 key：
      - 匹配到 migrations 的 key（旧 key） → 同步值到对应的 value（新 key）
      - 匹配到 migrations 的 value（新 key） → 同步值到对应的 key（旧 key）
    两边都不删除，JSON 中始终保留新旧两套 key，确保回滚安全。
    若文件不存在或不是合法 dict，静默跳过。
    """
    if not migrations:
        return

    config_file = get_relative_path("configs", f"{task_class_name}.json")
    config = read_json_file(config_file)
    if not isinstance(config, dict):
        return

    # 构建反向查找表：新key → 旧key
    reverse = {v: k for k, v in migrations.items()}

    modified = False
    for json_key in list(config.keys()):
        if json_key in migrations:
            new_key = migrations[json_key]
            if new_key not in config:
                config[new_key] = config[json_key]
                modified = True
        elif json_key in reverse:
            old_key = reverse[json_key]
            if old_key not in config:
                config[old_key] = config[json_key]
                modified = True

    if modified:
        write_json_file(config_file, config)


# ── 值转换迁移 ────────────────────────────────────────────


def apply_value_migrations(config: dict, value_migrations: dict) -> tuple[dict, bool]:
    """对配置字典应用值转换迁移。

    Args:
        config: 配置字典（就地修改）。
        value_migrations: {新key: 转换函数}，转换函数签名
            ``def transform(config, new_key) -> object``，
            返回 _NO_MIGRATION 表示无需迁移。

    Returns:
        (config, modified): 修改后的配置与是否有改动。
    """
    modified = False
    for new_key, transform in value_migrations.items():
        value = transform(config, new_key)
        if value is not _NO_MIGRATION:
            config[new_key] = value
            modified = True
    return config, modified


def migrate_config_values(task_class_name: str, value_migrations: dict) -> None:
    """扫描 JSON 并对指定新键做值转换迁移。

    转换函数自行决定触发条件（新键缺失、旧格式值等）与返回值；
    返回 _NO_MIGRATION 时保持 JSON 不变。若文件不存在或不是合法 dict，静默跳过。
    """
    if not value_migrations:
        return

    config_file = get_relative_path("configs", f"{task_class_name}.json")
    config = read_json_file(config_file)
    if not isinstance(config, dict):
        return

    config, modified = apply_value_migrations(config, value_migrations)
    if modified:
        write_json_file(config_file, config)


# ── 通用转换辅助函数 ──────────────────────────────────────


def merge_bool_options(option_keys: dict):
    """把多个旧布尔开关键合并为一个新多选列表键。

    Args:
        option_keys: {选项名: 旧配置键}，值为 True 的选项进入生成的列表。

    Returns:
        供 config_value_migrations 使用的转换函数：
          新键已存在，或不存在任何旧键 → _NO_MIGRATION（不生成）；
          否则返回按旧布尔值筛选出的选项列表（全部关闭时为空列表）。
    """

    def transform(config, new_key):
        if new_key in config:
            return _NO_MIGRATION
        present = [name for name, key in option_keys.items() if key in config]
        if not present:
            return _NO_MIGRATION
        return [name for name, key in option_keys.items() if config.get(key)]

    return transform


def legacy_battle_mode_to_bool(config, new_key):
    """把旧「战斗配置」下拉框值迁移为「使用独立配置」布尔开关。

    旧值 "使用独立配置" → True；"使用全局配置" → False。
    新键已是 bool → _NO_MIGRATION（无需迁移）。
    """
    if isinstance(config.get(new_key), bool):
        return _NO_MIGRATION
    old_value = config.get("战斗配置")
    if old_value == "使用独立配置":
        return True
    if old_value == "使用全局配置":
        return False
    return _NO_MIGRATION


def legacy_bool_switch_to_list(*, ops_key: str, defaults: list):
    """把旧「布尔开关 + 操作列表」合并为新多选列表键。

    Args:
        ops_key: 旧的操作列表配置键（如 ``帝江号收菜操作``）。
        defaults: 开关为 True 但缺少操作列表时的默认选项。

    Returns:
        供 config_value_migrations 使用的转换函数：
          新键已是列表 → _NO_MIGRATION；
          新键缺失或非 bool → _NO_MIGRATION（沿用默认配置）；
          新键为 bool → True 用 ops_key 列表（缺失回退 defaults），False 用空列表。
    """

    def transform(config, new_key):
        switch = config.get(new_key)
        if isinstance(switch, list):
            return _NO_MIGRATION
        if not isinstance(switch, bool):
            return _NO_MIGRATION
        if not switch:
            return []
        ops = config.get(ops_key)
        return list(ops) if isinstance(ops, list) else list(defaults)

    return transform
