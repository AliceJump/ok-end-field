"""Recover only suspended launcher parents recorded as created by OK-EF.

Records are not proof by PID alone: every destructive action rechecks the
process creation time and full executable path, plus any still-running game.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path

import psutil

from src.core.paths import config_path

_RECORD_VERSION = 1
_TIME_TOLERANCE = 0.02
TEMPORARY_IMAGE_DIRNAME = "endfield_parent_images"
_TEMP_IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")


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


def _remove_local_image(record: dict, logger) -> bool:
    """Delete only a UUID-named copy under the app-controlled image root."""
    recorded = record.get("parent_exe")
    if not isinstance(recorded, str):
        return False
    candidate = Path(recorded)
    # Resolve only trusted config paths, never the untrusted journal path.
    config_root = Path(os.path.abspath(config_path(TEMPORARY_IMAGE_DIRNAME)))
    root = config_root.resolve()
    allowed_roots = {
        os.path.normcase(os.path.abspath(str(config_root))),
        os.path.normcase(os.path.abspath(str(root))),
    }
    candidate_root = os.path.normcase(os.path.abspath(str(candidate.parent.parent)))
    if candidate_root not in allowed_roots:
        if candidate.name.casefold() == "games.exe" and _TEMP_IMAGE_ID.fullmatch(candidate.parent.name):
            logger.warning("Cannot verify the root of a recorded temporary parent image; retaining journal")
            return False
        return True  # Original official Games.exe: its image is not ours.
    if candidate.name.casefold() != "games.exe" or not _TEMP_IMAGE_ID.fullmatch(candidate.parent.name):
        logger.warning("Refusing a malformed temporary parent image identity")
        return False

    identifier = uuid.UUID(hex=candidate.parent.name).hex
    directory = root / identifier
    image = directory / "Games.exe"
    allowed_images = {
        os.path.normcase(os.path.abspath(str(base / identifier / "Games.exe")))
        for base in (config_root, root)
    }
    if (
        os.path.normcase(os.path.abspath(recorded)) not in allowed_images
        or directory.is_symlink()
        or directory.is_junction()
    ):
        logger.warning("Refusing an unrecognized temporary parent image location")
        return False
    try:
        # The actual deletion target is reconstructed only from trusted root + UUID.
        image.unlink(missing_ok=True)
        if directory.exists():
            directory.rmdir()
        return True
    except OSError as error:
        logger.warning(f"Cannot retire owned temporary parent image; retry later ({type(error).__name__})")
        return False

def _retire_record(path: Path, record: dict, logger) -> None:
    if not _remove_local_image(record, logger):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(f"Cannot retire temporary launcher record {path}: {error}")


def forget_owned_parent(pid: int, logger) -> None:
    """Retire a terminated owned parent and its local copy, if any."""
    path = _record_path(pid)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as error:
        logger.warning(f"Cannot read temporary launcher record for PID {pid}: {error}")
        return
    if not isinstance(record, dict) or record.get("parent_pid") != pid:
        logger.warning(f"Invalid owned launcher record for PID {pid}: {path}")
        return
    _retire_record(path, record, logger)


def _owner_still_alive(record: dict) -> bool:
    """Never collect a temporary parent managed by a live OK-EF process."""
    try:
        owner = psutil.Process(record["owner_pid"])
        return owner.is_running() and abs(owner.create_time() - record["owner_created"]) <= _TIME_TOLERANCE
    except psutil.NoSuchProcess:
        return False


def _recorded_game_alive(record: dict) -> bool:
    game_pid = record.get("game_pid")
    if not game_pid:
        return False
    try:
        game = psutil.Process(game_pid)
        created = record.get("game_created")
        return game.is_running() and (created is None or abs(game.create_time() - created) <= _TIME_TOLERANCE)
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
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(started, (int, float))
        or isinstance(started, bool)
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
        # An old game can outlive its parent; retain a copy until the game exits.
        if not _recorded_game_alive(record):
            _retire_record(path, record, logger)
        return
    if not _same_process(parent, started, executable):
        # PID reused or old parent exited. Never terminate the unrelated process.
        if not _recorded_game_alive(record):
            _retire_record(path, record, logger)
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
    parent.wait(timeout=5)
    _retire_record(path, record, logger)
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
