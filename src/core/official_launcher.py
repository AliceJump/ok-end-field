"""Start Endfield with a signed, temporary, suspended official Games.exe parent.

This is an alternative to the live-launcher startup/cleanup proposal in PR #558.
Both official channels release only their owned parent after its fresh check succeeds.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote

import psutil
from ok.util.file import ensure_dir_for_file, read_json_file, write_json_file

from src.core.launcher_cleanup import LauncherCleanup
from src.core.paths import config_path
from src.core.temporary_parent_recovery import (
    forget_owned_parent,
    note_game_started,
    recover_owned_parents,
    register_owned_parent,
)

OFFICIAL_SIGNERS = {
    "2B366F397E64A9494A553B70004E8CA1AAE02B9D",
    "FF4AF264C36A27F0C805936708EF8EC67B396225",
}
LAUNCHER_PRODUCTS = {
    "Hypergryph": {"鹰角启动器", "Hypergryph Launcher"},
    "Gryphline": {"GRYPHLINK"},
}
LAUNCHER_ID = "33a0a6296a20400d503c59ac0fd6341e"
LAUNCHER_CACHE_FILE = "official_launcher.json"
CHANNEL_REGISTRY_KEYS = {
    "Hypergryph": r"Software\Hypergryph\Launcher\7cadd91e975e03634d178312268c57d6",
    "Gryphline": r"Software\GRYPHLINK\Launcher\94005b4608518100ed8df1d8107356a8",
}
STEAM_ENDFIELD_APP_ID = "4732690"


def _path_has_part(path: str, part: str) -> bool:
    return part.casefold() in {item.casefold() for item in Path(os.path.abspath(path)).parts}


def _game_company(game_path: str) -> str:
    """Use Unity's installed company/product identity, independent of directory names."""
    info = Path(os.path.abspath(game_path)).parent / "Endfield_Data" / "app.info"
    try:
        lines = info.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError("Cannot identify Endfield channel; repair the game or launch through its client") from error
    if len(lines) >= 2 and lines[1].strip().casefold() == "endfield":
        for company in LAUNCHER_PRODUCTS:
            if lines[0].strip().casefold() == company.casefold():
                return company
    raise RuntimeError("Unrecognized Endfield channel; launch through its official client")


def _platform_for_game(game_path: str) -> str | None:
    """Route recognizable store installations through their platform client."""
    folder = Path(os.path.abspath(game_path)).parent
    if _path_has_part(game_path, "steamapps") and _path_has_part(game_path, "common"):
        return "steam"
    if (folder / ".egstore").is_dir():
        return "epic"
    return None


def _launch_via_platform(game_path: str, arguments: str, platform: str, logger) -> bool:
    if arguments.strip():
        raise RuntimeError(
            f"{platform} launch arguments cannot be forwarded by the platform URI; "
            "set them in the platform client instead"
        )
    if platform == "steam":
        uri = f"steam://rungameid/{STEAM_ENDFIELD_APP_ID}"
    elif platform == "epic":
        # Epic's documented protocol accepts an encoded installation directory,
        # avoiding fragile or outdated Catalog/Artifact identifiers.
        folder = str(Path(os.path.abspath(game_path)).parent).replace("\\", "/")
        uri = f"com.epicgames.launcher://apps/{quote(folder, safe='')}?action=launch&silent=true"
    else:
        raise ValueError(f"Unsupported launch platform: {platform}")
    logger.info(f"Requesting Endfield launch through {platform}")
    os.startfile(uri)
    return True



def get_launcher_cache_path() -> str:
    """Return the persisted launcher-path cache location."""
    return config_path(LAUNCHER_CACHE_FILE)


