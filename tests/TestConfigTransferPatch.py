"""配置导出/导入补丁的单元测试。

背景
----
src.patches.config_transfer_patch 通过猴子补丁为 StartTab 追加"导出配置/
导入配置"按钮，并让 MainWindow 支持拖入 zip 导入配置。

本测试覆盖补丁的纯逻辑部分：

1. 导出：configs 目录打包为 zip，条目以 configs/ 为根，backup 与
   global_config_migration_backup 备份目录被跳过；
2. 导入前缀解析：configs/ 根、zip 根部、单层目录包裹三种形态，非法
   压缩包返回 None；
3. 导入应用：覆盖前把旧配置备份到 configs/backup/import_backup_<ts>/，
   清空（保留排除目录）后解压，并拒绝越出配置目录的 zip-slip 条目；
4. 补丁安装：幂等，StartTab/MainWindow 被打上补丁且拖拽处理函数就位。

隔离策略
--------
文件操作全部使用 tempfile 临时目录；补丁安装测试在类结束时恢复
StartTab/MainWindow 的原始方法与安装标记。拖拽事件用 QMimeData 与
桩事件对象验证，不弹出任何对话框。
"""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import src.patches.config_transfer_patch as config_transfer_patch
from src.patches.config_transfer_patch import (
    _collect_zip_paths,
    _drag_enter_event,
    _pause_executor,
    _restart_application,
    _resume_executor,
    apply_config_import,
    export_config_zip,
    install_config_transfer_patch,
    resolve_import_prefix,
)


