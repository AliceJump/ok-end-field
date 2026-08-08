# -*- coding: utf-8 -*-
"""让 Windows 计划任务的 -t 参数使用任务名而非 onetime_tasks 索引。

背景
----
ok-script 的 Windows 计划任务（ScheduleTaskTab）在创建时把 -t 参数写成
onetime_tasks 的 1-based 索引（如 ``main.py -t 15 -e``），运行时按索引取
``onetime_tasks[N-1]``。一旦 onetime_tasks 排序变化（新增任务、按业务/测试
分组重排等），已创建的计划任务仍指向旧索引，会运行到错误任务，且计划任务
模块不会自动更新（configs/schedule_tasks_cache.json 与 Windows 计划任务里
保存的都是创建时刻的 -t N）。

修复
----
1. 创建/修改计划任务时：-t 写入任务名（Task.name）而非索引。Task.name 不随
   onetime_tasks 排序变化，因此计划任务对排序免疫。
2. 运行时解析 -t：数字索引（旧格式）保持兼容；任务名解析为当前 onetime_tasks
   中的 1-based 索引再启动，因此重排后计划任务仍正确指向目标任务。
3. 修改对话框 _parse_args 同时支持解析 -t 后的任务名或数字索引。

闭环示例
--------
- 创建计划任务「影拓丰碑」→ XML Arguments 为 ``main.py -t 影拓丰碑 -e``。
- 运行时 ``main.py -t 影拓丰碑 -e`` → 本 patch 把名字解析为当前索引
  （如 7）→ ``onetime_tasks[6]`` 即 YingTuoTask。
- 若后续重排 onetime_tasks，计划任务里的任务名不变，运行时仍解析到正确任务。
"""
from __future__ import annotations

from ok import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False


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


def install_schedule_task_name_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import argparse

    from ok.util import process as process_util
    from ok.util import windows_schedule

    # 1) 运行时解析：-t 支持数字索引（旧格式）或任务名（新格式）。
    #    任务名在解析时换算为当前 onetime_tasks 的 1-based 索引，
    #    MainWindow.showEvent 的 `args.get('task') > 0` / `- 1` 逻辑无需改动。
    original_parse_arguments_to_map = process_util.parse_arguments_to_map

    def patched_parse_arguments_to_map(description="main script"):
        parser = argparse.ArgumentParser(description=description, add_help=False)
        parser.add_argument("--help", action="help", help="show this help message and exit")
        parser.add_argument(
            "-t", "--task",
            help="which task to execute as 1-based index or task name",
            default=0,
        )
        parser.add_argument("-e", "--exit", action="store_true", help="exit after task")
        parser.add_argument("-h", "--headless", action="store_true", help="start without ui")
        args, _ = parser.parse_known_args()
        arg_map = vars(args)
        task = arg_map.get("task")
        if isinstance(task, str):
            s = task.strip()
            if s.isdigit():
                # 旧格式：数字索引
                arg_map["task"] = int(s)
            else:
                index = _find_index_by_name(s)
                if index is not None:
                    arg_map["task"] = index
                else:
                    logger.warning(f"-t task name not found in onetime_tasks: {s!r}")
                    arg_map["task"] = 0  # 找不到不自动启动
        return arg_map

    process_util.parse_arguments_to_map = patched_parse_arguments_to_map

    # 2) 创建/修改计划任务：把 GUI 传入的 onetime_tasks 索引换算为任务名，
    #    使 _generate_task_xml 生成 `-t <任务名> -e`。
    original_create_task = windows_schedule.WindowsScheduleManager.create_task

    def patched_create_task(self, task_name, task_index, *args, **kwargs):
        if isinstance(task_index, int) and not isinstance(task_index, bool):
            task = _find_task_by_index(task_index)
            if task is not None:
                task_index = task.name
                logger.info(
                    f"schedule task {task_name!r}: -t index -> task name {task_index!r}"
                )
            else:
                logger.warning(
                    f"schedule task {task_name!r}: task index {task_index} out of range, "
                    f"keeping index"
                )
        return original_create_task(self, task_name, task_index, *args, **kwargs)

    windows_schedule.WindowsScheduleManager.create_task = patched_create_task

    # 3) 修改计划任务对话框：_parse_args 解析 -t 后跟任务名或数字索引。
    from ok.gui.tasks import ScheduleTaskTab

    original_parse_args = ScheduleTaskTab.ModifyScheduleTaskDialog._parse_args

    def patched_parse_args(self, actions: str):
        import re

        args = actions or ""
        task_selector = 1
        auto_exit = False

        m = re.search(r"(?:^|\s)-t\s+(\S+)(?:\s|$)", args)
        if m:
            raw = m.group(1)
            try:
                task_selector = int(raw)
            except ValueError:
                task_selector = raw  # 任务名

        if re.search(r"(?:^|\s)-e(?:\s|$)", args):
            auto_exit = True

        return task_selector, auto_exit

    ScheduleTaskTab.ModifyScheduleTaskDialog._parse_args = patched_parse_args

    _PATCH_INSTALLED = True
