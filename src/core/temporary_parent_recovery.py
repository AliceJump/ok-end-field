"""Recover only suspended launcher parents recorded as created by OK-EF.

Records are not proof by PID alone: every destructive action rechecks the
process creation time and full executable path, plus any still-running game.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import psutil

from src.core.paths import config_path

_RECORD_VERSION = 1
_TIME_TOLERANCE = 0.02


def _records_dir() -> Path:
    return Path(config_path("owned_temporary_launchers"))


def _record_path(parent_pid: int) -> Path:
    return _records_dir() / f"{parent_pid}.json"


def _same_process(process: psutil.Process, started: float, executable: str) -> bool:
    return (
        process.is_running()
        and abs(process.create_time() - started) <= _TIME_TOLERANCE
        and os.path.normcase(os.path.abspath(process.exe())) == executable
    )


def _write_record(path: Path, data: dict) -> None:
    """Atomically publish a durable record, never a partially written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=".owned-", suffix=".tmp", delete=False
        ) as stream:
            tmp = Path(stream.name)
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def register_owned_parent(pid: int) -> None:
    """Persist a uniquely identified owned parent before any game is launched."""
    proc = psutil.Process(pid)
    started = proc.create_time()
    # Use the OS-reported image path for both registration and recovery; the
    # CreateProcessW input can use a different spelling through a path alias.
    full_path = os.path.normcase(os.path.abspath(proc.exe()))
    if not _same_process(proc, started, full_path) or Path(full_path).name.casefold() != "games.exe":
        raise RuntimeError("Cannot identify the newly created temporary Games.exe")
    owner = psutil.Process(os.getpid())
    _write_record(
        _record_path(pid),
        {
            "version": _RECORD_VERSION,
            "parent_pid": pid,
            "parent_created": started,
            "parent_exe": full_path,
            "owner_pid": owner.pid,
            "owner_created": owner.create_time(),
        },
    )


def note_game_started(parent_pid: int, game_pid: int, logger) -> None:
    """Record the child where available; the parent-child check is still mandatory."""
    path = _record_path(parent_pid)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("parent_pid") != parent_pid:
            return
        record["game_pid"] = game_pid
        try:
            record["game_created"] = psutil.Process(game_pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # The child may have exited before we could record its start time.
            record["game_created"] = None
        _write_record(path, record)
    except (OSError, ValueError, TypeError) as error:
        logger.warning(f"Could not update temporary launcher game record for PID {parent_pid}: {error}")


def forget_owned_parent(pid: int, logger) -> None:
    """Retire the record once this instance has discarded its owned parent."""
    try:
        _record_path(pid).unlink(missing_ok=True)
    except OSError as error:
        logger.warning(f"Cannot retire temporary launcher record for PID {pid}: {error}")


def _owner_still_alive(record: dict) -> bool:
    """Never collect a temporary parent managed by a live OK-EF process."""
    try:
        owner = psutil.Process(record["owner_pid"])
        return (
            owner.is_running()
            and abs(owner.create_time() - record["owner_created"]) <= _TIME_TOLERANCE
        )
    except psutil.NoSuchProcess:
        return False


def _recorded_game_alive(record: dict) -> bool:
    game_pid = record.get("game_pid")
    if not game_pid:
        return False
    try:
        game = psutil.Process(game_pid)
        created = record.get("game_created")
        return game.is_running() and (
            created is None or abs(game.create_time() - created) <= _TIME_TOLERANCE
        )
    except psutil.NoSuchProcess:
        return False


def _recover_record(path: Path, logger) -> None:
    """Validate one journal identity and reclaim it only after its users exit."""
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("version") != _RECORD_VERSION:
        logger.warning(f"Unknown temporary launcher record: {path}")
        return
    pid = record["parent_pid"]
    started = record["parent_created"]
    executable = record["parent_exe"]
    if (
        not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
        or not isinstance(started, (int, float)) or isinstance(started, bool)
        or not isinstance(executable, str)
        or not executable
        or path.name != f"{pid}.json"
        or os.path.normcase(os.path.abspath(executable)) != executable
        or Path(executable).name.casefold() != "games.exe"
    ):
        logger.warning(f"Invalid temporary launcher record: {path}")
        return

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        path.unlink(missing_ok=True)
        return
    if not _same_process(parent, started, executable):
        # PID reused, or a different process now occupies this identity.
        path.unlink(missing_ok=True)
        return
    if _owner_still_alive(record) or _recorded_game_alive(record):
        return
    # If the child metadata was never saved, children() still protects
    # the small interval between process creation and journal update.
    if any(child.is_running() for child in parent.children(recursive=True)):
        return
    if not _same_process(parent, started, executable):
        return
    parent.terminate()
    path.unlink(missing_ok=True)
    logger.info(f"Recovered abandoned temporary Games.exe PID {pid}")


def recover_owned_parents(logger) -> None:
    """Reclaim abandoned parents on next startup; never use process names alone."""
    folder = _records_dir()
    if not folder.exists():
        return
    for path in folder.glob("*.json"):
        try:
            _recover_record(path, logger)
        except (psutil.Error, OSError, KeyError, ValueError, TypeError) as error:
            # Missing permissions, ambiguous identity, corrupt data: preserve.
            logger.warning(f"Skipping unsafe temporary launcher recovery from {path}: {error}")
