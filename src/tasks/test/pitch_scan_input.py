"""Read-only input context and packet planning for the pitch scan preview."""

import ctypes
import ctypes.wintypes
import hashlib
import json
import math
import re
import struct
import winreg
from contextlib import contextmanager
from dataclasses import asdict, dataclass

import win32api
import win32con
import win32gui

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_system_parameters_info = _user32.SystemParametersInfoW
_system_parameters_info.argtypes = (ctypes.wintypes.UINT, ctypes.wintypes.UINT, ctypes.c_void_p, ctypes.wintypes.UINT)
_system_parameters_info.restype = ctypes.wintypes.BOOL
_get_dpi_for_window = _user32.GetDpiForWindow
_get_dpi_for_window.argtypes = (ctypes.wintypes.HWND,)
_get_dpi_for_window.restype = ctypes.wintypes.UINT
_set_thread_dpi_context = _user32.SetThreadDpiAwarenessContext
_set_thread_dpi_context.argtypes = (ctypes.c_void_p,)
_set_thread_dpi_context.restype = ctypes.c_void_p
_enum_value_name = ctypes.WinDLL("advapi32", use_last_error=True).RegEnumValueW
_enum_value_name.argtypes = (
    ctypes.wintypes.HKEY,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPWSTR,
    ctypes.POINTER(ctypes.wintypes.DWORD),
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
_enum_value_name.restype = ctypes.wintypes.LONG
_query_registry_value = ctypes.WinDLL("advapi32", use_last_error=True).RegQueryValueExW
_query_registry_value.argtypes = (
    ctypes.wintypes.HKEY,
    ctypes.wintypes.LPCWSTR,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.wintypes.DWORD),
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.wintypes.DWORD),
)
_query_registry_value.restype = ctypes.wintypes.LONG

# Only collect camera/rendering preferences, never account/SDK data.
_GAME_SETTING_PREFIXES = (
    "camera_",
    "controller_camera_",
    "enable_camera_far",
    "combo_skill_camera_alpha",
    "video_resolution",
    "video_fps",
    "video_frame_rate",
    "video_quality_vsync_",
    "video_quality_framegen_",
)
_FLOAT_GAME_SETTINGS = {
    "controller_camera_speed_x",
    "controller_camera_speed_y",
    "controller_camera_top_view_speed",
}


