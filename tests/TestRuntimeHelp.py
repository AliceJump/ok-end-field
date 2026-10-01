"""The userscript help button opens the official site without local docs/."""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.tasks.trigger import ItemNavigatorTask as navigator

REPO_ROOT = Path(__file__).resolve().parents[1]
HELP_URL = "https://ok-script.com/ok-end-field/docs/物品导航与实时检测/"


class TestRuntimeHelp(unittest.TestCase):
    def test_help_opens_official_site_without_local_docs(self):
        with tempfile.TemporaryDirectory(prefix="ok-ef help ") as folder:
            app_root = Path(folder)
            script_file = app_root / navigator.RELAY_USER_SCRIPT
            script_file.parent.mkdir(parents=True)
            shutil.copyfile(REPO_ROOT / navigator.RELAY_USER_SCRIPT, script_file)
            errors = []
            task = SimpleNamespace(log_info=lambda _: None, log_error=errors.append)
            with (
                patch.object(navigator.Path, "cwd", return_value=app_root),
                patch.object(navigator.webbrowser, "open") as open_browser,
                patch.object(navigator.subprocess, "Popen") as open_explorer,
            ):
                navigator.ItemNavigatorTask.open_userscript_help(task)

            self.assertFalse((app_root / "docs").exists())
            self.assertFalse((app_root / "assets/help").exists())
            self.assertEqual(errors, [])
            open_browser.assert_called_once_with(HELP_URL)
            # 生产侧会对路径 .resolve()，期望值需同样规范化：
            # runner 的 TEMP 可能是 8.3 短名（RUNNER~1），不规范化会断言失败。
            open_explorer.assert_called_once_with(["explorer", f"/select,{script_file.resolve()}"])

    def test_browser_failure_still_opens_the_script(self):
        with tempfile.TemporaryDirectory() as folder:
            app_root = Path(folder)
            script_file = app_root / navigator.RELAY_USER_SCRIPT
            script_file.parent.mkdir(parents=True)
            shutil.copyfile(REPO_ROOT / navigator.RELAY_USER_SCRIPT, script_file)
            errors = []
            task = SimpleNamespace(log_info=lambda _: None, log_error=errors.append)
            with (
                patch.object(navigator.Path, "cwd", return_value=app_root),
                patch.object(navigator.webbrowser, "open", side_effect=OSError("browser unavailable")) as open_browser,
                patch.object(navigator.subprocess, "Popen") as open_explorer,
            ):
                navigator.ItemNavigatorTask.open_userscript_help(task)

            open_browser.assert_called_once_with(HELP_URL)
            # 生产侧会对路径 .resolve()，期望值需同样规范化：
            # runner 的 TEMP 可能是 8.3 短名（RUNNER~1），不规范化会断言失败。
            open_explorer.assert_called_once_with(["explorer", f"/select,{script_file.resolve()}"])
            self.assertEqual(len(errors), 1)
            self.assertIn("打开油猴脚本帮助失败", errors[0])
