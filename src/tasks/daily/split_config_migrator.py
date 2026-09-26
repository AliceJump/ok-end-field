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
- 账号覆盖（``account_scoped_overrides.json``）同批迁移：来源任务段
  的参数键复制到目标类名段（旧键保留）。

键映射条目两种形态（dict 键 = 目标键名）：
- ``{"新键": "旧键"}``            —— 纯值复制（setdefault：目标已有该键则跳过）；
- ``{"新键": callable}``          —— 转换函数
  ``fn(source_config, target_config, target_key) -> value``，
  返回 ``_NO_MIGRATION`` 表示跳过；返回值与目标现值不同时写入（用于
  兼容旧布尔键等旧格式，或「目标值仍为默认时用来源值填充」）。

路径一律经 ``src.core.paths.config_path`` 惰性求值，不在模块级固化。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from src.core.BattleConfig import BATTLE_CONFIG_MODE_KEY, DEFAULT_BATTLE_CONFIG
from src.core.config_migration import _NO_MIGRATION
from src.core.paths import config_path
from src.data.world_map import areas_list
from src.tasks.onetime.ActivityRewardTask import ActivityRewardTask
from src.tasks.onetime.BoatHarvestTask import BoatHarvestTask

_STATE_FILE_NAME = "_daily_split_migrations.json"
_BACKUP_DIR_NAME = "daily_split_migration_backup"
# 账号覆盖存储文件名（与 account_scope_store.get_store_path 保持一致）
_ACCOUNT_OVERRIDE_STORE = "account_scoped_overrides"
# 类常量：收菜阶段 / 活动奖励选项（随类定义，导入类后取属性）
BOAT_STAGES = BoatHarvestTask.BOAT_STAGES
ACTIVITY_REWARDS = ActivityRewardTask.ACTIVITY_REWARDS

_LOCK = threading.Lock()
# 进程内一次性标记：状态文件读写有 IO 成本，本进程内只检查一次
_PROCESS_DONE = False


def _area_trade_key_map() -> dict[str, str]:
    """按地区生成买卖货键（{area} / {area}买入价 / {area}卖出价）的等名映射。"""
    key_map: dict[str, str] = {}
    for area in areas_list:
        key_map[area] = area
        key_map[f"{area}买入价"] = f"{area}买入价"
        key_map[f"{area}卖出价"] = f"{area}卖出价"
    return key_map


def _import_boat_stages(source_config: dict, target_config: dict, target_key: str):
    """⭐帝江号收菜：新列表值直接搬；旧「布尔开关 + 操作列表」转换为列表。"""
    if isinstance(target_config.get(target_key), list):
        return _NO_MIGRATION
    value = source_config.get(target_key)
    if isinstance(value, list):
        return value
    if value is True:
        ops = source_config.get("帝江号收菜操作")
        return list(ops) if isinstance(ops, list) else list(BOAT_STAGES)
    if value is False:
        return []
    return _NO_MIGRATION


def _import_activity_rewards(source_config: dict, target_config: dict, target_key: str):
    """⭐活动奖励：新列表值直接搬；旧「布尔开关 + 操作列表」转换为列表。"""
    if isinstance(target_config.get(target_key), list):
        return _NO_MIGRATION
    value = source_config.get(target_key)
    if isinstance(value, list):
        return value
    if value is True:
        ops = source_config.get("活动奖励")
        return list(ops) if isinstance(ops, list) else list(ACTIVITY_REWARDS)
    if value is False:
        return []
    return _NO_MIGRATION


