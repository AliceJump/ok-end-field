"""Stop only our temporary suspended launcher after verified SDK startup."""

from __future__ import annotations

import os
import re
import threading
import time
from ctypes import byref, wintypes
from datetime import datetime
from pathlib import Path

import psutil
from win32com.shell import shell, shellcon

from src.core.temporary_parent_recovery import forget_owned_parent

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
    """Snapshot both SDK logs before game creation, then own the temporary process handle."""

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

    def start_exit_watch(self, game_pid: int) -> None:
        """Recover watcher ownership when the primary monitor could not start."""
        game = psutil.Process(game_pid)
        game.create_time()
        try:
            threading.Thread(
                target=self._watch,
                args=(game,),
                name="endfield-launcher-fallback",
                daemon=True,
            ).start()
        except Exception as error:
            # A thread-start failure must not leave an unowned suspended parent.
            # In this exceptional case, prefer blocking until verified startup
            # or game exit to silently abandoning the process handle.
            self.logger.warning(f"Cannot start fallback launcher watcher; waiting in caller: {error}")
            self._watch(game)

    def _parent_running(self) -> bool:
        exit_code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.parent, byref(exit_code)):
            raise OSError("Cannot query the verified launcher parent")
        return exit_code.value == 259  # STILL_ACTIVE

    def _request_exit(self) -> None:
        # This handle belongs exclusively to the Games.exe instance we created.
        # Do not kill by executable name or terminate an unrelated launcher tree.
        if not self._parent_running():
            forget_owned_parent(self.parent_pid, self.logger)
            return
        if not self.kernel.TerminateProcess(self.parent, 0):
            raise OSError("Could not terminate the owned temporary Games.exe")
        forget_owned_parent(self.parent_pid, self.logger)
        self.logger.info(f"Requested termination of temporary Games.exe PID {self.parent_pid}")

    def _seen(self, tail: _LogTail, pattern) -> bool:
        """Consume new log records and detect a current matching line."""
        return any(_is_current(line, self.started) and pattern.search(line) for line in tail.read())

    def _wait_for_game_exit(self, game) -> None:
        """Keep our parent alive while the game runs, then discard it."""
        while self._parent_running():
            if not game.is_running():
                self.logger.info(f"Game exited; discarding temporary Games.exe PID {self.parent_pid}")
                self._request_exit()
                return
            time.sleep(2)

    def _watch_startup(self, game) -> None:
        """Observe SDK readiness, retaining the parent until readiness or game exit."""
        checked = ready = False
        success = re.compile(rb"Parent process check success:" + str(self.parent_pid).encode() + rb"\s*$")
        deadline = time.monotonic() + _TIMEOUT
        while time.monotonic() < deadline:
            if not self._parent_running():
                return
            if not game.is_running():
                self.logger.info(f"Game exited; discarding temporary Games.exe PID {self.parent_pid}")
                self._request_exit()
                return
            checked = self._seen(self.check_log, success) or checked
            ready = self._seen(self.sdk_log, _SDK_READY) or ready
            if checked and ready:
                self.logger.info(f"Game startup verified; terminating temporary Games.exe PID {self.parent_pid}")
                self._request_exit()
                return
            time.sleep(_INTERVAL)
        self.logger.warning(
            "Game startup not verified in 120 seconds; keeping temporary Games.exe until the game exits"
        )
        self._wait_for_game_exit(game)

    def _watch(self, game) -> None:
        try:
            self._watch_startup(game)
        except Exception as error:
            self.logger.warning(
                f"Cannot verify SDK startup; retaining temporary Games.exe while the game runs: {error}"
            )
            try:
                self._wait_for_game_exit(game)
            except Exception as wait_error:
                self.logger.warning(f"Cannot monitor game exit for owned Games.exe PID {self.parent_pid}: {wait_error}")
                # Parent status queries may be broken. We still own the process
                # handle, so wait for the game to exit before attempting cleanup.
                try:
                    while game.is_running():
                        time.sleep(2)
                    if self.kernel.TerminateProcess(self.parent, 0):
                        forget_owned_parent(self.parent_pid, self.logger)
                    else:
                        self.logger.warning(
                            f"Could not terminate owned temporary Games.exe PID {self.parent_pid} after game exit"
                        )
                except Exception as fallback_error:
                    self.logger.warning(
                        f"Cannot discard owned temporary Games.exe PID {self.parent_pid}: {fallback_error}"
                    )
        finally:
            self.kernel.CloseHandle(self.parent)
