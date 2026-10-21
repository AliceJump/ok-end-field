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


class TestLauncherChannels(unittest.TestCase):
    def test_game_channel_uses_company_not_installation_folder_name(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "GRYPHLINK" / "renamed game"
            (folder / "Endfield_Data").mkdir(parents=True)
            info = folder / "Endfield_Data" / "app.info"
            for company in ("Hypergryph", "Gryphline"):
                info.write_text(f"{company}\nEndfield\n", encoding="utf-8-sig")
                self.assertEqual(official_launcher._game_company(str(folder / "Endfield.exe")), company)
            for content in ("Other\nEndfield\n", "Gryphline\nOther Game\n", "Gryphline"):
                info.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "channel"):
                    official_launcher._game_company(str(folder / "Endfield.exe"))
            info.unlink()
            with self.assertRaisesRegex(RuntimeError, "channel"):
                official_launcher._game_company(str(folder / "Endfield.exe"))

    def test_registry_queries_do_not_cross_channels(self):
        import winreg

        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "Launcher.exe"
            launcher.touch()
            for company in ("Hypergryph", "Gryphline"):
                with (
                    self.subTest(company=company),
                    patch.object(winreg, "OpenKey") as open_key,
                    patch.object(winreg, "QueryValueEx", return_value=(directory, winreg.REG_SZ)),
                ):
                    self.assertIn(str(launcher), official_launcher._registered_launchers(company))
                queried = {call.args[1] for call in open_key.call_args_list}
                expected = {official_launcher.CHANNEL_REGISTRY_KEYS[company]}
                if company == "Hypergryph":
                    expected.add(rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{official_launcher.LAUNCHER_ID}")
                self.assertEqual(queried, expected)


class TestOfficialLauncher(unittest.TestCase):

    def test_detects_steam_and_epic_installations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            steam = root / "steamapps" / "common" / "EndField Game" / "Endfield.exe"
            epic = root / "EpicGame" / "Endfield.exe"
            (epic.parent / ".egstore").mkdir(parents=True)
            self.assertEqual(official_launcher._platform_for_game(str(steam)), "steam")
            self.assertEqual(official_launcher._platform_for_game(str(epic)), "epic")
            self.assertIsNone(official_launcher._platform_for_game(str(root / "Endfield.exe")))

    def test_steam_launch_routes_through_client_without_cn_parent(self):
        path = os.path.abspath(os.path.join("steamapps", "common", "EndField Game", "Endfield.exe"))
        with (
            patch.object(official_launcher, "running_pids", return_value=[]),
            patch.object(official_launcher.os, "startfile", create=True) as start,
            patch.object(official_launcher, "create_temporary_parent") as parent,
        ):
            self.assertTrue(official_launcher.launch_game(path, "", Mock()))
        start.assert_called_once_with("steam://rungameid/4732690")
        parent.assert_not_called()

    def test_epic_launch_uses_official_installation_path_uri(self):
        from urllib.parse import quote

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".egstore").mkdir()
            # The official protocol can use the installed directory; no
            # deprecated Artifact ID or optional .mancpn manifest is required.
            with (
                patch.object(official_launcher, "running_pids", return_value=[]),
                patch.object(official_launcher.os, "startfile", create=True) as start,
                patch.object(official_launcher, "create_temporary_parent") as parent,
            ):
                self.assertTrue(official_launcher.launch_game(str(root / "Endfield.exe"), "", Mock()))
            expected_path = quote(str(root).replace("\\", "/"), safe="")
            start.assert_called_once_with(
                f"com.epicgames.launcher://apps/{expected_path}?action=launch&silent=true"
            )
            parent.assert_not_called()

    def test_platform_launch_rejects_arguments_instead_of_silently_dropping(self):
        path = os.path.abspath(os.path.join("steamapps", "common", "EndField Game", "Endfield.exe"))
        with (
            patch.object(official_launcher, "running_pids", return_value=[]),
            patch.object(official_launcher.os, "startfile", create=True) as start,
            self.assertRaisesRegex(RuntimeError, "arguments"),
        ):
            official_launcher.launch_game(path, "-force-d3d11", Mock())
        start.assert_not_called()

    def test_gryphline_uses_the_shared_parent_cleanup(self):
        game = os.path.abspath(os.path.join("GRYPHLINK", "games", "EndField Game", "Endfield.exe"))
        kernel = Mock()
        with (
            patch.object(official_launcher, "_game_company", return_value="Gryphline"),
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", return_value=456),
        ):
            self.assertTrue(official_launcher.launch_game(game, "", Mock()))
        cleanup.assert_called_once_with(kernel, 321, unittest.mock.ANY, company="Gryphline")
        cleanup.return_value.start.assert_called_once_with(456)

    def test_cached_cn_launcher_never_overrides_international_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            intl = base / "GRYPHLINK" / "Launcher.exe"
            cn = base / "Hypergryph Launcher" / "Launcher.exe"
            for launcher in (intl, cn):
                launcher.parent.mkdir()
                launcher.touch()
            game = intl.parent / "games" / "EndField Game" / "Endfield.exe"
            logger = Mock()
            with (
                patch.object(official_launcher, "_game_company", return_value="Gryphline"),
                patch.object(official_launcher, "_cached_launcher", return_value=str(cn)),
                patch.object(official_launcher, "_registered_launchers", return_value=[str(cn)]),
                patch.object(official_launcher, "verify_launcher"),
                patch.object(official_launcher, "_save_cached_launcher"),
            ):
                self.assertEqual(official_launcher.installed_launcher(str(game), logger), str(intl))

    def test_both_channels_accept_either_valid_official_fingerprint(self):
        for company, products in official_launcher.LAUNCHER_PRODUCTS.items():
            for status, fingerprint, accepted in (
                *((0, signer, True) for signer in official_launcher.OFFICIAL_SIGNERS),
                *((1, signer, False) for signer in official_launcher.OFFICIAL_SIGNERS),
                (0, "new", False),
            ):
                with (
                    self.subTest(company=company, status=status, signer=fingerprint),
                    patch.object(official_launcher.subprocess, "run", return_value=types.SimpleNamespace(
                        stdout=json.dumps({"status": status, "signer": fingerprint, "product": next(iter(products)),
                                           "subject": "O=GRYPH FRONTIER PTE. LTD."}))),
                ):
                    if accepted:
                        official_launcher.verify_launcher("renamed/Games.exe", company)
                    else:
                        with self.assertRaises(RuntimeError):
                            official_launcher.verify_launcher("GRYPHLINK/Games.exe", company)

    def test_mixed_candidates_and_cross_channel_cache_cannot_select_wrong_launcher(self):
        for company, products in official_launcher.LAUNCHER_PRODUCTS.items():
            correct_product = next(iter(products))
            wrong_product = next(iter(next(value for key, value in official_launcher.LAUNCHER_PRODUCTS.items() if key != company)))
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wrong, correct = str(root / "GRYPHLINK" / "Launcher.exe"), str(root / "renamed" / "Launcher.exe")

                def signed_file(*args, correct=correct, correct_product=correct_product, wrong_product=wrong_product, **kwargs):
                    product = correct_product if kwargs["env"]["OK_EF_VERIFY_LAUNCHER"] == correct else wrong_product
                    return types.SimpleNamespace(stdout=json.dumps({"status": 0, "signer": next(iter(official_launcher.OFFICIAL_SIGNERS)), "product": product}))

                with (
                    self.subTest(company=company),
                    patch.object(official_launcher, "_game_company", return_value=company),
                    patch.object(official_launcher, "_game_path_launchers", return_value=[wrong]),
                    patch.object(official_launcher, "_registered_launchers", return_value=[wrong, correct]) as registry,
                    patch.object(official_launcher, "_cached_launcher", return_value=wrong),
                    patch.object(official_launcher, "_save_cached_launcher"),
                    patch.object(official_launcher.subprocess, "run", side_effect=signed_file),
                ):
                    self.assertEqual(official_launcher.installed_launcher("relocated/Endfield.exe", Mock()), correct)
                registry.assert_called_once_with(company)

    def test_newest_games_from_the_other_channel_is_rejected(self):
        for company, products in official_launcher.LAUNCHER_PRODUCTS.items():
            correct_product = next(iter(products))
            wrong_product = next(iter(next(value for key, value in official_launcher.LAUNCHER_PRODUCTS.items() if key != company)))
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for version in ("1.0", "2.0"):
                    (root / version).mkdir()
                    (root / version / "Games.exe").touch()

                def signed_file(*args, correct_product=correct_product, wrong_product=wrong_product, **kwargs):
                    path = kwargs["env"]["OK_EF_VERIFY_LAUNCHER"]
                    product = wrong_product if Path(path).parent.name == "2.0" else correct_product
                    return types.SimpleNamespace(stdout=json.dumps({"status": 0, "signer": next(iter(official_launcher.OFFICIAL_SIGNERS)), "product": product}))

                with (
                    self.subTest(company=company),
                    patch.object(official_launcher, "_game_company", return_value=company),
                    patch.object(official_launcher, "installed_launcher", return_value=str(root / "Launcher.exe")),
                    patch.object(official_launcher.subprocess, "run", side_effect=signed_file),
                ):
                    self.assertEqual(official_launcher.find_official_games_exe("unused", Mock()), root / "1.0" / "Games.exe")

    def setUp(self):
        patcher = patch.object(official_launcher, "_game_company", return_value="Hypergryph")
        patcher.start()
        self.addCleanup(patcher.stop)
        # These tests use fabricated process handles; the real journal is
        # covered separately with an isolated temporary config directory.
        for name in ("recover_owned_parents", "register_owned_parent", "note_game_started", "forget_owned_parent"):
            patcher = patch.object(official_launcher, name)
            patcher.start()
            self.addCleanup(patcher.stop)

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
        verify.assert_called_once_with(str(launcher), "Hypergryph")
        registered.assert_called_once_with("Hypergryph")
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
        verify.assert_called_once_with(str(launcher), "Hypergryph")

    def test_inferred_launcher_takes_priority_over_stale_cache(self):
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
                patch.object(official_launcher, "verify_launcher") as verify,
            ):
                self.assertEqual(
                    official_launcher.installed_launcher("unused", logger),
                    str(launcher),
                )
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8")),
                {"path": os.path.abspath(launcher)},
            )
        verify.assert_called_once_with(str(launcher), "Hypergryph")
        logger.warning.assert_not_called()

    def test_invalid_cached_launcher_is_rejected_without_other_candidates(self):
        logger = Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / "stale" / "Launcher.exe"
            stale.parent.mkdir()
            stale.touch()
            cache = root / "official_launcher.json"
            cache.write_text(json.dumps({"path": str(stale)}), encoding="utf-8")
            with (
                patch.object(official_launcher, "get_launcher_cache_path", return_value=str(cache)),
                patch.object(official_launcher, "_registered_launchers", return_value=[]),
                patch.object(official_launcher, "_game_path_launchers", return_value=[]),
                patch.object(
                    official_launcher, "verify_launcher",
                    side_effect=RuntimeError("bad signature"),
                ) as verify,
                self.assertRaisesRegex(RuntimeError, "not found"),
            ):
                official_launcher.installed_launcher("unused", logger)
            verify.assert_called_once_with(str(stale), "Hypergryph")
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8")),
                {"path": str(stale)},
            )
        logger.warning.assert_called_once()

    def test_launch_game_preserves_arguments_without_injecting_defaults(self):
        kernel = Mock()
        logger = Mock()
        game_path = os.path.abspath("Endfield.exe")
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", return_value=456) as create,
        ):
            self.assertTrue(official_launcher.launch_game(game_path, "-force-d3d11", logger))
        create.assert_called_once_with(kernel, game_path, "-force-d3d11", 321)
        cleanup.assert_called_once_with(kernel, 321, logger, company="Hypergryph")
        cleanup.return_value.start.assert_called_once_with(456)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()  # Background watcher owns it.

    def test_running_game_skips_launcher(self):
        with (
            patch.object(official_launcher, "running_pids", return_value=[123]),
            patch.object(official_launcher, "create_temporary_parent") as prepare,
        ):
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
            prepare.assert_not_called()

    def test_game_started_during_preparation_skips_duplicate_and_closes_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], [123]]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup"),
            patch.object(official_launcher, "create_process") as create,
        ):
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
            create.assert_not_called()
        kernel.TerminateProcess.assert_called_once_with(321, 0)
        kernel.CloseHandle.assert_called_once_with(321)

    def test_create_failure_closes_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", return_value=[]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", side_effect=OSError("denied")),
            self.assertRaises(OSError),
        ):
            official_launcher.launch_game("unused", "", Mock())
        cleanup.return_value.start.assert_not_called()
        kernel.TerminateProcess.assert_called_once_with(321, 0)
        kernel.CloseHandle.assert_called_once_with(321)

    def test_watcher_unavailable_never_terminates_live_child_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", return_value=456),
            patch.object(official_launcher.psutil, "pid_exists", return_value=True),
        ):
            cleanup.return_value.start.side_effect = RuntimeError("thread unavailable")
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
        cleanup.return_value.start_exit_watch.assert_called_once_with(456)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()  # Fallback now owns the handle.

    def test_watcher_failure_and_fallback_setup_failure_waits_for_child_exit(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", return_value=456),
            patch.object(official_launcher.psutil, "pid_exists", side_effect=[True, True, False]),
            patch.object(official_launcher.time, "sleep") as sleep,
        ):
            cleanup.return_value.start.side_effect = RuntimeError("no primary thread")
            cleanup.return_value.start_exit_watch.side_effect = RuntimeError("no backup thread")
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
        sleep.assert_called_once_with(2)
        kernel.TerminateProcess.assert_called_once_with(321, 0)
        kernel.CloseHandle.assert_called_once_with(321)

    def test_watcher_unavailable_after_game_exit_discards_owned_parent(self):
        kernel = Mock()
        with (
            patch.object(official_launcher, "running_pids", side_effect=[[], []]),
            patch.object(official_launcher, "_kernel32", return_value=kernel),
            patch.object(official_launcher, "create_temporary_parent", return_value=321),
            patch.object(official_launcher, "LauncherCleanup") as cleanup,
            patch.object(official_launcher, "create_process", return_value=456),
            patch.object(official_launcher.psutil, "pid_exists", return_value=False),
        ):
            cleanup.return_value.start.side_effect = psutil.NoSuchProcess(456)
            self.assertTrue(official_launcher.launch_game("unused", "", Mock()))
        kernel.TerminateProcess.assert_called_once_with(321, 0)
        kernel.CloseHandle.assert_called_once_with(321)

    def test_find_official_games_uses_newest_signed_version(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "Launcher.exe").touch()
            for name in ("1.0.0", "2.4.9"):
                (base / name).mkdir()
                (base / name / "Games.exe").touch()
            logger = Mock()
            with (
                patch.object(official_launcher, "installed_launcher", return_value=str(base / "Launcher.exe")),
                patch.object(official_launcher, "verify_launcher") as verify,
            ):
                exe = official_launcher.find_official_games_exe("unused", logger)
            self.assertEqual(exe, base / "2.4.9" / "Games.exe")
            verify.assert_called_once_with(str(exe), "Hypergryph")

    def test_journal_failure_discards_unattached_suspended_parent(self):
        import ctypes

        kernel = Mock()

        def created(*args):
            info = ctypes.cast(args[-1], ctypes.POINTER(official_launcher._ProcessInfo))
            info.contents.dwProcessId = 111
            info.contents.hProcess = 222
            info.contents.hThread = 333
            return True

        kernel.CreateProcessW.side_effect = created
        with (
            patch.object(
                official_launcher, "find_official_games_exe", return_value=Path("C:/Launcher/2.4.9/Games.exe")
            ),
            patch.object(official_launcher, "register_owned_parent", side_effect=OSError("disk full")),
        ):
            logger = Mock()
            with self.assertRaisesRegex(OSError, "disk full"):
                official_launcher.create_temporary_parent(kernel, "Endfield.exe", logger)
        kernel.TerminateProcess.assert_called_once_with(222, 0)
        self.assertIn((222,), [call.args for call in kernel.CloseHandle.call_args_list])

    def test_temporary_parent_uses_suspended_native_creation_and_owns_handle(self):
        import ctypes

        kernel = Mock()

        def created(*args):
            info = ctypes.cast(args[-1], ctypes.POINTER(official_launcher._ProcessInfo))
            info.contents.dwProcessId = 111
            info.contents.hProcess = 222
            info.contents.hThread = 333
            return True

        kernel.CreateProcessW.side_effect = created
        with patch.object(
            official_launcher, "find_official_games_exe", return_value=Path(r"C:\\Launcher\\2.4.9\\Games.exe")
        ):
            handle = official_launcher.create_temporary_parent(kernel, "Endfield.exe", Mock())
        self.assertEqual(handle, 222)
        self.assertTrue(kernel.CreateProcessW.call_args.args[5] & 0x00000004)
        kernel.CloseHandle.assert_called_once_with(333)

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