def _import_region_options(source_config: dict, target_config: dict, target_key: str):
    """⭐地区建设：新列表值直接搬；旧三个布尔开关合并为选项列表。"""
    if isinstance(target_config.get(target_key), list):
        return _NO_MIGRATION
    value = source_config.get(target_key)
    if isinstance(value, list):
        return value
    option_keys = {"据点兑换": "⭐据点兑换", "买物资": "⭐买物资", "买卖货": "⭐买卖货"}
    if not any(key in source_config for key in option_keys.values()):
        return _NO_MIGRATION
    return [name for name, key in option_keys.items() if source_config.get(key)]


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
    "daily_split_v2": {
        "LiaisonGiftTask": {
            "DailyTask": {
                "一次送礼个数": "一次送礼个数",
                "送礼任务最多尝试次数": "送礼任务最多尝试次数",
                "优先送礼对象": "优先送礼对象",
            },
        },
        "BoatOrganizeTask": {
            "DailyTask": {
                "⭐帝江号一键存放": "⭐帝江号一键存放",
                "⭐简易制作": "⭐简易制作",
            },
        },
        "BoatHarvestTask": {
            "DailyTask": {
                "⭐帝江号收菜": _import_boat_stages,
            },
        },
        "RegionalBuildTask": {
            "DailyTask": {
                "⭐地区建设": _import_region_options,
                "交易货品优先序列": "交易货品优先序列",
                "据点兑换仅购买优先商品": "据点兑换仅购买优先商品",
                "只买不卖": "只买不卖",
                "购物白名单": "购物白名单",
                "是否买礼物": "是否买礼物",
                **_area_trade_key_map(),
            },
        },
        "CreditShopTask": {
            "DailyTask": {
                "信用商店保留信用": "信用商店保留信用",
            },
        },
        "ActivityRewardTask": {
            "DailyTask": {
                "⭐活动奖励": _import_activity_rewards,
            },
        },
    },
    # phase2 已启动过的用户，旧日常参数可能已被 verify_config 从 DailyTask.json
    # 清除；本批次读取 phase2 的预清理备份，且不覆盖日常专属任务已有的配置。
    "daily_split_v3": {
        "DailyBattleTask": {
            "DailyTask": {
                **{key: key for key in DEFAULT_BATTLE_CONFIG},
                BATTLE_CONFIG_MODE_KEY: BATTLE_CONFIG_MODE_KEY,
                "战斗配置": "战斗配置",
                **{
                    key: key
                    for key in (
                        "消耗限时体力药",
                        "体力本",
                        "体力本奖励档位",
                        "刷体力开始日期",
                        "刷本序列",
                        "仅站桩",
                        "体力刷完后继续刷取次数",
                        "指定的队伍编号",
                    )
                },
            },
        },
        "DailyDeliveryTask": {
            "DailyTask": {
                "目标券数": "目标券数",
                "地区切换": "地区切换",
            },
        },
        # 修复已完成 v2 批次时原样复制的旧布尔账号覆盖；有效列表保持不变。
        "BoatHarvestTask": {"DailyTask": {"⭐帝江号收菜": _import_boat_stages}},
        "RegionalBuildTask": {"DailyTask": {"⭐地区建设": _import_region_options}},
        "ActivityRewardTask": {"DailyTask": {"⭐活动奖励": _import_activity_rewards}},
    },
}


def _migration_state_path() -> str:
    return config_path(_STATE_FILE_NAME)


def _migration_backup_dir(batch_id: str) -> str:
    return config_path(_BACKUP_DIR_NAME, batch_id)


def _task_config_path(name: str) -> str:
    return config_path(f"{name}.json")


def _read_source_task_config(source_name: str, batch_id: str) -> dict[str, Any]:
    """读取当前来源配置；v3 缺失的旧日常键从此前批次备份补回。"""
    from ok.util.file import read_json_file

    source_config: dict[str, Any] = {}
    if source_name == "DailyTask" and batch_id == "daily_split_v3":
        for backup_batch in ("daily_split_pilot_v1", "daily_split_v2"):
            backup_file = Path(_migration_backup_dir(backup_batch)) / "DailyTask.json"
            backup = read_json_file(str(backup_file))
            if isinstance(backup, dict):
                source_config.update(backup)
    current = read_json_file(_task_config_path(source_name))
    if isinstance(current, dict):
        source_config.update(current)
    return source_config


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


def _resolve_entry_value(entry: Any, source_config: dict, target_config: dict, target_key: str) -> Any:
    """解析单条映射的来源值。返回 _NO_MIGRATION 表示本条跳过。"""
    if callable(entry):
        return entry(source_config, target_config, target_key)
    if entry not in source_config:
        return _NO_MIGRATION
    return source_config[entry]


