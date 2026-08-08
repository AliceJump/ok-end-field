# -*- coding: utf-8 -*-
"""schedule_task_index_sync_patch 的单元测试：启动时按新索引校正计划任务 -t。

覆盖：
- _find_index_by_name / _find_task_by_index（按任务名/类名解析当前 1-based 索引）
- sync_schedule_task_indexes：
  * 缓存中 -t 为旧数字索引 -> 改写为当前索引
  * 缓存中 -t 为任务名（历史迁移格式）-> 改写为当前索引
  * 已正确索引不变、无 -t 跳过、非本应用任务不动、找不到 name 跳过
  * 改写 actions / xml_config / task_index，并写回缓存
  * 无变更时不写缓存
- _fix_current_argv：当前进程 argv 中的 -t 被改写为新索引
- install_schedule_task_index_sync_patch 已安装（showEvent 被包装）

隔离策略
--------
本测试不依赖 ok 全局（不 init_ok/destroy_ok），而是 monkeypatch 模块级
`_onetime_tasks()` 返回假任务列表。这样在任何测试顺序（含 unittest discover
全量）下都稳定，不影响其它测试的 ok 全局状态。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

from src.config import config
from src.patches import schedule_task_index_sync_patch as patch

# 与 src/config.py onetime_tasks 顺序一致的任务名列表（业务 7 + 测试）
_FAKE_TASK_NAMES = [
    "日常任务",
    "运送委托",
    "仓库物品转移",
    "自动送货",
    "自动战斗",
    "演示绘图",
    "影拓丰碑",
    "启动一次游戏,120s后自动关闭",
    "战斗到结束",
    "箭头角度",
    "拖拽扫描",
    "暂停时机",
    "蓝点对齐",
    "等级读取",
    "演示图形",
    "实时检测",
    "诊断",
    "战斗槽检测",
    "战斗模板匹配",
    "鼠标旋转标定",
]


class _FakeTask:
    """模拟 BaseTask：只有 name 与类名，供索引解析。"""

    def __init__(self, name, cls_name=None):
        self.name = name
        self.__class__ = type(cls_name or name, (object,), {})


class TestScheduleTaskIndexSyncPatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patch.install_schedule_task_index_sync_patch()

    def setUp(self):
        # 每个用例独立注入假任务列表，并重置同步 guard 与缓存路径
        patch._SYNCED = False
        self._orig_onetime_tasks = patch._onetime_tasks
        patch._onetime_tasks = lambda: [
            _FakeTask(n, f"Task{i + 1}") for i, n in enumerate(_FAKE_TASK_NAMES)
        ]
        self.addCleanup(self._restore)

    def _restore(self):
        patch._onetime_tasks = self._orig_onetime_tasks

    # ---------- 索引解析 ----------

    def test_find_index_by_name(self):
        # 假任务顺序：日常任务=1, 自动送货=4, 影拓丰碑=7, 启动一次游戏=8
        self.assertEqual(patch._find_index_by_name("影拓丰碑"), 7)
        self.assertEqual(patch._find_index_by_name("日常任务"), 1)
        self.assertEqual(patch._find_index_by_name("启动一次游戏,120s后自动关闭"), 8)
        self.assertIsNone(patch._find_index_by_name("不存在的任务"))

    def test_find_index_by_class_name(self):
        # 假任务类名为 TaskN（1-based）
        self.assertEqual(patch._find_index_by_name("Task7"), 7)
        self.assertEqual(patch._find_index_by_name("Task1"), 1)
        self.assertEqual(patch._find_index_by_name("Task4"), 4)

    def test_find_task_by_index(self):
        task = patch._find_task_by_index(7)
        self.assertIsNotNone(task)
        self.assertEqual(task.name, "影拓丰碑")
        self.assertIsNone(patch._find_task_by_index(999))
        self.assertIsNone(patch._find_task_by_index(0))

    # ---------- argv 改写 ----------

    def test_fix_current_argv(self):
        old_argv = ["main.py", "-t", "15", "-e"]
        orig = patch.sys.argv
        patch.sys.argv = old_argv
        try:
            changed = patch._fix_current_argv("15", 8)
            self.assertTrue(changed)
            self.assertEqual(patch.sys.argv, ["main.py", "-t", "8", "-e"])
        finally:
            patch.sys.argv = orig

    def test_fix_current_argv_task_name(self):
        # 历史迁移格式：argv 里是任务名，也改写为新索引
        old_argv = ["main.py", "-t", "影拓丰碑", "-e"]
        orig = patch.sys.argv
        patch.sys.argv = old_argv
        try:
            changed = patch._fix_current_argv("影拓丰碑", 7)
            self.assertTrue(changed)
            self.assertEqual(patch.sys.argv, ["main.py", "-t", "7", "-e"])
        finally:
            patch.sys.argv = orig

    def test_fix_current_argv_no_match(self):
        old_argv = ["main.py", "-e"]
        orig = patch.sys.argv
        patch.sys.argv = old_argv
        try:
            self.assertFalse(patch._fix_current_argv("15", 8))
        finally:
            patch.sys.argv = orig

    # ---------- 同步逻辑 ----------

    def _make_cache(self, tasks):
        """写一个临时缓存文件并 monkeypatch CACHE_FILE，返回缓存文件路径"""
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        orig_cache_file = patch.CACHE_FILE
        patch.CACHE_FILE = Path(path)
        self.addCleanup(lambda: patch.__dict__.update(CACHE_FILE=orig_cache_file))
        return path

    def _read_cache(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_sync_old_index_to_new(self):
        # 旧索引：启动一次游戏原为 -t 15（旧顺序），现应为 -t 8
        tasks = {
            r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e": {
                "path": r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e",
                "name": "启动一次游戏,120s后自动关闭",
                "actions": "D:\\x\\python.exe main.py -t 15 -e",
                "xml_config": "<Arguments>-t 15 -e</Arguments>",
                "task_index": 15,
            }
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 1)

        data = self._read_cache(path)
        info = data[r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e"]
        self.assertIn("-t 8", info["actions"])
        self.assertIn("-t 8", info["xml_config"])
        self.assertEqual(info["task_index"], 8)

    def test_sync_task_name_to_new_index(self):
        # 历史迁移格式：-t 任务名 -> 改回 -t 新索引（影拓丰碑 -> 7）
        tasks = {
            r"\ok-ef\影拓丰碑_e7c8e618": {
                "path": r"\ok-ef\影拓丰碑_e7c8e618",
                "name": "影拓丰碑",
                "actions": "D:\\x\\python.exe main.py -t 影拓丰碑 -e",
                "xml_config": "<Arguments>-t 影拓丰碑 -e</Arguments>",
                "task_index": -1,
            }
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 1)

        info = self._read_cache(path)[r"\ok-ef\影拓丰碑_e7c8e618"]
        self.assertIn("-t 7", info["actions"])
        self.assertIn("-t 7", info["xml_config"])
        self.assertEqual(info["task_index"], 7)

    def test_sync_already_correct_no_change(self):
        # 已是最新索引（日常任务 -> 1），无变更
        tasks = {
            r"\ok-ef\日常任务_98548fce": {
                "path": r"\ok-ef\日常任务_98548fce",
                "name": "日常任务",
                "actions": "D:\\x\\python.exe main.py -t 1 -e",
                "xml_config": "<Arguments>-t 1 -e</Arguments>",
                "task_index": 1,
            }
        }
        path = self._make_cache(tasks)
        mtime = Path(path).stat().st_mtime

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 0)
        # 无变更不应写文件
        self.assertEqual(Path(path).stat().st_mtime, mtime)

    def test_sync_skips_other_apps(self):
        # 非 ok-ef 应用的任务不动（read_only）
        tasks = {
            r"\ok-gf2\一键日常_3696743b": {
                "path": r"\ok-gf2\一键日常_3696743b",
                "name": "一键日常",
                "actions": "D:\\x\\python.exe main.py -t 3 -e",
                "xml_config": "<Arguments>-t 3 -e</Arguments>",
                "task_index": 3,
            }
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 0)

        info = self._read_cache(path)[r"\ok-gf2\一键日常_3696743b"]
        self.assertIn("-t 3", info["actions"])
        self.assertEqual(info["task_index"], 3)

    def test_sync_skips_unknown_name(self):
        # name 不在 onetime_tasks 中 -> 跳过，不报错
        tasks = {
            r"\ok-ef\不存在的任务_12345678": {
                "path": r"\ok-ef\不存在的任务_12345678",
                "name": "不存在的任务",
                "actions": "D:\\x\\python.exe main.py -t 5 -e",
                "xml_config": "<Arguments>-t 5 -e</Arguments>",
                "task_index": 5,
            }
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 0)

    def test_sync_no_t_arg(self):
        # 无 -t 的任务跳过
        tasks = {
            r"\ok-ef\日常任务_98548fce": {
                "path": r"\ok-ef\日常任务_98548fce",
                "name": "日常任务",
                "actions": "D:\\x\\python.exe main.py -e",
                "xml_config": "<Arguments>-e</Arguments>",
                "task_index": 1,
            }
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 0)

    def test_sync_multiple_tasks(self):
        # 混合：一个旧索引 + 一个任务名，同时校正
        tasks = {
            r"\ok-ef\自动送货_5a22bfe3": {
                "path": r"\ok-ef\自动送货_5a22bfe3",
                "name": "自动送货",
                "actions": "D:\\x\\python.exe main.py -t 自动送货 -e",
                "xml_config": "<Arguments>-t 自动送货 -e</Arguments>",
                "task_index": -1,
            },
            r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e": {
                "path": r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e",
                "name": "启动一次游戏,120s后自动关闭",
                "actions": "D:\\x\\python.exe main.py -t 15 -e",
                "xml_config": "<Arguments>-t 15 -e</Arguments>",
                "task_index": 15,
            },
        }
        path = self._make_cache(tasks)

        changed = patch.sync_schedule_task_indexes()
        self.assertEqual(changed, 2)

        data = self._read_cache(path)
        self.assertIn("-t 4", data[r"\ok-ef\自动送货_5a22bfe3"]["actions"])
        self.assertIn("-t 8", data[r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e"]["actions"])

    def test_sync_updates_current_argv(self):
        # 同步时把当前进程 argv 里的 -t 旧值改为新索引（本次运行也正确）
        old_argv = ["main.py", "-t", "15", "-e"]
        orig = patch.sys.argv
        patch.sys.argv = old_argv
        try:
            tasks = {
                r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e": {
                    "path": r"\ok-ef\启动一次游戏,120s后自动关闭_1672d46e",
                    "name": "启动一次游戏,120s后自动关闭",
                    "actions": "D:\\x\\python.exe main.py -t 15 -e",
                    "xml_config": "<Arguments>-t 15 -e</Arguments>",
                    "task_index": 15,
                }
            }
            path = self._make_cache(tasks)
            patch.sync_schedule_task_indexes()
            self.assertEqual(patch.sys.argv, ["main.py", "-t", "8", "-e"])
        finally:
            patch.sys.argv = orig

    def test_patch_installed(self):
        # showEvent 已被包装
        from ok.gui import MainWindow as MainWindowModule

        self.assertNotEqual(
            MainWindowModule.MainWindow.showEvent.__name__,
            "showEvent",
        )


if __name__ == "__main__":
    unittest.main()
