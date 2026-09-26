"""日常子任务拆分的跨文件配置导入。

背景：
日常子任务拆成独立任务类后，参数键从 ``configs/DailyTask.json`` 迁到
``configs/<子任务类名>.json``。框架 verify_config 会删除任务文件中不在
default_config 的键，而 DailyTask 在注册列表第一位、最先加载——导入必须
发生在任何任务 load_config 之前，否则参数键先被删、迁移读到空值、用户
数据丢失。

机制（仿 global_config_store.py 的迁移模式）：
- ``DAILY_SPLIT_IMPORTS`` 按「批次 id → {目标类名: {来源类名: 键映射}}」声明；
- ``BaseEfTask.load_config`` 最前调用 ``maybe_run_pending_config_imports()``，
  一次性执行全部未完成批次（setdefault 语义：目标已有该键则不覆盖）；
- 完成状态记录在 ``_daily_split_migrations.json``，按批次累积；
- 首次执行某批次前，把涉及的来源配置文件与账号覆盖存储备份到
  ``daily_split_migration_backup/<批次>/``（回滚安全）；
- 账号覆盖（``account_scoped_overrides.json``）同批迁移：来源任务段的
  参数键复制到目标类名段（旧键保留）。

键映射条目两种形态（dict 键 = 目标键名）：
- ``{"新键": "旧键"}``            —— 纯值复制；
- ``{"新键": callable}``          —— 转换函数 ``fn(source_config) -> value``，
  返回 ``_NO_MIGRATION`` 表示跳过（用于兼容旧布尔键等旧格式）。

路径一律经 ``src.core.paths.config_path`` 惰性求值，不在模块级固化。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from src.core.config_migration import _NO_MIGRATION
from src.core.paths import config_path

_LOCK = threading.Lock()
# 进程内一次性标记：状态文件读写有 IO 成本，本进程内只检查一次
_PROCESS_DONE = False

# 来源任务名：拆分前所有日常参数都存放在 DailyTask 的配置与账号覆盖段下
_SOURCE_TASK_NAME = "DailyTask"
_STATE_FILE_NAME = "_daily_split_migrations.json"
_BACKUP_DIR_NAME = "daily_split_migration_backup"
# 账号覆盖存储文件名（与 account_scope_store.get_store_path 保持一致）
_ACCOUNT_OVERRIDE_STORE = "account_scoped_overrides"

# 批次声明。键值映射的 dict 键 = 目标键名；值为来源键名（str）或转换函数（callable）。
# 新批次往后追加即可，已完成批次受状态文件标记保护不会重复执行。
DAILY_SPLIT_IMPORTS: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
    "daily_split_pilot_v1": {
        "CreditCollectTask": {
            "DailyTask": {
                "尝试仅收培育室": "尝试仅收培育室",
            },
        },
    },
}


def _migration_state_path() -> str:
    return config_path(_STATE_FILE_NAME)


def _migration_backup_dir(batch_id: str) -> str:
    return config_path(_BACKUP_DIR_NAME, batch_id)


def _task_config_path(name: str) -> str:
    return config_path(f"{name}.json")


def _read_state() -> dict[str, Any]:
    from ok.util.file import read_json_file

    state = read_json_file(_migration_state_path())
    return state if isinstance(state, dict) else {}


def _write_state(state: dict[str, Any]) -> None:
    from ok.util.file import write_json_file

    write_json_file(_migration_state_path(), state)


def _batch_source_files(batch: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """收集批次涉及的全部来源任务配置文件名（含账号覆盖存储，用于备份）。"""
    names = [_ACCOUNT_OVERRIDE_STORE]
    for source_map in batch.values():
        for source_name in source_map:
            if source_name not in names:
                names.append(source_name)
    return names


def _backup_source_configs(batch_id: str, batch: dict[str, dict[str, dict[str, Any]]], state: dict[str, Any]) -> None:
    backup_marker = f"{batch_id}_backup"
    if state.get(backup_marker):
        return

    backup_dir = Path(_migration_backup_dir(batch_id))
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in _batch_source_files(batch):
        source_path = Path(_task_config_path(name))
        if source_path.is_file():
            shutil.copy2(source_path, backup_dir / source_path.name)

    state[backup_marker] = True


def _resolve_entry_value(entry: Any, source_config: dict) -> Any:
    """解析单条映射的来源值。返回 _NO_MIGRATION 表示本条跳过。"""
    if callable(entry):
        return entry(source_config)
    if entry not in source_config:
        return _NO_MIGRATION
    return source_config[entry]


def _import_task_config(target_name: str, key_map: dict[str, Any], source_config: dict) -> bool:
    """把来源任务配置中的键值按映射导入目标任务配置（setdefault 语义）。

    Returns:
        目标文件是否有改动。
    """
    from ok.util.file import read_json_file, write_json_file

    target_file = _task_config_path(target_name)
    target_config = read_json_file(target_file)
    if not isinstance(target_config, dict):
        target_config = {}

    modified = False
    for target_key, entry in key_map.items():
        if target_key in target_config:
            continue
        value = _resolve_entry_value(entry, source_config)
        if value is _NO_MIGRATION:
            continue
        target_config[target_key] = value
        modified = True

    if modified:
        write_json_file(target_file, target_config)
    return modified


def _import_account_overrides(target_name: str, key_map: dict[str, Any]) -> None:
    """把账号覆盖存储中来源任务段的参数键复制到目标类名段（旧键保留）。

    callable 条目面向来源任务配置文件做值转换，覆盖段里按
    「目标键 = 来源段同名键」处理（覆盖值与任务配置同形，直接复制即可）。
    """
    from src.tasks.account.account_scope_store import update_overrides

    def apply(data):
        accounts = data.get("accounts") or {}
        if not isinstance(accounts, dict):
            return data
        for account_tasks in accounts.values():
            if not isinstance(account_tasks, dict):
                continue
            source_segment = account_tasks.get(_SOURCE_TASK_NAME, {})
            if not isinstance(source_segment, dict):
                continue
            target_segment = account_tasks.setdefault(target_name, {})
            if not isinstance(target_segment, dict):
                target_segment = {}
                account_tasks[target_name] = target_segment
            for target_key, entry in key_map.items():
                if target_key in target_segment:
                    continue
                source_key = entry if isinstance(entry, str) else target_key
                if source_key not in source_segment:
                    continue
                target_segment[target_key] = source_segment[source_key]
        return data

    update_overrides(apply)


def _run_batch(batch_id: str, batch: dict[str, dict[str, dict[str, Any]]], state: dict[str, Any]) -> None:
    from ok.util.file import read_json_file

    _backup_source_configs(batch_id, batch, state)
    for target_name, source_map in batch.items():
        for source_name, key_map in source_map.items():
            source_config = read_json_file(_task_config_path(source_name))
            if not isinstance(source_config, dict):
                source_config = {}
            _import_task_config(target_name, key_map, source_config)
        # 账号覆盖段迁移与任务配置迁移共用同一份键映射
        for _source_name, key_map in source_map.items():
            _import_account_overrides(target_name, key_map)
    state.setdefault("completed_batches", []).append(batch_id)
    _write_state(state)


def run_pending_config_imports(table: dict | None = None) -> list[str]:
    """执行声明表中全部未完成批次，返回本次完成的批次 id 列表。幂等可重入。

    Args:
        table: 批次声明表，默认 ``DAILY_SPLIT_IMPORTS``。测试可传入临时表。
    """
    global _PROCESS_DONE
    with _LOCK:
        table = DAILY_SPLIT_IMPORTS if table is None else table
        if not table:
            _PROCESS_DONE = True
            return []

        state = _read_state()
        completed = state.get("completed_batches")
        if not isinstance(completed, list):
            completed = []
            state["completed_batches"] = completed

        executed = []
        for batch_id, batch in table.items():
            if batch_id in completed:
                continue
            _run_batch(batch_id, batch, state)
            executed.append(batch_id)

        _PROCESS_DONE = True
        return executed


def maybe_run_pending_config_imports() -> None:
    """BaseEfTask.load_config 入口：进程内首次调用时执行全部未完成批次。

    必须在任何任务文件的框架 verify_config 之前执行，时序由调用点保证。
    声明表为空或批次全部完成时，仅做一次进程内标记检查，无 IO。
    """
    if _PROCESS_DONE:
        return
    run_pending_config_imports()
