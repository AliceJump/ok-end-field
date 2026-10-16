"""Start Endfield with the real, signed official launcher as its parent.

The CN 1.6.3 SDK checks the immediate parent, so adding command line arguments
to a shell launch is insufficient. No game files or SDK checks are modified.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes

import psutil

OFFICIAL_SIGNERS = {
    "2B366F397E64A9494A553B70004E8CA1AAE02B9D",
    "FF4AF264C36A27F0C805936708EF8EC67B396225",
}
LAUNCHER_ID = "33a0a6296a20400d503c59ac0fd6341e"


def verify_launcher(path: str) -> None:
    """Validate Authenticode and the signer allowed by the CN launcher rule."""
    script = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
        "$s=Get-AuthenticodeSignature -LiteralPath $env:OK_EF_VERIFY_LAUNCHER; "
        "@{status=[int]$s.Status; signer=$s.SignerCertificate.Thumbprint} | ConvertTo-Json -Compress"
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


def installed_launcher() -> str:
    """Find the CN launcher through its installer registration, without fixed drives/versions."""
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{LAUNCHER_ID}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
                    folder = winreg.QueryValueEx(key, "InstallLocation")[0]
                path = os.path.join(folder, "Launcher.exe")
                if os.path.isfile(path):
                    return path
            except OSError:
                continue
    raise RuntimeError("Official Hypergryph launcher not found; install or open the official launcher first")


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


def prepare_launcher(kernel, logger):
    parent = official_parent(kernel, logger)
    if parent:
        return parent
    path = installed_launcher()
    verify_launcher(path)
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


def launch_game(game_path: str, arguments: str, logger) -> bool:
    if running_pids("Endfield.exe"):
        logger.info("Endfield is already running; skipping launch")
        return True
    kernel = _kernel32()
    parent = prepare_launcher(kernel, logger)
    try:
        # The user may have started the game while the launcher was opening.
        if running_pids("Endfield.exe"):
            return True
        pid = create_process(kernel, os.path.abspath(game_path), arguments, parent)
        logger.info(f"Created Endfield with official launcher parent: pid={pid}")
        return True
    finally:
        kernel.CloseHandle(parent)
