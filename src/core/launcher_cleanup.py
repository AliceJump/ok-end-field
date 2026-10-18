"""Release the verified launcher after this game's SDK startup succeeds."""

from __future__ import annotations

import os
import re
import threading
import time
from ctypes import byref, wintypes
from datetime import datetime
from pathlib import Path

import psutil
import win32api
import win32con
import win32gui
import win32process
from win32com.shell import shell, shellcon

_LOG_TIME = re.compile(r"^\[(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")
_SDK_READY = re.compile(rb"OnU8V2InitCallback status\s*:\s*0\s*$")
_TIMEOUT = 120
_INTERVAL = 0.25


class _LogTail:
    """Read complete new lines, including after replacement or truncation."""

    def __init__(self, path: Path):
        self.path = path
        self.identity = None
        self.offset = 0
        self.anchor = b""
        self.pending = b""
        self.discard = False
        try:
            with path.open("rb") as stream:
                self._remember(stream, stream.seek(0, 2))
                self.discard = bool(self.anchor and not self.anchor.endswith(b"\n"))
        except FileNotFoundError:
            pass

    def _remember(self, stream, offset):
        stat = os.fstat(stream.fileno())
        self.identity = (stat.st_dev, stat.st_ino)
        self.offset = offset
        stream.seek(max(0, offset - 64))
        self.anchor = stream.read(min(offset, 64))

    def read(self) -> list[bytes]:
        try:
            stream = self.path.open("rb")
        except FileNotFoundError:
            self.identity = None
            self.offset = 0
            self.anchor = self.pending = b""
            self.discard = False
            return []
        with stream:
            stat = os.fstat(stream.fileno())
            stream.seek(max(0, self.offset - len(self.anchor)))
            anchor = stream.read(len(self.anchor))
            if (stat.st_dev, stat.st_ino) != self.identity or stat.st_size < self.offset or anchor != self.anchor:
                self.offset = 0
                self.pending = b""
                self.discard = False
            stream.seek(self.offset)
            data = self.pending + stream.read(256 * 1024)
            offset = stream.tell()
            self._remember(stream, offset)
        *lines, self.pending = data.split(b"\n")
        if self.discard and lines:
            lines.pop(0)
            self.discard = False
        if len(self.pending) > 64 * 1024:
            self.pending = b""
            self.discard = True
        return lines


def _is_current(line: bytes, started: datetime) -> bool:
    match = _LOG_TIME.match(line.decode("utf-8", errors="replace"))
    if not match:
        return False
    # Logs omit the year. Trying adjacent years also covers New Year's midnight.
    for year in (started.year - 1, started.year, started.year + 1):
        try:
            event = datetime.strptime(f"{year}-{match[1]}", "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            continue
        if 0 <= (event - started).total_seconds() <= _TIMEOUT:
            return True
    return False


class LauncherCleanup:
    """Snapshot logs before creation; own the parent handle only after start()."""

    def __init__(self, kernel, parent, logger):
        self.kernel = kernel
        self.parent = parent
        self.logger = logger
        self.parent_pid = kernel.GetProcessId(parent)
        if not self.parent_pid:
            raise OSError("Cannot identify the verified launcher parent")
        folder = Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_LocalAppDataLow))
        folder = folder / "Hypergryph" / "Endfield" / "sdklogs"
        self.check_log = _LogTail(folder / "OneChannelLC.log")
        self.sdk_log = _LogTail(folder / "u8sdk_pc.log")
        now = datetime.now()
        self.started = now.replace(microsecond=now.microsecond // 1000 * 1000)

    def start(self, game_pid: int) -> None:
        game = psutil.Process(game_pid)
        game.create_time()
        threading.Thread(
            target=self._watch,
            args=(game,),
            name="endfield-launcher-cleanup",
            daemon=True,
        ).start()

    def _parent_running(self) -> bool:
        exit_code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.parent, byref(exit_code)):
            raise OSError("Cannot query the verified launcher parent")
        return exit_code.value == 259  # STILL_ACTIVE

    def _request_exit(self) -> None:
        windows = []

        def collect(hwnd, _):
            thread, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == self.parent_pid and not win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                windows.append(((right - left) * (bottom - top), thread))
            return True

        win32gui.EnumWindows(collect, None)
        if not windows or not self._parent_running():
            self.logger.warning("Cannot find the verified launcher's main window; leaving it open")
            return
        # WM_CLOSE can hide the launcher to its tray. WM_QUIT exits its GUI loop
        # without terminating the process tree (which also contains the game).
        win32api.PostThreadMessage(max(windows)[1], win32con.WM_QUIT, 0, 0)
        deadline = time.monotonic() + 5
        while self._parent_running() and time.monotonic() < deadline:
            time.sleep(_INTERVAL)
        if self._parent_running():
            self.logger.warning("Official launcher did not exit after the quit request; leaving it open")
        else:
            self.logger.info("Official launcher exited after game startup verification")

    def _watch(self, game) -> None:
        try:
            checked = ready = False
            success = re.compile(rb"Parent process check success:" + str(self.parent_pid).encode() + rb"\s*$")
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                if not game.is_running() or not self._parent_running():
                    return
                for line in self.check_log.read():
                    if _is_current(line, self.started) and success.search(line):
                        checked = True
                for line in self.sdk_log.read():
                    if _is_current(line, self.started) and _SDK_READY.search(line):
                        ready = True
                if checked and ready:
                    self.logger.info(f"Game startup verified; closing official launcher PID {self.parent_pid}")
                    self._request_exit()
                    return
                time.sleep(_INTERVAL)
            self.logger.warning("Game startup success logs not observed within 120 seconds; leaving launcher open")
        except Exception as error:
            self.logger.warning(f"Cannot close official launcher after startup; leaving it open: {error}")
        finally:
            self.kernel.CloseHandle(self.parent)