def _import_task_config(target_name: str, key_map: dict[str, Any], source_config: dict) -> bool:
    """把来源任务配置中的键值按映射导入目标任务配置。

    str 条目 setdefault（目标已有则跳过）；callable 条目总是求值，
    返回值与目标现值不同且非 _NO_MIGRATION 时写入。

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
        if not callable(entry) and target_key in target_config:
            continue
        value = _resolve_entry_value(entry, source_config, target_config, target_key)
        if value is _NO_MIGRATION:
            continue
        if target_key in target_config and target_config[target_key] == value:
            continue
        target_config[target_key] = value
        modified = True

    if modified:
        write_json_file(target_file, target_config)
    return modified


def _backup_account_keys(account_id: str, registry: dict) -> list[str]:
    """仅用归属明确的账号名查询旧备份，账号 ID 始终保留。"""
    account_meta = registry.get(account_id, {})
    if not isinstance(account_meta, dict):
        return [account_id]

    used_by_others = set()
    for other_id, other_meta in registry.items():
        if other_id == account_id:
            continue
        used_by_others.add(other_id)
        if isinstance(other_meta, dict):
            other_username = other_meta.get("username")
            if isinstance(other_username, str):
                used_by_others.add(other_username)
            other_aliases = other_meta.get("aliases")
            if isinstance(other_aliases, list):
                used_by_others.update(alias for alias in other_aliases if isinstance(alias, str))

    aliases = account_meta.get("aliases")
    candidates = [*(aliases if isinstance(aliases, list) else []), account_meta.get("username")]
    keys = [account_id]
    for alias in candidates:
        if isinstance(alias, str) and alias and alias not in used_by_others and alias not in keys:
            keys.append(alias)
    return keys


def _read_source_account_segment(account_id: str, source_name: str, batch_id: str, data: dict) -> dict:
    """v3 从旧批次备份找回已从 DailyTask 账号段清理的参数。"""
    from ok.util.file import read_json_file

    source_segment: dict[str, Any] = {}
    if source_name == "DailyTask" and batch_id == "daily_split_v3":
        registry = data.get("account_registry") or {}
        backup_keys = _backup_account_keys(account_id, registry if isinstance(registry, dict) else {})
        for backup_batch in ("daily_split_pilot_v1", "daily_split_v2"):
            backup_file = Path(_migration_backup_dir(backup_batch)) / f"{_ACCOUNT_OVERRIDE_STORE}.json"
            backup = read_json_file(str(backup_file))
            backup_accounts = backup.get("accounts", {}) if isinstance(backup, dict) else {}
            if not isinstance(backup_accounts, dict):
                continue
            for key in backup_keys:
                task_map = backup_accounts.get(key, {})
                segment = task_map.get(source_name, {}) if isinstance(task_map, dict) else {}
                if isinstance(segment, dict):
                    source_segment.update(segment)
    current_task_map = (data.get("accounts") or {}).get(account_id, {})
    current_segment = current_task_map.get(source_name, {}) if isinstance(current_task_map, dict) else {}
    if isinstance(current_segment, dict):
        source_segment.update(current_segment)
    return source_segment


def _import_account_overrides(target_name: str, source_name: str, key_map: dict[str, Any], batch_id: str) -> None:
    """把账号覆盖存储中来源任务段的参数键复制到目标类名段（旧键保留）。

    callable 条目同任务配置文件一样执行转换；已迁移的有效目标值优先。
    """
    from src.tasks.account.account_scope_store import update_overrides

    def apply(data):
        accounts = data.setdefault("accounts", {})
        if not isinstance(accounts, dict):
            return data
        account_ids = list(accounts)
        if source_name == "DailyTask" and batch_id == "daily_split_v3":
            account_ids.extend(
                account_id
                for account_id in (data.get("account_registry") or {})
                if account_id not in accounts
            )
        for account_id in account_ids:
            account_tasks = accounts.setdefault(account_id, {})
            if not isinstance(account_tasks, dict):
                continue
            source_segment = _read_source_account_segment(account_id, source_name, batch_id, data)
            if not source_segment:
                continue
            target_segment = account_tasks.setdefault(target_name, {})
            if not isinstance(target_segment, dict):
                target_segment = {}
                account_tasks[target_name] = target_segment
            for target_key, entry in key_map.items():
                if not callable(entry) and target_key in target_segment:
                    continue
                value = _resolve_entry_value(entry, source_segment, target_segment, target_key)
                if value is _NO_MIGRATION or target_segment.get(target_key, _NO_MIGRATION) == value:
                    continue
                target_segment[target_key] = value
        return data

    update_overrides(apply)


def _run_batch(batch_id: str, batch: dict[str, dict[str, dict[str, Any]]], state: dict[str, Any]) -> None:
    _backup_source_configs(batch_id, batch, state)
    for target_name, source_map in batch.items():
        for source_name, key_map in source_map.items():
            source_config = _read_source_task_config(source_name, batch_id)
            _import_task_config(target_name, key_map, source_config)
            # 账号覆盖段迁移与任务配置迁移共用同一份键映射
            _import_account_overrides(target_name, source_name, key_map, batch_id)
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