def _make_zip(zip_path: Path, entries: dict[str, str]):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _FakeDragEvent:
    def __init__(self, mime_data):
        self._mime_data = mime_data
        self.accepted = False
        self.ignored = False

    def mimeData(self):
        return self._mime_data

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class TestConfigTransferPatch(unittest.TestCase):
    def test_export_config_zip_roots_at_configs_and_skips_backup_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs_dir = Path(tmp) / "configs"
            _write_file(configs_dir / "_ok.json", "{}")
            _write_file(configs_dir / "BattleTask.json", "{}")
            _write_file(configs_dir / "nested" / "sub.json", "{}")
            _write_file(configs_dir / "backup" / "keep.json", "{}")
            _write_file(configs_dir / "global_config_migration_backup" / "keep.json", "{}")

            zip_path = Path(tmp) / "out.zip"
            count = export_config_zip(configs_dir, zip_path)

            with zipfile.ZipFile(zip_path) as zf:
                names = sorted(zf.namelist())
            self.assertEqual(
                names,
                ["configs/BattleTask.json", "configs/_ok.json", "configs/nested/sub.json"],
            )
            self.assertEqual(count, 3)

    def test_resolve_import_prefix_for_all_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            configs_zip = tmp_path / "configs.zip"
            _make_zip(configs_zip, {"configs/_ok.json": "{}", "configs/a.json": "{}"})
            self.assertEqual(resolve_import_prefix(configs_zip), "configs/")

            flat_zip = tmp_path / "flat.zip"
            _make_zip(flat_zip, {"_ok.json": "{}"})
            self.assertEqual(resolve_import_prefix(flat_zip), "")

            wrapped_zip = tmp_path / "wrapped.zip"
            _make_zip(wrapped_zip, {"myconf/_ok.json": "{}", "myconf/a.json": "{}"})
            self.assertEqual(resolve_import_prefix(wrapped_zip), "myconf/")

            no_json_zip = tmp_path / "no_json.zip"
            _make_zip(no_json_zip, {"readme.txt": "hello"})
            self.assertIsNone(resolve_import_prefix(no_json_zip))

            bad_zip = tmp_path / "bad.zip"
            bad_zip.write_text("not a zip", encoding="utf-8")
            self.assertIsNone(resolve_import_prefix(bad_zip))

            self.assertIsNone(resolve_import_prefix(tmp_path / "missing.zip"))

    def test_apply_config_import_replaces_files_and_backs_up_old_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "_ok.json", "old_global")
            _write_file(configs_dir / "OldTask.json", "old_task")
            _write_file(configs_dir / "backup" / "keep.json", "keep")
            _write_file(configs_dir / "global_config_migration_backup" / "keep.json", "keep")

            zip_path = tmp_path / "in.zip"
            _make_zip(
                zip_path,
                {
                    "configs/_ok.json": "new_global",
                    "configs/BattleTask.json": "battle",
                    "configs/nested/deep/c.json": "c",
                },
            )

            backup_dir = apply_config_import(zip_path, configs_dir)

            self.assertTrue(backup_dir.is_relative_to(configs_dir / "backup"))
            self.assertTrue(backup_dir.name.startswith("import_backup_"))
            # 旧配置完整备份（排除目录本身不重复备份）
            self.assertEqual((backup_dir / "_ok.json").read_text(encoding="utf-8"), "old_global")
            self.assertEqual((backup_dir / "OldTask.json").read_text(encoding="utf-8"), "old_task")
            self.assertEqual(sorted(p.name for p in backup_dir.iterdir()), ["OldTask.json", "_ok.json"])

            # 新配置就位，zip 中不存在的旧文件被清除
            self.assertEqual((configs_dir / "_ok.json").read_text(encoding="utf-8"), "new_global")
            self.assertTrue((configs_dir / "BattleTask.json").is_file())
            self.assertTrue((configs_dir / "nested" / "deep" / "c.json").is_file())
            self.assertFalse((configs_dir / "OldTask.json").exists())

            # 排除目录在清空时保留
            self.assertEqual((configs_dir / "backup" / "keep.json").read_text(encoding="utf-8"), "keep")
            self.assertTrue((configs_dir / "global_config_migration_backup" / "keep.json").is_file())

    def test_apply_config_import_accepts_flat_and_wrapped_zips(self):
        # 包裹目录前缀在导入时被剥离，条目直接落到配置目录
        for zip_entries, expect_files in [
            ({"_ok.json": "flat"}, ["_ok.json"]),
            ({"pkg/_ok.json": "wrapped", "pkg/a.json": "a"}, ["_ok.json", "a.json"]),
        ]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                configs_dir = tmp_path / "configs"
                configs_dir.mkdir()
                zip_path = tmp_path / "in.zip"
                _make_zip(zip_path, zip_entries)
                apply_config_import(zip_path, configs_dir)
                for name in expect_files:
                    self.assertTrue((configs_dir / name).is_file(), name)

    def test_apply_config_import_rejects_zip_slip(self):
        for zip_entries in [
            {"configs/../evil.json": "evil"},
            {"../evil.json": "evil"},
            {"configs/..\\evil.json": "evil"},
        ]:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                configs_dir = tmp_path / "configs"
                configs_dir.mkdir()
                zip_path = tmp_path / "evil.zip"
                _make_zip(zip_path, zip_entries)

                with self.assertRaises(ValueError):
                    apply_config_import(zip_path, configs_dir)
                self.assertFalse((tmp_path / "evil.json").exists())

    def test_apply_config_import_keeps_live_config_when_archive_read_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "old.json", "old")
            zip_path = tmp_path / "bad-crc.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                zf.writestr("configs/new.json", "new-content")
            archive = zip_path.read_bytes().replace(b"new-content", b"bad-content", 1)
            zip_path.write_bytes(archive)

            with self.assertRaises(zipfile.BadZipFile):
                apply_config_import(zip_path, configs_dir)

            self.assertEqual((configs_dir / "old.json").read_text(encoding="utf-8"), "old")
            self.assertFalse((configs_dir / "new.json").exists())

    def test_apply_config_import_skips_excluded_archive_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "backup" / "keep.json", "keep")
            zip_path = tmp_path / "in.zip"
            _make_zip(
                zip_path,
                {
                    "configs/new.json": "new",
                    "configs/backup/imported.json": "ignored",
                },
            )

            apply_config_import(zip_path, configs_dir)

            self.assertEqual((configs_dir / "new.json").read_text(encoding="utf-8"), "new")
            self.assertEqual(
                (configs_dir / "backup" / "keep.json").read_text(encoding="utf-8"),
                "keep",
            )
            self.assertFalse((configs_dir / "backup" / "imported.json").exists())

    def test_apply_config_import_rejects_excluded_only_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "old.json", "old")
            zip_path = tmp_path / "in.zip"
            _make_zip(zip_path, {"configs/backup/imported.json": "ignored"})

            with self.assertRaisesRegex(ValueError, "no config files"):
                apply_config_import(zip_path, configs_dir)

            self.assertEqual((configs_dir / "old.json").read_text(encoding="utf-8"), "old")

    def test_apply_config_import_enforces_member_and_total_size_limits(self):
        cases = [
            ({"configs/large.json": "12345"}, 4, 100),
            ({"configs/a.json": "123", "configs/b.json": "456"}, 4, 5),
        ]
        for entries, member_limit, total_limit in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                configs_dir = tmp_path / "configs"
                _write_file(configs_dir / "old.json", "old")
                zip_path = tmp_path / "in.zip"
                _make_zip(zip_path, entries)

                with (
                    patch.object(config_transfer_patch, "_MAX_IMPORT_MEMBER_BYTES", member_limit),
                    patch.object(config_transfer_patch, "_MAX_IMPORT_TOTAL_BYTES", total_limit),
                    self.assertRaisesRegex(ValueError, "too large"),
                ):
                    apply_config_import(zip_path, configs_dir)

                self.assertEqual((configs_dir / "old.json").read_text(encoding="utf-8"), "old")

    def test_apply_config_import_uses_unique_backup_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "old.json", "old")
            zip_path = tmp_path / "in.zip"
            _make_zip(zip_path, {"configs/new.json": "new"})
            fixed_datetime = Mock()
            fixed_datetime.now.return_value.strftime.return_value = "20260907_010203_000000"

            with patch.object(config_transfer_patch, "datetime", fixed_datetime):
                first = apply_config_import(zip_path, configs_dir)
                second = apply_config_import(zip_path, configs_dir)

            self.assertNotEqual(first, second)
            self.assertEqual(second.name, f"{first.name}_1")

    def test_apply_config_import_restores_backup_when_replacement_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            configs_dir = tmp_path / "configs"
            _write_file(configs_dir / "old.json", "old")
            zip_path = tmp_path / "in.zip"
            _make_zip(zip_path, {"configs/a.json": "a", "configs/b.json": "b"})
            real_move = config_transfer_patch.shutil.move
            move_count = 0

            def fail_second_move(source, destination):
                nonlocal move_count
                move_count += 1
                if move_count == 2:
                    raise OSError("replacement failed")
                return real_move(source, destination)

            with (
                patch.object(config_transfer_patch.shutil, "move", fail_second_move),
                self.assertRaisesRegex(OSError, "replacement failed"),
            ):
                apply_config_import(zip_path, configs_dir)

            self.assertEqual((configs_dir / "old.json").read_text(encoding="utf-8"), "old")
            self.assertFalse((configs_dir / "a.json").exists())
            self.assertFalse((configs_dir / "b.json").exists())

    def test_executor_resume_only_reverses_pause_from_import(self):
        from ok import og

        executor = SimpleNamespace(paused=False, pause=Mock(), start=Mock())
        executor.pause.side_effect = lambda: setattr(executor, "paused", True)
        with patch.object(og, "executor", executor):
            paused_by_import = _pause_executor()
            _resume_executor(paused_by_import)

        self.assertTrue(paused_by_import)
        executor.pause.assert_called_once_with()
        executor.start.assert_called_once_with()

        executor = SimpleNamespace(paused=True, pause=Mock(), start=Mock())
        with patch.object(og, "executor", executor):
            paused_by_import = _pause_executor()
            _resume_executor(paused_by_import)

        self.assertFalse(paused_by_import)
        executor.pause.assert_not_called()
        executor.start.assert_not_called()

    def test_restart_application_does_not_quit_when_shell_launch_fails(self):
        import ctypes

        from ok import Logger, og
        from ok.gui.util import Alert

        app = SimpleNamespace(tr=lambda value: value, quit=Mock())
        shell32 = SimpleNamespace(ShellExecuteW=Mock(return_value=31))
        with (
            patch.object(og, "app", app),
            patch.object(Logger, "get_logger", return_value=Mock()),
            patch.object(Alert, "alert_error") as alert_error,
            patch.object(ctypes, "windll", SimpleNamespace(shell32=shell32), create=True),
        ):
            self.assertFalse(_restart_application())

        app.quit.assert_not_called()
        alert_error.assert_called_once()

    def test_confirm_import_resumes_executor_on_failure_and_later(self):
        from ok import Logger, og
        from ok.gui.util import Alert

        app = SimpleNamespace(tr=lambda value: value)

        def run_import(responses, import_error=None):
            response_iter = iter(responses)

            def make_message_box(*_args):
                return SimpleNamespace(
                    exec=lambda: next(response_iter),
                    yesButton=SimpleNamespace(setText=Mock()),
                    cancelButton=SimpleNamespace(setText=Mock()),
                )

            resume_executor = Mock()
            with (
                patch.object(og, "app", app),
                patch.object(Logger, "get_logger", return_value=Mock()),
                patch.object(Alert, "alert_error"),
                patch.object(Alert, "alert_info"),
                patch.object(config_transfer_patch.QApplication, "activeWindow", return_value=None),
                patch.object(config_transfer_patch, "MessageBox", side_effect=make_message_box),
                patch.object(config_transfer_patch, "resolve_import_prefix", return_value="configs/"),
                patch.object(config_transfer_patch, "_pause_executor", return_value=True),
                patch.object(config_transfer_patch, "_resume_executor", resume_executor),
                patch.object(config_transfer_patch, "get_configs_dir", return_value=Path("configs")),
                patch.object(config_transfer_patch, "apply_config_import", side_effect=import_error),
            ):
                config_transfer_patch._confirm_and_import(Path("config.zip"))
            resume_executor.assert_called_once_with(True)

        run_import([True], OSError("import failed"))
        run_import([True, False])

    def test_collect_zip_paths_only_accepts_local_zip_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PySide6.QtCore import QMimeData, QUrl

            zip_path = Path(tmp) / "config.zip"
            _make_zip(zip_path, {"configs/_ok.json": "{}"})
            txt_path = Path(tmp) / "note.txt"
            txt_path.write_text("x", encoding="utf-8")

            mime = QMimeData()
            mime.setUrls(
                [
                    QUrl.fromLocalFile(str(zip_path)),
                    QUrl.fromLocalFile(str(txt_path)),
                    QUrl("https://example.com/config.zip"),
                ]
            )
            paths = _collect_zip_paths(mime)
            self.assertEqual(paths, [zip_path])

    def test_drag_enter_accepts_zip_and_ignores_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PySide6.QtCore import QMimeData, QUrl

            zip_path = Path(tmp) / "config.zip"
            _make_zip(zip_path, {"configs/_ok.json": "{}"})

            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(zip_path))])
            event = _FakeDragEvent(mime)
            _drag_enter_event(None, event)
            self.assertTrue(event.accepted)
            self.assertFalse(event.ignored)

            mime_other = QMimeData()
            mime_other.setUrls([QUrl.fromLocalFile(str(Path(tmp) / "a.txt"))])
            event_other = _FakeDragEvent(mime_other)
            _drag_enter_event(None, event_other)
            self.assertTrue(event_other.ignored)
            self.assertFalse(event_other.accepted)


