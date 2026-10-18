import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from src.core import launcher_cleanup


class TestLogTail(unittest.TestCase):
    def test_skips_history_and_waits_for_complete_new_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sdk.log"
            path.write_bytes(b"old\nincomplete")
            tail = launcher_cleanup._LogTail(path)
            with path.open("ab") as stream:
                stream.write(b" old line\nnew")
            self.assertEqual(tail.read(), [])
            with path.open("ab") as stream:
                stream.write(b" success\n")
            self.assertEqual(tail.read(), [b"new success"])
            self.assertEqual(tail.read(), [])

    def test_missing_file_can_be_created_and_rotated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sdk.log"
            tail = launcher_cleanup._LogTail(path)
            self.assertEqual(tail.read(), [])
            path.write_bytes(b"first\n")
            self.assertEqual(tail.read(), [b"first"])
            path.rename(path.with_suffix(".old"))
            path.write_bytes(b"second\n")
            self.assertEqual(tail.read(), [b"second"])

    def test_truncation_and_regrowth_reset_partial_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sdk.log"
            path.write_bytes(b"old log\n")
            tail = launcher_cleanup._LogTail(path)
            with path.open("ab") as stream:
                stream.write(b"partial")
            self.assertEqual(tail.read(), [])
            path.write_bytes(b"replacement longer than previous offset\n")
            self.assertEqual(tail.read(), [b"replacement longer than previous offset"])

    def test_oversized_partial_line_is_not_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sdk.log"
            tail = launcher_cleanup._LogTail(path)
            path.write_bytes(b"x" * (70 * 1024))
            self.assertEqual(tail.read(), [])
            self.assertEqual(tail.pending, b"")
            with path.open("ab") as stream:
                stream.write(b"\nvalid\n")
            self.assertEqual(tail.read(), [b"valid"])


