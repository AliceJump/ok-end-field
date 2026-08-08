# -*- coding: utf-8 -*-
"""启动时按当前 onetime_tasks 顺序校正 ok-ef 计划任务的 -t 索引。

背景
----
ok-script 的 Windows 计划任务（ScheduleTaskTab）创建时把 -t 参数写成
onetime_tasks 的 1-based 索引（如 ``main.py -t 15 -e``），运行时按索引取
``onetime_tasks[N-1]``。一旦 onetime_tasks 排序变化（新增任务、按业务/测试
分组重排等），已创建的计划任务仍指向旧索引，会运行到错误任务，且计划任务
模块不会自动更新（configs/schedule_tasks_cache.json 与 Windows 计划任务里
保存的都是创建时刻的 -t N）。

方案（保留 ok 原生索引机制）
------------------------------
不做任务名解析。而是在应用启动时（MainWindow 首次显示，onetime_tasks 已就绪）
读取 configs/schedule_tasks_cache.json 中本应用（\\ok-ef\\）的计划任务：
1. 用缓存里的 name（任务名，不随排序变化）在当前 onetime_tasks 中查新索引；
2. 若 actions 里的 ``-t X`` 与新索引不一致（X 为旧索引或历史迁移的任务名），
   改写为 ``-t 新索引``；
3. 同步更新 xml_config、task_index、Windows 计划任务（COM），并修正当前进程
   sys.argv 中的 -t（保证本次计划任务触发也能正确运行）；
4. 写回缓存。仅在有变更时写文件与调用 COM。

这样计划任务始终是 ok 原生 ``-t N``，排序变化后下次启动自动校正，对排序免疫。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ok import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False
_SYNCED = False

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_FILE = ROOT / "configs" / "schedule_tasks_cache.json"
APP_ROOT = r"\ok-ef"  # 本应用 gui_title=ok-ef 对应的计划任务根路径


def _onetime_tasks():
    """返回当前 og.executor 的 onetime_tasks（可能为空列表）。"""
    try:
        from ok import og
    except Exception:
        return []
    executor = getattr(og, "executor", None)
    tasks = getattr(executor, "onetime_tasks", None)
    return tasks or []


def _find_index_by_name(name: str):
    """在 onetime_tasks 中按任务名/类名查找，返回 1-based 索引；找不到返回 None。"""
    tasks = _onetime_tasks()
    for i, task in enumerate(tasks, start=1):
        if getattr(task, "name", None) == name:
            return i
    for i, task in enumerate(tasks, start=1):
        if task.__class__.__name__ == name:
            return i
    return None


def _find_task_by_index(index: int):
    """按 1-based 索引取 onetime_tasks 中的任务实例；越界返回 None。"""
    tasks = _onetime_tasks()
    if 1 <= index <= len(tasks):
        return tasks[index - 1]
    return None


# -t 匹配：前面不能是字母/数字/下划线（避免误匹配 my-test 之类），
# 对 actions（-t 前为空格/开头）与 xml_config（-t 前为 > 或空格）均适用。
_T_ARG_PATTERN = r"(?<![A-Za-z0-9_])-t\s+\S+"


def _parse_t_arg(args_str: str):
    """解析命令行里的 ``-t X``，返回 X（字符串）；没有 -t 返回 None。"""
    m = re.search(r"(?<![A-Za-z0-9_])-t\s+(\S+)", args_str or "")
    return m.group(1) if m else None


def _rewrite_t_arg(args_str: str, new_index: int) -> str:
    """把命令行里的 ``-t X`` 改写为 ``-t <new_index>``。"""
    return re.sub(_T_ARG_PATTERN, f"-t {new_index}", args_str or "", count=1)


def _fix_current_argv(old_t, new_index: int):
    """若当前进程以 ``-t <old_t>`` 启动，改写 sys.argv 中的 -t 为新索引。

    这样本次计划任务触发（main.py -t 旧索引 -e）也能立即运行正确任务，
    而不必等到下次。
    """
    for i, arg in enumerate(sys.argv):
        if arg in ("-t", "--task") and i + 1 < len(sys.argv):
            if str(sys.argv[i + 1]) == str(old_t):
                sys.argv[i + 1] = str(new_index)
                return True
    return False


def _update_windows_task(task_path: str, new_actions: str) -> bool:
    """通过 COM 更新 Windows 计划任务的 Arguments（尽力而为）。"""
    try:
        import win32com.client

        rel = task_path.strip("\\")
        root, _, task_name = rel.partition("\\")
        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder(f"\\{root}")
        task = folder.GetTask(task_name)
        definition = task.Definition
        if definition.Actions.Count < 1:
            return False
        new_arguments = new_actions.split("main.py ", 1)[-1]
        definition.Actions.Item(1).Arguments = new_arguments
        # TASK_CREATE_OR_UPDATE=6, TASK_LOGON_INTERACTIVE_TOKEN=3
        folder.RegisterTaskDefinition(task_name, definition, 6, "", "", 3)
        return True
    except Exception as e:  # noqa: BLE001 - 尽力而为
        logger.warning(f"update windows task failed {task_path}: {e}")
        return False


def sync_schedule_task_indexes() -> int:
    """启动时校正 ok-ef 计划任务 -t 索引为当前 onetime_tasks 顺序。

    返回校正的任务数量。无变更时不写缓存、不调用 COM。
    """
    global _SYNCED
    if _SYNCED:
        return 0
    _SYNCED = True

    if not CACHE_FILE.exists():
        logger.debug(f"schedule cache not found: {CACHE_FILE}")
        return 0

    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"load schedule cache failed: {e}")
        return 0

    changed = 0
    for key, info in cache.items():
        path = info.get("path", "") or ""
        if not path.startswith(APP_ROOT + "\\"):
            continue  # 只处理本应用任务，其他 ok-* 应用只读不动

        name = info.get("name", "") or ""
        if not name:
            continue

        new_index = _find_index_by_name(name)
        if new_index is None:
            logger.warning(
                f"schedule task {key!r}: name {name!r} not found in onetime_tasks, skip"
            )
            continue

        actions = info.get("actions", "") or ""
        old_t = _parse_t_arg(actions)
        if old_t is None:
            continue
        if str(old_t) == str(new_index):
            continue  # 已正确

        new_actions = _rewrite_t_arg(actions, new_index)
        info["actions"] = new_actions

        if info.get("xml_config"):
            info["xml_config"] = re.sub(
                _T_ARG_PATTERN, f"-t {new_index}", info["xml_config"]
            )
        info["task_index"] = new_index

        _fix_current_argv(old_t, new_index)
        _update_windows_task(key, new_actions)

        logger.info(
            f"schedule task {key!r}: -t {old_t} -> -t {new_index} ({name})"
        )
        changed += 1

    if changed:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            logger.info(f"schedule cache updated: {CACHE_FILE} ({changed} tasks)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"save schedule cache failed: {e}")

    return changed


def install_schedule_task_index_sync_patch():
    """在 MainWindow 首次显示时校正计划任务 -t 索引（onetime_tasks 已就绪）。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.gui import MainWindow as MainWindowModule

    original_show_event = MainWindowModule.MainWindow.showEvent

    def patched_show_event(self, event):
        # 必须在 original showEvent 之前执行：showEvent 里会 parse_arguments_to_map()
        # 读取 sys.argv 中的 -t，先校正 argv 才能保证本次启动运行正确任务。
        try:
            sync_schedule_task_indexes()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"sync schedule task indexes failed: {e}")
        return original_show_event(self, event)

    MainWindowModule.MainWindow.showEvent = patched_show_event
    _PATCH_INSTALLED = True