def verify_launcher(path: str, company: str | None = None) -> None:
    """Share certificate trust; use the signed product identity to select a channel."""
    script = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
        "$s=Get-AuthenticodeSignature -LiteralPath $env:OK_EF_VERIFY_LAUNCHER; "
        "$p=(Get-Item -LiteralPath $env:OK_EF_VERIFY_LAUNCHER).VersionInfo.ProductName; "
        "@{status=[int]$s.Status; signer=$s.SignerCertificate.Thumbprint; product=$p} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [
            os.path.join(os.environ["SYSTEMROOT"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        # pwsh can export a PSModulePath containing incompatible PowerShell 7 modules.
        env={
            **os.environ,
            "OK_EF_VERIFY_LAUNCHER": path,
            "PSModulePath": os.path.join(os.environ["SYSTEMROOT"], "System32", "WindowsPowerShell", "v1.0", "Modules"),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    signature = json.loads(result.stdout)
    if signature.get("status") != 0 or (signature.get("signer") or "").upper() not in OFFICIAL_SIGNERS:
        raise RuntimeError(f"Official launcher signature is invalid or unrecognized: {path}")
    if company is not None:
        products = {name.casefold() for name in LAUNCHER_PRODUCTS[company]}
        if (signature.get("product") or "").strip().casefold() not in products:
            raise RuntimeError(f"Official launcher product does not match {company}: {path}")


def _cached_launcher() -> str:
    try:
        data = read_json_file(get_launcher_cache_path())
    except OSError:
        return ""
    if not isinstance(data, dict):
        return ""
    path = str(data.get("path") or "").strip()
    return path if path and os.path.isfile(path) else ""


def _save_cached_launcher(path: str, logger) -> None:
    """Persist a verified launcher path without making launch depend on cache I/O."""
    try:
        cache_path = get_launcher_cache_path()
        ensure_dir_for_file(cache_path)
        write_json_file(cache_path, {"path": os.path.abspath(path)})
    except OSError as error:
        logger.warning(f"Cannot persist official launcher path: {error}")


def _registered_launchers(company: str) -> list[str]:
    """Query only the selected channel; never mix two installers' results."""
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{LAUNCHER_ID}"
    paths = []
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE) if company == "Hypergryph" else ():
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
                    folder = winreg.QueryValueEx(key, "InstallLocation")[0]
                path = os.path.join(folder, "Launcher.exe")
                if os.path.isfile(path):
                    paths.append(path)
            except OSError:
                continue
    # Both launchers register install_path, under their own product key.
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, CHANNEL_REGISTRY_KEYS[company], 0, winreg.KEY_READ | view
            ) as key:
                location = winreg.QueryValueEx(key, "install_path")[0]
            root = Path(location)
            launcher = root if root.name.casefold() == "launcher.exe" else root / "Launcher.exe"
            if launcher.is_file():
                paths.append(str(launcher))
        except OSError:
            continue
    return paths


def _game_path_launchers(game_path: str) -> list[str]:
    """Find Launcher.exe in ancestors of the configured Endfield executable."""
    if not game_path:
        return []
    folder = Path(os.path.abspath(game_path)).parent
    candidates = []
    for parent in (folder, *folder.parents):
        path = str(parent / "Launcher.exe")
        if os.path.isfile(path):
            candidates.append(path)
    return candidates


def installed_launcher(game_path: str, logger, company: str | None = None) -> str:
    """Find and verify the selected-channel launcher, then cache its location."""
    # Prefer the launcher adjacent to this installation over a cached launcher
    # from another channel. Registry/cache serve as fallbacks for relocated games.
    company = company or _game_company(game_path)
    candidates = [("game path", path) for path in _game_path_launchers(game_path)]
    candidates.extend(("registry", path) for path in _registered_launchers(company))
    candidates.append(("cache", _cached_launcher()))

    seen = set()
    errors = []
    for source, path in candidates:
        if not path:
            continue
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            verify_launcher(path, company)
        except Exception as error:
            logger.warning(f"Cannot use official launcher from {source}: path={path}, error={error}")
            errors.append(error)
            continue
        _save_cached_launcher(path, logger)
        logger.info(f"Verified official launcher: source={source}, path={path}")
        return path

    message = "Official Hypergryph/GRYPHLINK launcher not found; install the matching launcher first"
    if errors:
        raise RuntimeError(message) from errors[-1]
    raise RuntimeError(message)


def running_pids(name: str) -> list[int]:
    pids = []
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info["name"] or "").casefold() == name.casefold():
                pids.append(process.pid)
        except psutil.NoSuchProcess:
            continue
    return pids


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", wintypes.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "GetProcessId": ([wintypes.HANDLE], wintypes.DWORD),
        "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "QueryFullProcessImageNameW": (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        ),
        "InitializeProcThreadAttributeList": (
            [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)],
            wintypes.BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.c_size_t,
                wintypes.LPVOID,
                ctypes.c_size_t,
                wintypes.LPVOID,
                wintypes.LPVOID,
            ],
            wintypes.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([wintypes.LPVOID], None),
        "CreateProcessW": (
            [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                wintypes.LPVOID,
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.LPCWSTR,
                ctypes.POINTER(_StartupInfoEx),
                ctypes.POINTER(_ProcessInfo),
            ],
            wintypes.BOOL,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = args
        function.restype = result
    return kernel


def _check(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def official_parent(kernel, logger):
    rejected = None
    for pid in running_pids("Games.exe"):
        handle = None
        try:
            # Query the image through the same handle passed to CreateProcess.
            handle = _check(kernel.OpenProcess(0x0080 | 0x1000, False, pid))
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            _check(kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)))
            if os.path.basename(path.value).casefold() != "games.exe":
                raise RuntimeError(f"Launcher process image changed: PID {pid}")
            verify_launcher(path.value)
            logger.info(f"Verified official launcher parent: pid={pid}, path={path.value}")
            return handle
        except Exception as error:
            if handle:
                kernel.CloseHandle(handle)
            logger.warning(f"Cannot use Games.exe PID {pid}: {error}")
            rejected = error
    if rejected is not None:
        raise RuntimeError(
            "Cannot access a verified official Games.exe; check launcher signature and privileges"
        ) from rejected
    return None