@contextmanager
def physical_coordinates():
    """Use physical Win32 coordinates locally, restoring the caller's context."""
    previous = _set_thread_dpi_context(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    if not previous:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        _set_thread_dpi_context(previous)


@dataclass(frozen=True)
class MouseSettings:
    speed: int | None
    thresholds: tuple[int, int] | None
    acceleration: int | None
    pointer_curves: tuple[tuple[str, str], ...] = ()
    pointer_curves_status: str = "not-read"

    @property
    def packet_limit(self):
        """Stay below documented acceleration thresholds, without assuming a gain."""
        if self.speed is None or self.thresholds is None or self.acceleration is None:
            return 1
        if self.acceleration == 0:
            return 6
        thresholds = self.thresholds[: 1 if self.acceleration == 1 else 2]
        return max(1, min(6, *thresholds))


@dataclass(frozen=True)
class ScanInputEnvironment:
    hwnd: int
    capture_size: tuple[int, int]
    client_size: tuple[int, int]
    client_origin: tuple[int, int]
    window_dpi: int | None
    display_refresh_hz: int | None
    mouse: MouseSettings
    game_settings: tuple[tuple[str, int | float | str], ...]
    game_settings_status: str

    @property
    def physical_center(self):
        return tuple(
            origin + round(size / 2) for origin, size in zip(self.client_origin, self.client_size, strict=True)
        )

    @property
    def center_tolerance(self):
        return max(1, round(4 * (self.window_dpi or 96) / 96))

    @property
    def profile_id(self):
        # Fingerprint the entire context, not just resolution, so later angle
        # measurements cannot silently be attributed to a different environment.
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def read_mouse_settings():
    speed = ctypes.c_int()
    thresholds = (ctypes.c_int * 3)()
    speed_ok = _system_parameters_info(win32con.SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0)
    thresholds_ok = _system_parameters_info(win32con.SPI_GETMOUSE, 0, thresholds, 0)
    curves = []
    curve_status = "ok"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", 0, winreg.KEY_READ) as key:
            for name in ("SmoothMouseXCurve", "SmoothMouseYCurve"):
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    curve_status = "missing"
                    continue
                if kind == winreg.REG_BINARY and isinstance(value, bytes):
                    curves.append((name, value.hex()))
                else:
                    curve_status = "unsupported-value"
    except FileNotFoundError:
        curve_status = "missing"
    except OSError:
        curve_status = "unavailable"
    return MouseSettings(
        speed.value if speed_ok and 1 <= speed.value <= 20 else None,
        (thresholds[0], thresholds[1]) if thresholds_ok else None,
        thresholds[2] if thresholds_ok and thresholds[2] in (0, 1, 2) else None,
        tuple(curves),
        curve_status,
    )


def _registry_value_names(key):
    """Enumerate names without loading values outside the camera allowlist."""
    buffer = ctypes.create_unicode_buffer(16384)
    for index in range(winreg.QueryInfoKey(key)[1]):
        length = ctypes.wintypes.DWORD(len(buffer))
        result = _enum_value_name(int(key), index, buffer, ctypes.byref(length), None, None, None, None)
        if result == 259:  # ERROR_NO_MORE_ITEMS: values may change during enumeration.
            return
        if result:
            raise ctypes.WinError(result)
        yield buffer.value


def _read_raw_registry_value(key, name):
    """Unity can label an eight-byte double REG_DWORD; winreg truncates it."""
    kind = ctypes.wintypes.DWORD()
    length = ctypes.wintypes.DWORD()
    result = _query_registry_value(int(key), name, None, ctypes.byref(kind), None, ctypes.byref(length))
    if result:
        raise ctypes.WinError(result)
    if length.value > 65536:
        raise OSError("Unexpectedly large camera setting")
    buffer = ctypes.create_string_buffer(length.value)
    result = _query_registry_value(int(key), name, None, ctypes.byref(kind), buffer, ctypes.byref(length))
    if result:
        raise ctypes.WinError(result)
    return kind.value, buffer.raw[: length.value]


def decode_game_setting(name, kind, raw):
    if name in _FLOAT_GAME_SETTINGS and kind == winreg.REG_DWORD and len(raw) in (4, 8):
        value = struct.unpack("<d" if len(raw) == 8 else "<f", raw)[0]
        if math.isfinite(value):
            return value
    elif (kind == winreg.REG_DWORD and len(raw) == 4) or (kind == winreg.REG_QWORD and len(raw) == 8):
        return int.from_bytes(raw, "little")
    return f"type={kind}:bytes={len(raw)}:sha256={hashlib.sha256(raw).hexdigest()}"


def read_game_settings():
    settings = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Hypergryph\Endfield", 0, winreg.KEY_READ) as key:
            for stored_name in _registry_value_names(key):
                name = re.sub(r"_h\d+$", "", stored_name)
                if not name.startswith(_GAME_SETTING_PREFIXES):
                    continue
                kind, raw = _read_raw_registry_value(key, stored_name)
                # Keep the stored number as-is. Zero may be an enum/default,
                # and controller_* is not proof that the setting governs mice.
                settings.append((name, decode_game_setting(name, kind, raw)))
    except FileNotFoundError:
        return (), "missing"
    except OSError:
        return (), "unavailable"
    return tuple(sorted(settings)), "ok"


def read_input_environment(hwnd, capture_size):
    if min(capture_size) <= 0:
        raise RuntimeError("游戏采集尺寸无效，取消俯仰扫描")
    with physical_coordinates():
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        client_size = (right - left, bottom - top)
        if min(client_size) <= 0:
            raise RuntimeError("游戏客户区尺寸无效，取消俯仰扫描")
        origin = win32gui.ClientToScreen(hwnd, (left, top))
    dpi = _get_dpi_for_window(hwnd) or None
    try:
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
        refresh = win32api.EnumDisplaySettings(monitor["Device"], win32con.ENUM_CURRENT_SETTINGS).DisplayFrequency
        refresh = refresh if refresh > 1 else None
    except (OSError, win32api.error):
        refresh = None
    settings, status = read_game_settings()
    return ScanInputEnvironment(
        hwnd, tuple(capture_size), client_size, origin, dpi, refresh, read_mouse_settings(), settings, status
    )


def split_mouse_delta(dx, dy, limit):
    """Split both axes along the same line, preserving every integer input unit."""
    count = max(1, math.ceil(max(abs(dx), abs(dy)) / max(1, limit)))
    previous_x = previous_y = 0
    for index in range(1, count + 1):
        x, y = round(dx * index / count), round(dy * index / count)
        step = (x - previous_x, y - previous_y)
        if step != (0, 0):
            yield step
        previous_x, previous_y = x, y