class TestTemporaryParentLogWatcher(unittest.TestCase):
    """Both channels only terminate our parent after its own fresh success line."""

    def setUp(self):
        from src.core import launcher_cleanup

        self.cleanup_mod = launcher_cleanup
        self.folder_handle = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder_handle.cleanup)
        folder = self.folder_handle.name
        patcher = patch.object(launcher_cleanup.shell, "SHGetKnownFolderPath", return_value=folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logs = Path(folder) / "Hypergryph" / "Endfield" / "sdklogs"
        self.logs.mkdir(parents=True)
        self.kernel = Mock()
        self.kernel.GetProcessId.return_value = 123
        self.logger = Mock()
        journal_patcher = patch.object(launcher_cleanup, "forget_owned_parent")
        journal_patcher.start()
        self.addCleanup(journal_patcher.stop)

    def _new_cleanup(self):
        return self.cleanup_mod.LauncherCleanup(self.kernel, 999, self.logger)

    def _write(self, name, text, cleanup, age=0):
        from datetime import timedelta

        stamp = (cleanup.started + timedelta(seconds=age)).strftime("%m-%d %H:%M:%S.%f")[:-3]
        with (self.logs / name).open("ab") as out:
            out.write(f"[{stamp}] [info] {text}\n".encode())

    def _watch(self, cleanup, game=None, ticks=(0, 0, 121)):
        game = game or Mock(is_running=Mock(return_value=True))
        with (
            patch.object(cleanup, "_parent_running", return_value=True),
            patch.object(cleanup, "_wait_for_game_exit") as wait_for_exit,
            patch.object(self.cleanup_mod.time, "monotonic", side_effect=ticks),
            patch.object(self.cleanup_mod.time, "sleep"),
        ):
            cleanup._watch(game)
        return wait_for_exit


    def test_international_reads_only_its_own_company_log(self):
        self.logs = self.logs.parents[2] / "Gryphline" / "Endfield" / "sdklogs"
        self.logs.mkdir(parents=True)
        cleanup = self.cleanup_mod.LauncherCleanup(self.kernel, 999, self.logger, company="Gryphline")
        self.assertEqual(cleanup.check_log.path, self.logs / "OneChannelLC.log")
        cn_log = self.logs.parents[2] / "Hypergryph" / "Endfield" / "sdklogs" / "OneChannelLC.log"
        stamp = cleanup.started.strftime("%m-%d %H:%M:%S.%f")[:-3]
        cn_log.write_text(f"[{stamp}] Parent process check success:123\n", encoding="utf-8")
        self._watch(cleanup)
        self.kernel.TerminateProcess.assert_not_called()
        self.kernel.reset_mock()
        self._write("OneChannelLC.log", "Parent process check success:123", cleanup)
        self._watch(cleanup, ticks=(0, 0))
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_parent_success_releases_owned_parent_without_waiting_for_sdk_init(self):
        cleanup = self._new_cleanup()
        self._write("OneChannelLC.log", "Parent process check success:123", cleanup)
        self._watch(cleanup, ticks=(0, 0))
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_wrong_pid_or_failed_parent_check_never_terminates(self):
        for check, sdk in (
            ("Parent process check success:456", "OnU8V2InitCallback status :0"),
            ("Parent process check failed:123", "OnU8V2InitCallback status :0"),
        ):
            with self.subTest(check=check, sdk=sdk):
                for name in ("OneChannelLC.log", "u8sdk_pc.log"):
                    (self.logs / name).unlink(missing_ok=True)
                self.kernel.reset_mock()
                cleanup = self._new_cleanup()
                self._write("OneChannelLC.log", check, cleanup)
                self._write("u8sdk_pc.log", sdk, cleanup)
                self._watch(cleanup)
                self.kernel.TerminateProcess.assert_not_called()
                self.kernel.CloseHandle.assert_called_once_with(999)

    def test_old_records_cannot_authorize_cleanup(self):
        cleanup = self._new_cleanup()
        self._write("OneChannelLC.log", "Parent process check success:123", cleanup, age=-3)
        self._write("u8sdk_pc.log", "OnU8V2InitCallback status :0", cleanup)
        self._watch(cleanup)
        self.kernel.TerminateProcess.assert_not_called()

    def test_game_exit_discards_owned_parent_and_log_error_preserves_live_game(self):
        cleanup = self._new_cleanup()
        self._watch(cleanup, game=Mock(is_running=Mock(return_value=False)))
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)
        self.kernel.reset_mock()
        cleanup = self._new_cleanup()
        with patch.object(cleanup.check_log, "read", side_effect=PermissionError("denied")):
            wait = self._watch(cleanup)
        self.kernel.TerminateProcess.assert_not_called()
        wait.assert_called_once()
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_timeout_keeps_parent_for_live_game_then_discards_on_exit(self):
        cleanup = self._new_cleanup()
        game = Mock(is_running=Mock(return_value=True))
        wait = self._watch(cleanup, game=game)
        self.kernel.TerminateProcess.assert_not_called()
        wait.assert_called_once_with(game)
        self.kernel.reset_mock()
        game.is_running.side_effect = [True, False]
        with (
            patch.object(cleanup, "_parent_running", return_value=True),
            patch.object(self.cleanup_mod.time, "sleep") as sleep,
        ):
            cleanup._wait_for_game_exit(game)
        sleep.assert_called_once_with(2)
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)

    def test_fallback_thread_failure_watches_inline_and_releases_owned_handle(self):
        cleanup = self._new_cleanup()
        game = Mock(is_running=Mock(return_value=False))
        with (
            patch.object(self.cleanup_mod.psutil, "Process", return_value=game),
            patch.object(self.cleanup_mod.threading, "Thread") as worker,
            patch.object(cleanup, "_parent_running", return_value=True),
            patch.object(self.cleanup_mod.time, "monotonic", side_effect=[0, 0]),
        ):
            worker.return_value.start.side_effect = RuntimeError("thread creation failed")
            cleanup.start_exit_watch(456)
        worker.assert_called_once()
        self.assertEqual(worker.call_args.kwargs["name"], "endfield-launcher-fallback")
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_failed_parent_status_query_waits_for_game_exit_before_termination(self):
        cleanup = self._new_cleanup()
        game = Mock()
        game.is_running.side_effect = [True, False]

        def terminate_after_exit(*args):
            self.assertEqual(game.is_running.call_count, 2)
            return True

        self.kernel.TerminateProcess.side_effect = terminate_after_exit
        with (
            patch.object(cleanup, "_parent_running", side_effect=OSError("Cannot query parent")) as status_query,
            patch.object(self.cleanup_mod.time, "monotonic", side_effect=[0, 0]),
            patch.object(self.cleanup_mod.time, "sleep") as sleep,
        ):
            cleanup._watch(game)

        self.assertEqual(status_query.call_count, 2)
        sleep.assert_called_once_with(2)
        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_failed_parent_status_query_preserves_parent_when_game_status_unknown(self):
        cleanup = self._new_cleanup()
        game = Mock()
        game.is_running.side_effect = OSError("Cannot query game")
        with (
            patch.object(cleanup, "_parent_running", side_effect=OSError("Cannot query parent")),
            patch.object(self.cleanup_mod.time, "monotonic", side_effect=[0, 0]),
        ):
            cleanup._watch(game)

        self.kernel.TerminateProcess.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(999)
        self.logger.warning.assert_called()

    def test_failed_parent_status_query_logs_termination_failure_and_closes_handle(self):
        cleanup = self._new_cleanup()
        self.kernel.TerminateProcess.return_value = False
        game = Mock(is_running=Mock(return_value=False))
        with (
            patch.object(cleanup, "_parent_running", side_effect=OSError("Cannot query parent")),
            patch.object(self.cleanup_mod.time, "monotonic", side_effect=[0, 0]),
        ):
            cleanup._watch(game)

        self.kernel.TerminateProcess.assert_called_once_with(999, 0)
        self.kernel.CloseHandle.assert_called_once_with(999)
        self.logger.warning.assert_called()

    def test_tail_skips_old_partial_and_survives_rotation(self):
        path = self.logs / "OneChannelLC.log"
        path.write_bytes(b"previous complete\nold half")
        tail = self.cleanup_mod._LogTail(path)
        with path.open("ab") as stream:
            stream.write(b" line\nnew complete\n")
        self.assertEqual(tail.read(), [b"new complete"])
        path.rename(path.with_suffix(".old"))
        path.write_bytes(b"next complete\n")
        self.assertEqual(tail.read(), [b"next complete"])


