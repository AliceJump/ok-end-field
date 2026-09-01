"""ok 库 win32_gdi 全局 user32.GetCursorPos 污染根治补丁的单元测试。

背景
----
ok/ui/overlay/win32_gdi.py 模块加载时自定义 POINT 结构（字段与 wintypes.POINT
一致但类型对象不同），并把全局 user32.GetCursorPos.argtypes 设为
POINTER(自定义POINT)。ctypes 按类型对象做指针校验，任何用标准 wintypes.POINT
调用 GetCursorPos 的代码（pyautogui / pynput.mouse / 项目 Mouse.py）都会报
"expected LP_POINT instance instead of pointer to POINT" 崩溃。

本测试验证 src.patches.win32_gdi_point_patch.install_win32_gdi_point_patch 从
源头修复：把 win32_gdi.POINT 替换为标准 wintypes.POINT，并重设所有引用 POINT
的 argtypes，使各调用方（含 ok 内部构造 POINT() 的调用）均兼容。

隔离策略
--------
仅导入 win32_gdi 并执行补丁函数，不构造 ok 全局（不 init_ok/destroy_ok）。
测试类结束后恢复 POINT、三个 argtypes 和安装标记，并由后置哨兵测试验证，
避免污染单进程 discovery 的后续模块；生产启动时补丁仍保持安装状态。
"""

import ctypes
import unittest
from ctypes import wintypes

from ok.ui.overlay import win32_gdi

from src.patches import win32_gdi_point_patch


class TestOkWin32GdiPointPatch(unittest.TestCase):
    _cleanup_completed = False

    @classmethod
    def setUpClass(cls):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        cls._orig_point = win32_gdi.POINT
        cls._orig_getcursorpos_argtypes = user32.GetCursorPos.argtypes
        cls._orig_updatelayeredwindow_argtypes = user32.UpdateLayeredWindow.argtypes
        cls._orig_movetoex_argtypes = gdi32.MoveToEx.argtypes
        cls._original_flag = win32_gdi_point_patch._PATCH_INSTALLED
        cls.addClassCleanup(cls._assert_and_restore_patch_state)
        win32_gdi_point_patch._PATCH_INSTALLED = False
        win32_gdi_point_patch.install_win32_gdi_point_patch()

    @classmethod
    def _assert_and_restore_patch_state(cls):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        win32_gdi.POINT = cls._orig_point
        user32.GetCursorPos.argtypes = cls._orig_getcursorpos_argtypes
        user32.UpdateLayeredWindow.argtypes = cls._orig_updatelayeredwindow_argtypes
        gdi32.MoveToEx.argtypes = cls._orig_movetoex_argtypes
        win32_gdi_point_patch._PATCH_INSTALLED = cls._original_flag
        cls._cleanup_completed = True

    def test_point_replaced_with_wintypes(self):
        # 模块内 POINT 已被替换为标准 wintypes.POINT（ok 内部 POINT() 也用它）
        self.assertIs(win32_gdi.POINT, wintypes.POINT)

    def test_getcursorpos_argtypes_use_wintypes_point(self):
        user32 = ctypes.windll.user32
        self.assertEqual(
            user32.GetCursorPos.argtypes,
            [ctypes.POINTER(wintypes.POINT)],
        )

    def test_getcursorpos_works_with_wintypes_point(self):
        # pyautogui / pynput.mouse / 项目 Mouse.py 的调用方式
        pt = wintypes.POINT()
        ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        self.assertTrue(ok)

    def test_getcursorpos_works_with_module_point(self):
        # ok win32_gdi 内部构造 POINT() 再调 GetCursorPos 的调用方式（第508行）
        point = win32_gdi.POINT()
        ok = ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        self.assertTrue(ok)

    def test_updatelayeredwindow_argtypes_use_wintypes_point(self):
        # 引用 POINT 的其它 argtypes 同步指向 wintypes.POINT，ok 内部调用不破
        user32 = ctypes.windll.user32
        orig_point_ptr = ctypes.POINTER(self._orig_point)
        self.assertNotIn(orig_point_ptr, user32.UpdateLayeredWindow.argtypes)
        self.assertIn(ctypes.POINTER(wintypes.POINT), user32.UpdateLayeredWindow.argtypes)

    def test_movetoex_argtypes_use_wintypes_point(self):
        gdi32 = ctypes.windll.gdi32
        self.assertEqual(
            gdi32.MoveToEx.argtypes,
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.POINT)],
        )


class TestZOkWin32GdiPointPatchStateRestored(unittest.TestCase):
    def test_global_state_restored_after_patch_tests(self):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        self.assertTrue(TestOkWin32GdiPointPatch._cleanup_completed)
        self.assertIs(win32_gdi.POINT, TestOkWin32GdiPointPatch._orig_point)
        self.assertIs(user32.GetCursorPos.argtypes, TestOkWin32GdiPointPatch._orig_getcursorpos_argtypes)
        self.assertIs(
            user32.UpdateLayeredWindow.argtypes,
            TestOkWin32GdiPointPatch._orig_updatelayeredwindow_argtypes,
        )
        self.assertIs(gdi32.MoveToEx.argtypes, TestOkWin32GdiPointPatch._orig_movetoex_argtypes)
        self.assertEqual(
            win32_gdi_point_patch._PATCH_INSTALLED,
            TestOkWin32GdiPointPatch._original_flag,
        )


if __name__ == "__main__":
    unittest.main()
