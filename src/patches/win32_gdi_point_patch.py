from __future__ import annotations

_PATCH_INSTALLED = False


def install_win32_gdi_point_patch():
    """根治 ok 库 win32_gdi 对全局 user32.GetCursorPos 的 argtypes 污染。

    背景：ok 库 ok/ui/overlay/win32_gdi.py 模块加载时会自定义一个 POINT 结构
    （字段与 wintypes.POINT 一致但类型对象不同），并把全局
    user32.GetCursorPos.argtypes 设为 POINTER(自定义POINT)。ctypes 按类型对象
    做指针校验，任何用标准 wintypes.POINT 调用 GetCursorPos 的代码
    （pyautogui._pyautogui_win._position / pynput.mouse / 项目 Mouse.py）都会报
    "expected LP_POINT instance instead of pointer to POINT" 崩溃。

    本补丁从源头修复：把 win32_gdi 模块里的 POINT 类替换为标准 wintypes.POINT
    （内存布局完全一致，ok 内部构造 POINT() 的调用不受影响），并重新设置所有
    引用 POINT 的 argtypes（GetCursorPos / UpdateLayeredWindow / MoveToEx），
    使全局 user32/gdi32 的 argtypes 与第三方库一致，各调用方均无需再规避。

    安装是原子的：先保存所有原始值，任何一步失败都会回滚已修改的值并记录日志，
    只有全部赋值成功后才标记为已安装，失败后下次调用可重试。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        from ok import Logger
        from ok.ui.overlay import win32_gdi
    except Exception as exc:
        # ok 库不可用，跳过，不影响启动
        return
    logger = Logger.get_logger(__name__)
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    originals = {
        "POINT": win32_gdi.POINT,
        "GetCursorPos.argtypes": user32.GetCursorPos.argtypes,
        "UpdateLayeredWindow.argtypes": user32.UpdateLayeredWindow.argtypes,
        "MoveToEx.argtypes": gdi32.MoveToEx.argtypes,
    }
    try:
        point_ptr = ctypes.POINTER(wintypes.POINT)
        win32_gdi.POINT = wintypes.POINT
        user32.GetCursorPos.argtypes = [point_ptr]
        user32.UpdateLayeredWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, point_ptr,
            ctypes.POINTER(win32_gdi.SIZE), ctypes.c_void_p, point_ptr,
            wintypes.DWORD, ctypes.POINTER(win32_gdi.BLENDFUNCTION), wintypes.DWORD,
        ]
        gdi32.MoveToEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, point_ptr]
    except Exception as exc:
        win32_gdi.POINT = originals["POINT"]
        user32.GetCursorPos.argtypes = originals["GetCursorPos.argtypes"]
        user32.UpdateLayeredWindow.argtypes = originals["UpdateLayeredWindow.argtypes"]
        gdi32.MoveToEx.argtypes = originals["MoveToEx.argtypes"]
        logger.warning("win32_gdi_point_patch 安装失败，已回滚所有已修改的值: %s", exc)
        return
    _PATCH_INSTALLED = True