def prepare_launcher(kernel, logger, game_path: str):
    parent = official_parent(kernel, logger)
    if parent:
        return parent
    path = installed_launcher(game_path, logger)
    subprocess.Popen(
        [path, "--game=Endfield", "--reason=4"],
        cwd=os.path.dirname(path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        parent = official_parent(kernel, logger)
        if parent:
            return parent
        time.sleep(0.25)
    raise TimeoutError("Official Games.exe did not become ready within 30 seconds")


def find_official_games_exe(game_path: str, logger, company: str | None = None) -> Path:
    """Choose the newest signed Games.exe within the verified official launcher."""
    company = company or _game_company(game_path)
    launcher = Path(installed_launcher(game_path, logger, company=company))
    candidates = []
    for folder in launcher.parent.iterdir():
        if not folder.is_dir():
            continue
        try:
            version = tuple(int(part) for part in folder.name.split("."))
        except ValueError:
            continue
        exe = folder / "Games.exe"
        if exe.is_file():
            candidates.append((version, exe))
    if not candidates:
        raise RuntimeError(f"No versioned official Games.exe found under {launcher.parent}")
    failures = []
    for _, exe in sorted(candidates, reverse=True):
        try:
            verify_launcher(str(exe), company)
            return exe
        except Exception as error:
            failures.append(error)
            logger.warning(f"Cannot use official Games.exe {exe}: {error}")
    raise RuntimeError("Cannot find a signed official Games.exe") from failures[-1]


def create_temporary_parent(kernel, game_path: str, logger, company: str | None = None):
    """Create our own suspended Games.exe, without touching running launcher processes."""
    executable = find_official_games_exe(game_path, logger, company=company)
    startup = _StartupInfoEx()
    startup.StartupInfo.cb = ctypes.sizeof(_StartupInfo)
    process = _ProcessInfo()
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable)]))
    _check(
        kernel.CreateProcessW(
            str(executable),
            command,
            None,
            None,
            False,
            0x00000004 | subprocess.CREATE_NO_WINDOW,  # CREATE_SUSPENDED
            None,
            str(executable.parent),
            ctypes.byref(startup),
            ctypes.byref(process),
        )
    )
    kernel.CloseHandle(process.hThread)
    try:
        # A crash after this point must leave sufficient identity evidence for
        # the next OK-EF launch to safely recognize our suspended process.
        register_owned_parent(process.dwProcessId)
    except Exception:
        kernel.TerminateProcess(process.hProcess, 0)
        kernel.CloseHandle(process.hProcess)
        raise
    logger.info(f"Created owned suspended official Games.exe parent PID {process.dwProcessId}")
    return process.hProcess


