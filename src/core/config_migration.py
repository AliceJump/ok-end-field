"""配置键迁移：在 load_config 前将 JSON 文件中的旧 key 重命名为新 key。

用法
----
往 CONFIG_KEY_MIGRATIONS 加一条映射即可：
    CONFIG_KEY_MIGRATIONS = {
        "旧key": "新key",
    }

然后调用 migrate_config_file_keys(task_class_name) 即可。
"""

from ok.util.file import get_relative_path, read_json_file, write_json_file

# ── 配置键迁移表 ─────────────────────────────────────────────
# 格式: { "旧key": "新key" }
CONFIG_KEY_MIGRATIONS: dict[str, str] = {
    "⭐执行结尾外部命令": "⭐执行外部命令",
    "结尾外部命令": "外部命令",
    "结尾外部命令起始于": "外部命令起始于",
    "结尾外部命令等待退出": "外部命令等待退出",
    "结尾外部命令已运行时跳过": "外部命令已运行时跳过",
    "结尾外部命令执行时机": "外部命令执行时机",
}


def migrate_config_file_keys(task_class_name: str) -> None:
    """读取任务对应的 JSON 配置文件，按 CONFIG_KEY_MIGRATIONS 重命名 key。

    只在 JSON 文件层面操作，不影响已在内存中的 Config 对象。
    若文件不存在或不是合法 dict，静默跳过。
    """
    config_file = get_relative_path("configs", f"{task_class_name}.json")
    config = read_json_file(config_file)
    if not isinstance(config, dict):
        return
    modified = False
    for old_key, new_key in CONFIG_KEY_MIGRATIONS.items():
        if old_key in config:
            if new_key not in config:
                config[new_key] = config[old_key]
            del config[old_key]
            modified = True
    if modified:
        write_json_file(config_file, config)