class TestTemporaryParentRecovery(unittest.TestCase):
    """Recovery must never terminate a user-owned launcher or a live game."""

    def setUp(self):
        from src.core import temporary_parent_recovery as recovery

        self.recovery = recovery
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        patcher = patch.object(recovery, "_records_dir", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logger = Mock()
        self.parent_pid = 231
        self.parent_started = 100.0
        self.executable = os.path.normcase(os.path.abspath("Games.exe"))

    def _write(self, **changes):
        record = {
            "version": 1,
            "parent_pid": self.parent_pid,
            "parent_created": self.parent_started,
            "parent_exe": self.executable,
            "owner_pid": 1000,
            "owner_created": 10.0,
        }
        record.update(changes)
        path = self.path / f"{self.parent_pid}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def _parent(self):
        parent = Mock()
        parent.is_running.return_value = True
        parent.create_time.return_value = self.parent_started
        parent.exe.return_value = self.executable
        parent.children.return_value = []
        return parent

    def _owner(self, alive=False):
        owner = Mock(pid=1000)
        owner.is_running.return_value = alive
        owner.create_time.return_value = 10.0
        return owner

    def _recover(self, parent, owner=None, game=None):
        owner = owner or self._owner()
        mapping = {self.parent_pid: parent, 1000: owner}
        if game:
            mapping[501] = game

        def get_process(pid):
            if pid not in mapping:
                raise psutil.NoSuchProcess(pid)
            return mapping[pid]

        with patch.object(self.recovery.psutil, "Process", side_effect=get_process):
            self.recovery.recover_owned_parents(self.logger)

    def test_register_and_forget_parent_identity(self):
        parent, owner = self._parent(), self._owner(alive=True)
        with (
            patch.object(self.recovery.psutil, "Process", side_effect=lambda pid: parent if pid == 231 else owner),
            patch.object(self.recovery.os, "getpid", return_value=1000),
        ):
            self.recovery.register_owned_parent(231)
        record = json.loads((self.path / "231.json").read_text(encoding="utf-8"))
        self.assertEqual(record["parent_created"], 100.0)
        self.assertEqual(record["parent_exe"], self.executable)
        self.assertEqual(record["owner_pid"], 1000)
        self.recovery.forget_owned_parent(231, self.logger)
        self.assertFalse((self.path / "231.json").exists())

    def test_register_uses_reported_image_path_for_recovery(self):
        parent, owner = self._parent(), self._owner(alive=True)
        reported = os.path.abspath("Resolved Launcher/2.4.9/Games.exe")
        parent.exe.return_value = reported
        with (
            patch.object(self.recovery.psutil, "Process", side_effect=lambda pid: parent if pid == 231 else owner),
            patch.object(self.recovery.os, "getpid", return_value=1000),
        ):
            self.recovery.register_owned_parent(231)
        path = self.path / "231.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["parent_exe"], os.path.normcase(reported))
        self._recover(parent)
        parent.terminate.assert_called_once()
        self.assertFalse(path.exists())

    def test_register_rejects_unexpected_image_name(self):
        parent = self._parent()
        parent.exe.return_value = os.path.abspath("Launcher.exe")
        with (
            patch.object(self.recovery.psutil, "Process", return_value=parent),
            self.assertRaisesRegex(RuntimeError, "Cannot identify"),
        ):
            self.recovery.register_owned_parent(231)
        self.assertFalse((self.path / "231.json").exists())

    def test_abandoned_parent_without_game_is_recovered(self):
        path = self._write()
        parent = self._parent()
        self._recover(parent)
        parent.terminate.assert_called_once()
        self.assertFalse(path.exists())

    def test_current_owner_alive_is_preserved(self):
        path = self._write()
        parent = self._parent()
        self._recover(parent, owner=self._owner(alive=True))
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_recorded_live_game_preserves_parent(self):
        path = self._write(game_pid=501, game_created=50.0)
        parent = self._parent()
        game = Mock()
        game.is_running.return_value = True
        game.create_time.return_value = 50.0
        self._recover(parent, game=game)
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_live_child_preserves_parent_even_if_game_record_was_not_written(self):
        path = self._write()
        parent = self._parent()
        child = Mock()
        child.is_running.return_value = True
        parent.children.return_value = [child]
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_reused_parent_pid_cannot_terminate_unrelated_process(self):
        path = self._write()
        parent = self._parent()
        parent.create_time.return_value = 200.0
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertFalse(path.exists())

    def test_changed_parent_executable_cannot_terminate_unrelated_launcher(self):
        path = self._write()
        parent = self._parent()
        parent.exe.return_value = os.path.normcase(os.path.abspath("Another/Games.exe"))
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertFalse(path.exists())

    def test_parent_access_denied_preserves_record(self):
        path = self._write()
        parent = self._parent()
        parent.exe.side_effect = psutil.AccessDenied(self.parent_pid)
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_recorded_game_status_unknown_preserves_parent(self):
        path = self._write(game_pid=501, game_created=50.0)
        parent = self._parent()
        game = Mock()
        game.is_running.side_effect = psutil.AccessDenied(501)
        self._recover(parent, game=game)
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_dead_parent_drops_stale_record(self):
        path = self._write()
        parent = self._parent()
        parent.is_running.return_value = False
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertFalse(path.exists())

    def test_invalid_record_is_never_used_to_terminate(self):
        path = self._write(parent_exe="C:/NotTheOfficialExecutable.exe")
        parent = self._parent()
        self._recover(parent)
        parent.terminate.assert_not_called()
        self.assertTrue(path.exists())

    def test_game_identity_is_persisted(self):
        path = self._write()
        game = Mock()
        game.create_time.return_value = 50.0
        with patch.object(self.recovery.psutil, "Process", return_value=game):
            self.recovery.note_game_started(self.parent_pid, 501, self.logger)
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["game_pid"], 501)
        self.assertEqual(record["game_created"], 50.0)


if __name__ == "__main__":
    unittest.main()
