"""配置键迁移：在 load_config 前将 JSON 文件中的旧 key 重命名为新 key。

每个任务或 mixin 定义自己的 config_key_migrations 类属性，
BaseEfTask.load_config 走 MRO 自动收集并调用本模块的函数。

用法
----
class MyMixin:
    config_key_migrations = {
        "旧key": "新key",
    }
"""

from ok.util.file import get_relative_path, read_json_file, write_json_file


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
