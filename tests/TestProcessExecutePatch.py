"""ok.util.process.execute 启动补丁的单元测试。

背景
----
ok/util/process.py 的 execute() 存在两个问题:

1. `start /b` 分支接管 stdout/stderr=PIPE 但从不读取, 游戏(反作弊/SDK)启动时
   向 stdout 写日志, 管道缓冲区写满后游戏进程阻塞, 窗口永不出现;
2. os.startfile 分支在游戏不带启动参数时把 None 传给只接受 str 的 arguments,
   必然抛 "TypeError: startfile() argument 'arguments' must be str, not None"。

本测试验证 src.patches.process_execute_patch.install_process_execute_patch
用修复版替换 ok.util.process.execute: 管道改为 DEVNULL, startfile 参数回退为 ""。

隔离策略
--------
仅替换 process_mod.os / process_mod.subprocess 为桩对象, 不真正拉起进程;
测试类结束后恢复原模块属性与安装标记, 由文件末尾的哨兵测试验证全局状态恢复。
"""

import os
import subprocess
import types
import unittest

import ok.util.process as process_mod

from src.patches import process_execute_patch

GAME_PATH = os.path.abspath(__file__)


class _Recorder:
    def __init__(self):
        self.calls = []


class TestProcessExecutePatch(unittest.TestCase):
    _original_execute = None
    _original_os = None
    _original_subprocess = None
    _original_flag = None
    _original_cached_execute = None
    _cleanup_completed = False

    @classmethod
    def setUpClass(cls):
        cls._original_execute = process_mod.execute
        cls._original_os = process_mod.os
        cls._original_subprocess = process_mod.subprocess
        cls._original_flag = process_execute_patch._PATCH_INSTALLED
        cls._original_cached_execute = process_execute_patch._original_execute
        cls.addClassCleanup(cls._restore_patch_state)
        process_execute_patch._PATCH_INSTALLED = False
        process_execute_patch.install_process_execute_patch()

    @classmethod
    def _restore_patch_state(cls):
        process_mod.execute = cls._original_execute
        process_mod.os = cls._original_os
        process_mod.subprocess = cls._original_subprocess
        process_execute_patch._PATCH_INSTALLED = cls._original_flag
        process_execute_patch._original_execute = cls._original_cached_execute
        cls._cleanup_completed = True

    def _install_stubs(self):
        recorder = _Recorder()

        def startfile(*args, **kwargs):
            recorder.calls.append(("startfile", args))

        def popen(*args, **kwargs):
            recorder.calls.append(("popen", args, kwargs))
            return object()

        process_mod.os = types.SimpleNamespace(
            path=os.path,
            startfile=startfile,
        )
        process_mod.subprocess = types.SimpleNamespace(
            PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
            CREATE_NEW_PROCESS_GROUP=subprocess.CREATE_NEW_PROCESS_GROUP,
            Popen=popen,
        )
        return recorder

    def test_execute_replaced(self):
        self.assertIs(process_mod.execute, process_execute_patch._execute_fixed)

    def test_install_idempotent(self):
        before = process_mod.execute
        process_execute_patch.install_process_execute_patch()
        self.assertIs(process_mod.execute, before)
        self.assertTrue(process_execute_patch._PATCH_INSTALLED)

    def test_startfile_arguments_none_fixed(self):
        # 游戏不带启动参数时 arguments 回退为 "", 不再传 None
        recorder = self._install_stubs()
        result = process_mod.execute(GAME_PATH, None, "os.startfile")
        self.assertTrue(result)
        kind, args = recorder.calls[0]
        self.assertEqual(kind, "startfile")
        self.assertEqual(args[0], GAME_PATH)
        self.assertEqual(args[2], "")

    def test_popen_uses_devnull(self):
        # start /b 分支不再接管 stdout/stderr 管道
        recorder = self._install_stubs()
        result = process_mod.execute(GAME_PATH)
        self.assertTrue(result)
        kind, _, kwargs = recorder.calls[0]
        self.assertEqual(kind, "popen")
        self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stderr"), subprocess.DEVNULL)

    def test_path_not_exist_returns_none(self):
        self._install_stubs()
        self.assertIsNone(process_mod.execute(os.path.join(GAME_PATH, "NotEndfield.exe")))


class TestZProcessExecutePatchStateRestored(unittest.TestCase):
    def test_global_state_restored_after_patch_tests(self):
        self.assertTrue(TestProcessExecutePatch._cleanup_completed)
        self.assertIs(process_mod.execute, TestProcessExecutePatch._original_execute)
        self.assertIs(process_mod.os, TestProcessExecutePatch._original_os)
        self.assertIs(process_mod.subprocess, TestProcessExecutePatch._original_subprocess)
        self.assertEqual(
            process_execute_patch._PATCH_INSTALLED,
            TestProcessExecutePatch._original_flag,
        )
        self.assertIs(
            process_execute_patch._original_execute,
            TestProcessExecutePatch._original_cached_execute,
        )


if __name__ == "__main__":
    unittest.main()
