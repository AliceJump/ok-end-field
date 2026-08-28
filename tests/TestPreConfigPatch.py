"""pre_config_patch 的单元测试：缺失 PATH 时用 os.defpath 补齐。

背景
----
PySide6 导入时 _setupQtDirectories 会执行
``os.environ['PATH'] = pyside_package_dir + os.pathsep + os.environ['PATH']``，
当 PATH 环境变量不存在时抛 KeyError: 'PATH'，导致 ok-ef 无法启动
（issue #191）。pre_config_patch 在导入 src.config 前先补齐 PATH。

本测试验证：
- 缺失 PATH 时 install_pre_config_patch 补上 os.defpath
- 已有 PATH 时保持不变
- 补齐后非绝对路径命令仍可被 os.get_exec_path 解析

隔离策略
--------
仅操作 os.environ['PATH']，并在 setUp/tearDown 中保存恢复，不影响其它测试。
不构造 ok 全局，不依赖 qfluentwidgets / PySide6 导入。
"""

import os
import unittest

from src.patches.pre_config_patch import install_pre_config_patch


class TestPreConfigPatch(unittest.TestCase):
    def setUp(self):
        self._saved_path = os.environ.get("PATH")

    def tearDown(self):
        if self._saved_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self._saved_path

    def test_sets_defpath_when_path_missing(self):
        os.environ.pop("PATH", None)
        install_pre_config_patch()
        self.assertIn("PATH", os.environ)
        self.assertEqual(os.environ["PATH"], os.defpath)

    def test_preserves_existing_path(self):
        os.environ["PATH"] = "C:\\existing\\bin"
        install_pre_config_patch()
        self.assertEqual(os.environ["PATH"], "C:\\existing\\bin")

    def test_bare_command_resolvable_after_patch(self):
        os.environ.pop("PATH", None)
        install_pre_config_patch()
        self.assertTrue(os.get_exec_path())


if __name__ == "__main__":
    unittest.main()