def create_process(kernel, game_path: str, arguments: str, parent) -> int:
    """Create without a shell or inherited output pipes; release every native handle."""
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    attributes = ctypes.cast(buffer, wintypes.LPVOID)
    _check(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
    try:
        parent_value = wintypes.HANDLE(parent)
        _check(
            kernel.UpdateProcThreadAttribute(
                attributes,
                0,
                0x00020000,
                ctypes.byref(parent_value),
                ctypes.sizeof(parent_value),
                None,
                None,
            )
        )
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.lpAttributeList = attributes
        process = _ProcessInfo()
        command = ctypes.create_unicode_buffer((subprocess.list2cmdline([game_path]) + " " + arguments).strip())
        _check(
            kernel.CreateProcessW(
                game_path,
                command,
                None,
                None,
                False,
                0x00080000 | 0x00000400 | subprocess.CREATE_NO_WINDOW,
                None,
                os.path.dirname(game_path),
                ctypes.byref(startup),
                ctypes.byref(process),
            )
        )
        kernel.CloseHandle(process.hThread)
        kernel.CloseHandle(process.hProcess)
        return process.dwProcessId
    finally:
        kernel.DeleteProcThreadAttributeList(attributes)


def _start_fallback_cleanup(cleanup, pid: int, kernel, parent, logger) -> bool:
    """Return whether a fallback watcher took ownership of the parent handle."""
    try:
        cleanup.start_exit_watch(pid)
        return True  # The fallback watcher took ownership (or completed inline).
    except Exception as fallback_error:
        # Only a verified child exit permits cleanup. If no worker can be
        # started, retain ownership and wait here for the game to exit.
        logger.warning(f"Cannot start fallback cleanup; waiting for game exit in caller: {fallback_error}")
        try:
            while psutil.pid_exists(pid):
                time.sleep(2)
            if kernel.TerminateProcess(parent, 0):
                forget_owned_parent(cleanup.parent_pid, logger)
            else:
                logger.warning("Could not terminate owned temporary Games.exe after game exit")
        except Exception as exit_error:
            logger.warning(f"Cannot complete emergency temporary launcher cleanup: {exit_error}")
    return False


def _start_cleanup(cleanup, pid: int, kernel, parent, logger) -> bool:
    """Return whether cleanup took ownership, preserving live children's parents."""
    try:
        cleanup.start(pid)
        return True
    except Exception as error:
        # If the child has definitely exited, our suspended parent is disposable.
        # Otherwise do not guess about SDK readiness or terminate a live child's parent.
        try:
            child_gone = not psutil.pid_exists(pid)
        except Exception:
            child_gone = False
        if child_gone:
            try:
                if not kernel.TerminateProcess(parent, 0):
                    raise OSError("Could not terminate the owned temporary Games.exe")
                forget_owned_parent(cleanup.parent_pid, logger)
                logger.info(f"Game PID {pid} exited before cleanup monitor started; discarded temporary Games.exe")
            except Exception as discard_error:
                logger.warning(f"Cannot discard temporary Games.exe after game exit: {discard_error}")
            return False
        logger.warning(f"Cannot start initial SDK monitor; starting fallback for owned Games.exe: {error}")
        return _start_fallback_cleanup(cleanup, pid, kernel, parent, logger)


def launch_game(game_path: str, arguments: str, logger) -> bool:
    try:
        recover_owned_parents(logger)
    except Exception as error:
        logger.warning(f"Cannot recover temporary launcher records: {error}")
    if running_pids("Endfield.exe"):
        logger.info("Endfield is already running; skipping launch")
        return True

    platform = _platform_for_game(game_path)
    if platform is not None:
        return _launch_via_platform(game_path, arguments, platform, logger)

    company = _game_company(game_path)
    kernel = _kernel32()
    # Parent compatibility is independent of the game's company/log directory.
    # Prefer its own launcher, then try the other known official product once.
    companies = [company, *(other for other in LAUNCHER_PRODUCTS if other != company)]
    failures = []
    for parent_company in companies:
        parent = None
        child_created = False
        try:
            parent = create_temporary_parent(kernel, game_path, logger, company=parent_company)
            # Always watch the game's logs, including when its parent is cross-channel.
            cleanup = LauncherCleanup(kernel, parent, logger, company=company)
            if running_pids("Endfield.exe"):
                return True
            pid = create_process(kernel, os.path.abspath(game_path), arguments, parent)
            child_created = True
            note_game_started(cleanup.parent_pid, pid, logger)
            logger.info(f"Created Endfield with suspended official launcher parent: pid={pid}, launcher={parent_company}")
            if _start_cleanup(cleanup, pid, kernel, parent, logger):
                parent = None  # The background watcher owns the process handle now.
            return True
        except Exception as error:
            if child_created:
                raise  # A post-creation failure must never trigger another launch.
            logger.warning(f"Cannot launch Endfield using {parent_company}: {error}")
            failures.append(error)
        finally:
            if parent:
                try:
                    if not child_created:
                        # Do not create another parent if disposal of this one fails.
                        if not kernel.TerminateProcess(parent, 0):
                            raise OSError("Could not terminate aborted owned temporary Games.exe")
                        forget_owned_parent(kernel.GetProcessId(parent), logger)
                finally:
                    kernel.CloseHandle(parent)
    raise failures[-1]