class TestLauncherCleanup(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(launcher_cleanup.shell, "SHGetKnownFolderPath", return_value=self.directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = Path(self.directory.name) / "Hypergryph/Endfield/sdklogs"
        self.folder.mkdir(parents=True)
        self.kernel = Mock()
        self.kernel.GetProcessId.return_value = 123
        self.logger = Mock()

    def line(self, text, age=0):
        stamp = (self.cleanup.started + timedelta(seconds=age)).strftime("%m-%d %H:%M:%S.%f")[:-3]
        return f"[{stamp}] [info] {text}\n".encode()

    def append(self, name, line):
        with (self.folder / name).open("ab") as stream:
            stream.write(line)

    def watch(self, ticks=(0, 0, 1), game=None):
        with (
            patch.object(self.cleanup, "_parent_running", return_value=True),
            patch.object(self.cleanup, "_request_exit") as close,
            patch.object(launcher_cleanup.time, "monotonic", side_effect=ticks),
            patch.object(launcher_cleanup.time, "sleep"),
        ):
            self.cleanup._watch(game or Mock(is_running=Mock(return_value=True)))
        return close

    def test_only_current_parent_success_and_sdk_ready_trigger_exit(self):
        self.append("OneChannelLC.log", b"historical Parent process check success:123\n")
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        self.append("OneChannelLC.log", self.line("Parent process check success:123"))
        self.append("u8sdk_pc.log", self.line("OnU8V2InitCallback status :0"))
        self.watch().assert_called_once_with()
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_wrong_pid_failure_history_and_incomplete_lines_do_not_close(self):
        for check, sdk, age in (
            ("Parent process check success:1234", "OnU8V2InitCallback status :0", 0),
            ("Parent process check success:123", "OnU8V2InitCallback status :1", 0),
            ("Parent process check success:123", "OnU8V2InitCallback status :0", -60),
        ):
            with self.subTest(check=check, sdk=sdk, age=age):
                self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
                self.append("OneChannelLC.log", self.line(check, age))
                self.append("u8sdk_pc.log", self.line(sdk, age))
                self.watch(ticks=(0, 0, 121)).assert_not_called()
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        self.append("OneChannelLC.log", self.line("Parent process check success:123").rstrip(b"\n"))
        self.append("u8sdk_pc.log", self.line("OnU8V2InitCallback status :0"))
        self.watch(ticks=(0, 0, 121)).assert_not_called()

    def test_both_signals_can_arrive_on_separate_polls(self):
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        self.append("OneChannelLC.log", self.line("Parent process check success:123"))
        with (
            patch.object(self.cleanup, "_parent_running", return_value=True),
            patch.object(self.cleanup, "_request_exit") as close,
            patch.object(launcher_cleanup.time, "monotonic", side_effect=(0, 0, 1)),
            patch.object(
                launcher_cleanup.time,
                "sleep",
                side_effect=lambda _: self.append("u8sdk_pc.log", self.line("OnU8V2InitCallback status :0")),
            ),
        ):
            self.cleanup._watch(Mock(is_running=Mock(return_value=True)))
        close.assert_called_once_with()

    def test_game_exit_and_log_read_failure_preserve_launcher(self):
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        self.watch(game=Mock(is_running=Mock(return_value=False))).assert_not_called()
        with patch.object(self.cleanup.check_log, "read", side_effect=PermissionError("denied")):
            self.watch().assert_not_called()
        self.assertEqual(self.kernel.CloseHandle.call_count, 2)

    def test_launcher_exit_preserves_game_and_releases_handle(self):
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        with (
            patch.object(self.cleanup, "_parent_running", return_value=False),
            patch.object(self.cleanup, "_request_exit") as close,
        ):
            self.cleanup._watch(Mock(is_running=Mock(return_value=True)))
        close.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(999)

    def test_start_failure_does_not_claim_handle_ownership(self):
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        with (
            patch.object(launcher_cleanup.psutil, "Process"),
            patch.object(launcher_cleanup.threading, "Thread") as thread,
        ):
            thread.return_value.start.side_effect = RuntimeError("unavailable")
            with self.assertRaises(RuntimeError):
                self.cleanup.start(456)
        self.kernel.CloseHandle.assert_not_called()

    def test_quit_targets_only_verified_launcher_gui_thread(self):
        self.cleanup = launcher_cleanup.LauncherCleanup(self.kernel, 999, self.logger)
        with (
            patch.object(
                launcher_cleanup.win32gui, "EnumWindows", side_effect=lambda cb, _: [cb(w, None) for w in (1, 2)]
            ),
            patch.object(launcher_cleanup.win32process, "GetWindowThreadProcessId", side_effect=((10, 456), (20, 123))),
            patch.object(launcher_cleanup.win32gui, "GetWindow", return_value=0),
            patch.object(launcher_cleanup.win32gui, "GetWindowRect", return_value=(0, 0, 800, 600)),
            patch.object(self.cleanup, "_parent_running", side_effect=(True, False, False)),
            patch.object(launcher_cleanup.win32api, "PostThreadMessage") as post,
        ):
            self.cleanup._request_exit()
        post.assert_called_once_with(20, launcher_cleanup.win32con.WM_QUIT, 0, 0)

    def test_year_rollover_and_old_dates(self):
        started = datetime(2026, 12, 31, 23, 59, 59)
        self.assertTrue(launcher_cleanup._is_current(b"[01-01 00:00:01.000] ready", started))
        self.assertFalse(launcher_cleanup._is_current(b"[12-31 23:59:58.000] ready", started))
        self.assertFalse(launcher_cleanup._is_current(b"[02-30 00:00:00.000] ready", started))

    @unittest.skipUnless(sys.platform == "win32", "Windows GUI process lifecycle")
    def test_native_quit_exits_parent_but_keeps_child_alive(self):
        from src.core import official_launcher

        # Disposable hidden window and child only; never use the real launcher/game.
        receipt = Path(self.directory.name) / "window.ready"
        code = (
            "import os,sys; from pathlib import Path; "
            "dlls=os.add_dll_directory(sys.argv[2]); import win32gui,win32con; "
            "c=win32gui.WNDCLASS(); c.lpszClassName='OkEfLauncherCleanupTest'; "
            "c.lpfnWndProc=win32gui.DefWindowProc; atom=win32gui.RegisterClass(c); "
            "w=win32gui.CreateWindow(atom,'Launcher cleanup test',win32con.WS_OVERLAPPEDWINDOW,"
            "0,0,400,300,0,0,0,None); "
            "Path(sys.argv[1]).write_text('ready'); win32gui.PumpMessages()"
        )
        # Use the underlying interpreter, avoiding the venv's redirector process.
        parent = subprocess.Popen(
            [
                sys._base_executable,
                "-c",
                code,
                str(receipt),
                str(Path(sys.prefix) / "Lib/site-packages/pywin32_system32"),
            ],
            cwd=os.getcwd(),
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        child = None
        handle = None
        try:
            deadline = time.monotonic() + 10
            while not receipt.exists() and parent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not receipt.exists():
                parent.terminate()
                self.fail(parent.communicate(timeout=5)[1].decode(errors="replace"))
            kernel = official_launcher._kernel32()
            handle = official_launcher._check(kernel.OpenProcess(0x0080 | 0x1000, False, parent.pid))
            self.cleanup = launcher_cleanup.LauncherCleanup(kernel, handle, self.logger)
            arguments = subprocess.list2cmdline(["-c", "import time; time.sleep(30)"])
            child = psutil.Process(official_launcher.create_process(kernel, sys._base_executable, arguments, handle))
            self.assertEqual(child.ppid(), parent.pid)
            self.append("OneChannelLC.log", self.line(f"Parent process check success:{parent.pid}"))
            self.append("u8sdk_pc.log", self.line("OnU8V2InitCallback status :0"))
            self.cleanup._watch(child)
            handle = None  # _watch released it, including on a failure.
            self.assertEqual(parent.wait(timeout=5), 0)
            self.assertTrue(child.is_running())
            self.logger.warning.assert_not_called()
        finally:
            if child is not None and child.is_running():
                child.kill()
                child.wait(timeout=5)
            if handle:
                kernel.CloseHandle(handle)
            if parent.poll() is None:
                parent.kill()
            parent.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
