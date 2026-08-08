# -*- coding: utf-8 -*-
"""把本应用（ok-ef）已创建的 Windows 计划任务 -t 索引校正为当前 onetime_tasks 顺序。

背景
----
ok-script 的 Windows 计划任务创建时把 -t 参数写成 onetime_tasks 的 1-based 索引
（如 ``main.py -t 15 -e``）。一旦 onetime_tasks 排序变化，旧索引会指向错误任务。
应用每次启动时（src/patches/schedule_task_index_sync_patch.py）会自动按缓存里的
任务名校正索引，无需手动运行本脚本。本工具用于：
1. 一次性批量校正（把历史迁移成的 ``-t 任务名`` 或旧 ``-t 索引`` 统一改回
   ``-t 新索引``），并同步 Windows 计划任务；
2. --dry-run 预览将要发生的变更。

用法
----
    .\\.venv\\Scripts\\python.exe tools\\fix_schedule_task_refs.py          # 实际校正
    .\\.venv\\Scripts\\python.exe tools\\fix_schedule_task_refs.py --dry-run # 只预览

说明
----
- 复用 src/patches/schedule_task_index_sync_patch.py 的 sync_schedule_task_indexes()，
  与启动时自动校正逻辑完全一致（单一实现）。
- 只处理属于当前应用（gui_title=ok-ef，计划任务根路径 \\ok-ef\\）的任务。
- 缓存文件 configs/schedule_tasks_cache.json 会被更新。
- Windows 计划任务本身通过 COM 尽力更新（需要权限；失败不影响缓存）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import config  # noqa: E402
from src.patches.schedule_task_index_sync_patch import (  # noqa: E402
    CACHE_FILE,
    sync_schedule_task_indexes,
)


def main():
    parser = argparse.ArgumentParser(
        description="校正 ok-ef 计划任务 -t 索引为当前 onetime_tasks 顺序"
    )
    parser.add_argument("--dry-run", action="store_true", help="只预览变更，不写文件")
    args = parser.parse_args()

    if not CACHE_FILE.exists():
        print(f"未找到缓存文件: {CACHE_FILE}")
        sys.exit(1)

    if args.dry_run:
        print("dry-run 模式：只预览，不写文件。")
        print("将扫描并按启动逻辑校正：")
        print(f"  {CACHE_FILE}")
        print("以及 Windows 计划任务（COM，尽力而为）。")
        sys.exit(0)

    # 加载 ok，使 og.executor.onetime_tasks 就绪，然后复用启动时的同步逻辑
    try:
        from ok.test import init_ok

        cfg_config = dict(config)
        cfg_config["trigger_tasks"] = []
        init_ok(cfg_config)
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 初始化 ok 失败: {e}")
        sys.exit(1)

    changed = sync_schedule_task_indexes()
    if changed:
        print(f"\n已校正 {changed} 个计划任务（缓存 + Windows 计划任务）")
    else:
        print("\n无需校正（所有 ok-ef 计划任务索引已是最新）")


if __name__ == "__main__":
    main()
