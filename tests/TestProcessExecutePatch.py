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
启动流程使用桩对象；原生 API 测试只创建临时 Python 进程，不启动游戏；
测试类结束后恢复原模块属性与安装标记, 由文件末尾的哨兵测试验证全局状态恢复。
"""

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ok.util.process as process_mod
import psutil

from src.core import official_launcher
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
        import ok.core.start_controller as controller

        cls._controller = controller
        cls._original_controller_execute = controller.execute
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
        cls._controller.execute = cls._original_controller_execute
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

    def test_gui_cached_execute_replaced(self):
        self.assertIs(self._controller.execute, process_execute_patch._execute_fixed)

    def test_endfield_uses_official_parent_for_both_start_methods(self):
        recorder = self._install_stubs()
        with (
            patch.object(process_mod, "get_path", return_value=r"D:\Game Folder\Endfield.exe"),
            patch.object(official_launcher, "launch_game", return_value=True) as launch,
            patch.object(process_mod.os, "path", wraps=os.path) as paths,
        ):
            paths.exists.return_value = True
            for method in ("start", "os.startfile"):
                self.assertTrue(
                    process_mod.execute(
                        r'"D:\Game Folder\Endfield.exe" -launcher_lang=en-us',
                        "-force-d3d11",
                        method,
                    )
                )
            launch.assert_called_with(
                r"D:\Game Folder\Endfield.exe",
                "-launcher_lang=en-us -force-d3d11",
                process_mod.logger,
            )
        self.assertEqual(recorder.calls, [])

    def test_endfield_failure_does_not_fall_back_to_shell(self):
        recorder = self._install_stubs()
        with (
            patch.object(process_mod, "get_path", return_value=r"D:\Endfield.exe"),
            patch.object(official_launcher, "launch_game", side_effect=OSError("access denied")),
            patch.object(process_mod.os, "path", wraps=os.path) as paths,
        ):
            paths.exists.return_value = True
            self.assertIsNone(process_mod.execute(r"D:\Endfield.exe"))
        self.assertEqual(recorder.calls, [])

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
        self.assertIs(
            TestProcessExecutePatch._controller.execute,
            TestProcessExecutePatch._original_controller_execute,
        )
        self.assertEqual(
            process_execute_patch._PATCH_INSTALLED,
            TestProcessExecutePatch._original_flag,
        )
        self.assertIs(
            process_execute_patch._original_execute,
            TestProcessExecutePatch._original_cached_execute,
        )


class TestOfficialLauncher(unittest.TestCase):
    def test_prepare_reuses_running_launcher(self):
        with (
            patch.object(official_launcher, "official_parent", return_value=123),
            patch.object(official_launcher, "installed_launcher") as installed,
            patch.object(official_launcher.subprocess, "Popen") as popen,
        ):
            self.assertEqual(official_launcher.prepare_launcher(Mock(), Mock(), GAME_PATH), 123)
        installed.assert_not_called()
        popen.assert_not_called()

    def test_prepare_opens_discovered_launcher_and_waits_for_games(self):
        with (
            patch.object(official_launcher, "official_parent", side_effect=[None, None, 123]),
            patch.object(
                official_launcher,
                "installed_launcher",
                return_value=r"D:\Launcher\Launcher.exe",
            ),
            patch.object(official_launcher.subprocess, "Popen") as popen,
            patch.object(official_launcher.time, "sleep") as sleep,
        ):
            self.assertEqual(official_launcher.prepare_launcher(Mock(), Mock(), GAME_PATH), 123)
        self.assertEqual(
            popen.call_args.args[0],
            [r"D:\Launcher\Launcher.exe", "--game=Endfield", "--reason=4"],
        )
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(
            popen.call_args.kwargs["creationflags"],
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        sleep.assert_called_once_with(0.25)

    def test_prepare_missing_launcher_fails_without_spawning(self):
        with (
            patch.object(official_launcher, "official_parent", return_value=None),
            patch.object(
                official_launcher,
                "installed_launcher",
                side_effect=RuntimeError("not installed"),
            ),
            patch.object(official_launcher.subprocess, "Popen") as popen,
            self.assertRaisesRegex(RuntimeError, "not installed"),
        ):
            official_launcher.prepare_launcher(Mock(), Mock(), GAME_PATH)
        popen.assert_not_called()

    def test_prepare_times_out_without_games_process(self):
        with (
            patch.object(official_launcher, "official_parent", return_value=None),
            patch.object(
                official_launcher,
                "installed_launcher",
                return_value=r"D:\Launcher\Launcher.exe",
            ),
            patch.object(official_launcher.subprocess, "Popen"),
            patch.object(official_launcher.time, "monotonic", side_effect=[0, 31]),
            self.assertRaises(TimeoutError),
        ):
            official_launcher.prepare_launcher(Mock(), Mock(), GAME_PATH)

    def test_parent_access_denied_is_reported_without_retrying_launcher(self):
        kernel = Mock()
        kernel.OpenProcess.side_effect = OSError("access denied")
        with (
            patch.object(official_launcher, "running_pids", return_value=[123]),
            patch.object(official_launcher, "installed_launcher") as installed,
            self.assertRaisesRegex(RuntimeError, "privileges"),
        ):
            official_launcher.prepare_launcher(kernel, Mock(), GAME_PATH)
        installed.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_installed_launcher_uses_persisted_verified_path(self):
        logger = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "Launcher.exe"
            launcher.touch()
            cache = root / "official_launcher.json"
            cache.write_text(json.dumps({"path": str(launcher)}), encoding="utf-8")
            with (
                patch.object(
                    official_launcher,
                    "get_launcher_cache_path",
                    return_value=str(cache),
                ),
                patch.object(official_launcher, "_registered_launchers") as registered,
                patch.object(official_launcher, "_game_path_launchers") as inferred,
                patch.object(official_launcher, "verify_launcher") as verify,
            ):
                self.assertEqual(
                    official_launcher.installed_launcher("unused", logger),
                    str(launcher),
                )
        verify.assert_called_once_with(str(launcher))
        registered.assert_called_once_with()
        inferred.assert_called_once_with("unused")

    def test_installed_launcher_infers_from_game_path_and_persists(self):
        logger = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "Launcher.exe"
            launcher.touch()
            game = root / "games" / "EndField Game" / "Endfield.exe"
            game.parent.mkdir(parents=True)
            game.touch()
            cache = root / "configs" / "official_launcher.json"
            with (
                patch.object(
                    official_launcher,
                    "get_launcher_cache_path",
                    return_value=str(cache),
                ),
                patch.object(official_launcher, "_registered_launchers", return_value=[]),
                patch.object(official_launcher, "verify_launcher") as verify,
            ):
                self.assertEqual(
                    official_launcher.installed_launcher(str(game), logger),
                    str(launcher),
                )
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8")),
                {"path": os.path.abspath(launcher)},
            )
        verify.assert_called_once_with(str(launcher))

    def test_invalid_cached_launcher_falls_back_and_replaces_cache(self):
        logger = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / "stale" / "Launcher.exe"
            stale.parent.mkdir()
            stale.touch()
            launcher = root / "Launcher.exe"
            launcher.touch()
            cache = root / "official_launcher.json"
            cache.write_text(json.dumps({"path": str(stale)}), encoding="utf-8")
            with (
                patch.object(
                    official_launcher,
                    "get_launcher_cache_path",
                    return_value=str(cache),
                ),
                patch.object(official_launcher, "_registered_launchers", return_value=[]),
                patch.object(
                    official_launcher,
                    "_game_path_launchers",
                    return_value=[str(launcher)],
                ),
                patch.object(
                    official_launcher,
                    "verify_launcher",
                    side_effect=[RuntimeError("bad signature"), None],
                ),
            ):
                self.assertEqual(
                    official_launcher.installed_launcher("unused", logger),
                    str(launcher),
                )
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8")),
                {"path": os.path.abspath(launcher)},
            )
        logger.warning.assert_called_once()

    def test_launch_game_preserves_arguments_without_injecting_defaults(self):
        kernel = Mock()
        game_path = os.path.abspath("Endfield.exe")
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "prepare_launcher", return_value=321),
            patch.object(official_launcher, "create_process", return_value=456) as create,
        ):
            self.assertTrue(official_launcher.launch_game(game_path, "-force-d3d11", Mock()))
        create.assert_called_once_with(kernel, game_path, "-force-d3d11", 321)
        kernel.CloseHandle.assert_called_once_with(321)

    def test_running_game_skips_launcher(self):
        with (
            patch.object(official_launcher, "running_pids", return_value=[123]),
            patch.object(official_launcher, "prepare_launcher") as prepare,
        ):
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
            prepare.assert_not_called()

    def test_game_started_during_preparation_skips_duplicate_and_closes_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], [123]]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "prepare_launcher", return_value=321),
            patch.object(official_launcher, "create_process") as create,
        ):
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
            create.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(321)

    def test_create_failure_closes_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", return_value=[]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "prepare_launcher", return_value=321),
            patch.object(official_launcher, "create_process", side_effect=OSError("denied")),
            self.assertRaises(OSError),
        ):
            official_launcher.launch_game("unused", "", Mock())
        kernel.CloseHandle.assert_called_once_with(321)

    def test_signature_requires_valid_official_signer(self):
        signer = next(iter(official_launcher.OFFICIAL_SIGNERS))
        for status, fingerprint, accepted in ((0, signer, True), (1, signer, False), (0, "other", False)):
            with (
                self.subTest(status=status, signer=fingerprint),
                patch.object(
                    official_launcher.subprocess,
                    "run",
                    return_value=types.SimpleNamespace(
                        stdout=json.dumps({"status": status, "signer": fingerprint}),
                    ),
                ),
            ):
                if accepted:
                    official_launcher.verify_launcher("Games.exe")
                else:
                    with self.assertRaises(RuntimeError):
                        official_launcher.verify_launcher("Games.exe")

    @unittest.skipUnless(sys.platform == "win32", "Windows native process API")
    def test_native_parent_cwd_and_unicode_arguments(self):
        # Use two disposable Python processes, never a game or the real launcher.
        kernel = official_launcher._kernel32()
        # The venv executable is a redirector that starts a second process.
        # Use the underlying executable to measure the actual native child.
        executable = sys._base_executable
        parent = subprocess.Popen(
            [executable, "-c", "import sys; sys.stdin.read()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        handle = None
        try:
            handle = official_launcher._check(kernel.OpenProcess(0x0080 | 0x1000, False, parent.pid))
            with tempfile.TemporaryDirectory() as folder:
                receipt = Path(folder) / "receipt.json"
                code = (
                    "import os,sys,json; from pathlib import Path; "
                    "p=Path(sys.argv[1]); q=p.with_suffix('.tmp'); "
                    "q.write_text(json.dumps([os.getppid(),os.getcwd(),sys.argv[2:]]),encoding='utf-8'); "
                    "q.replace(p)"
                )
                values = ["含空格 value", 'quote"and\\slashes\\']
                arguments = subprocess.list2cmdline(["-c", code, str(receipt), *values])
                pid = official_launcher.create_process(kernel, executable, arguments, handle)
                child = psutil.Process(pid)
                try:
                    self.assertEqual(child.wait(timeout=10), 0)
                finally:
                    if child.is_running():
                        child.kill()
                        child.wait(timeout=5)
                actual_parent, cwd, actual_values = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertEqual(actual_parent, parent.pid)
                self.assertEqual(os.path.normcase(cwd), os.path.normcase(os.path.dirname(executable)))
                self.assertEqual(actual_values, values)
        finally:
            if handle:
                kernel.CloseHandle(handle)
            parent.stdin.close()
            parent.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
