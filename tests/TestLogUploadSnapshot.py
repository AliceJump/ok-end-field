import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.patches import log_upload_patch


class TestLogUploadSnapshot(unittest.TestCase):
    def build_zip(self, root):
        with (
            patch("ok.og.config", {"gui_title": "test", "screenshots_folder": "screenshots"}),
            patch("ok.util.file.get_downloads_folder", return_value=str(root / "downloads")),
            patch.object(Path, "cwd", return_value=root),
        ):
            return log_upload_patch._build_logs_zip("snapshot test")

    def test_live_log_keeps_opening_prefix_while_more_records_arrive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            log = root / "logs" / "ok-script.log"
            initial = "最新错误\n".encode()
            log.write_bytes(initial)
            stat = os.fstat

            def append_later_record():
                with log.open("ab") as writer:
                    writer.write(b"later record\n")

            def append_after_snapshot(fd):
                result = stat(fd)
                append_later_record()
                return result

            with (
                patch.object(log_upload_patch.os, "fstat", side_effect=append_after_snapshot),
                patch.object(log_upload_patch.time, "sleep", side_effect=lambda _seconds: append_later_record()),
            ):
                archive = self.build_zip(root)
            with zipfile.ZipFile(archive) as zip_file:
                self.assertEqual(zip_file.read("logs/ok-script.log"), initial)
                self.assertEqual(zip_file.read("info.txt"), b"snapshot test")

    def test_log_rotated_before_open_does_not_abort_remaining_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            rotated = root / "logs" / "rotated.log"
            rotated.write_bytes(b"old log\n")
            (root / "logs" / "ok-script.log").write_bytes(b"remaining log\n")
            original_open = Path.open

            def rotate_before_open(path, *args, **kwargs):
                if path == rotated and args == ("rb",):
                    path.unlink()
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", new=rotate_before_open):
                archive = self.build_zip(root)
            with zipfile.ZipFile(archive) as zip_file:
                self.assertNotIn("logs/rotated.log", zip_file.namelist())
                self.assertEqual(zip_file.read("logs/ok-script.log"), b"remaining log\n")
                self.assertEqual(zip_file.read("info.txt"), b"snapshot test")

    def test_changing_screenshot_is_still_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "screenshots").mkdir()
            image = root / "screenshots" / "recent.png"
            Image.new("RGB", (10, 10), "red").save(image)
            os.utime(image, None)

            def change_image(_seconds):
                Image.new("RGB", (20, 20), "blue").save(image)

            with patch.object(log_upload_patch.time, "sleep", side_effect=change_image):
                archive = self.build_zip(root)
            with zipfile.ZipFile(archive) as zip_file:
                self.assertNotIn("screenshots/recent.png", zip_file.namelist())


if __name__ == "__main__":
    unittest.main()