class TestConfigTransferPatchInstall(unittest.TestCase):
    _original_start_init = None
    _original_main_init = None
    _original_flag = None
    _main_window_had_drag_enter = False
    _main_window_had_drop = False

    @classmethod
    def setUpClass(cls):
        from ok.ui.qt.MainWindow import MainWindow
        from ok.ui.qt.start.StartTab import StartTab

        cls._original_start_init = StartTab.__init__
        cls._original_main_init = MainWindow.__init__
        cls._original_flag = config_transfer_patch._PATCH_INSTALLED
        cls._main_window_had_drag_enter = "dragEnterEvent" in MainWindow.__dict__
        cls._main_window_had_drop = "dropEvent" in MainWindow.__dict__
        cls.addClassCleanup(cls._restore_patch_state)

        config_transfer_patch._PATCH_INSTALLED = False
        install_config_transfer_patch()

    @classmethod
    def _restore_patch_state(cls):
        from ok.ui.qt.MainWindow import MainWindow
        from ok.ui.qt.start.StartTab import StartTab

        StartTab.__init__ = cls._original_start_init
        MainWindow.__init__ = cls._original_main_init
        if not cls._main_window_had_drag_enter:
            delattr(MainWindow, "dragEnterEvent")
        if not cls._main_window_had_drop:
            delattr(MainWindow, "dropEvent")
        config_transfer_patch._PATCH_INSTALLED = cls._original_flag

    def test_patch_wires_handlers_and_is_idempotent(self):
        from ok.ui.qt.MainWindow import MainWindow
        from ok.ui.qt.start.StartTab import StartTab

        self.assertTrue(config_transfer_patch._PATCH_INSTALLED)
        self.assertIs(MainWindow.dragEnterEvent, config_transfer_patch._drag_enter_event)
        self.assertIs(MainWindow.dropEvent, config_transfer_patch._drop_event)
        self.assertEqual(StartTab.__init__.__name__, "patched_start_init")
        self.assertEqual(MainWindow.__init__.__name__, "patched_main_init")

        # 重复安装应直接返回，不再叠加包装
        install_config_transfer_patch()
        self.assertEqual(StartTab.__init__.__name__, "patched_start_init")
        self.assertEqual(MainWindow.__init__.__name__, "patched_main_init")


if __name__ == "__main__":
    unittest.main()
