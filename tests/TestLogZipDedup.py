# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.patches.log_zip_dedup import (
    DEDUP_INFO_FILENAME,
    build_dedup_info,
    collect_image_duplicates,
    md5_hex,
    read_dedup_info,
    restore_duplicates,
)


class TestLogZipDedup(unittest.TestCase):

    def test_md5_hex_returns_same_hash_for_same_bytes(self):
        self.assertEqual(md5_hex(b"abc"), "900150983cd24fb0d6963f7d28e17f72")
        self.assertNotEqual(md5_hex(b"abc"), md5_hex(b"abd"))

    def test_collect_image_duplicates_keeps_first_and_records_rest(self):
        entries = [
            ("screenshots/a_original.png", b"same"),
            ("screenshots/b_original.png", b"other"),
            ("screenshots/c_original.png", b"same"),
            ("screenshots/d_original.png", b"same"),
        ]
        unique, duplicates = collect_image_duplicates(entries)
        self.assertEqual(
            [name for name, _ in unique],
            ["screenshots/a_original.png", "screenshots/b_original.png"],
        )
        self.assertEqual(len(duplicates), 2)
        self.assertEqual(duplicates[0]["kept"], "screenshots/a_original.png")
        self.assertEqual(duplicates[0]["duplicate"], "screenshots/c_original.png")
        self.assertEqual(duplicates[1]["duplicate"], "screenshots/d_original.png")
        self.assertEqual(
            {record["hash"] for record in duplicates},
            {md5_hex(b"same")},
        )

    def test_build_and_read_dedup_info_roundtrip(self):
        duplicates = [{"hash": "h1", "kept": "a.png", "duplicate": "b.png"}]
        info = build_dedup_info(duplicates)
        self.assertEqual(info["format"], 1)
        self.assertEqual(info["duplicates"], duplicates)
        self.assertIn("restore_log_screenshots.py", info["note"])

        info = build_dedup_info(duplicates, note="自定义说明")
        self.assertEqual(info["note"], "自定义说明")

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "test-log.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.writestr(DEDUP_INFO_FILENAME, json.dumps(info, ensure_ascii=False))
            self.assertEqual(read_dedup_info(zip_path), info)

    def test_read_dedup_info_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "plain-log.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.writestr("info.txt", "")
            self.assertIsNone(read_dedup_info(zip_path))

    def test_restore_duplicates_rebuilds_full_file_set(self):
        kept_bytes = b"identical-image-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "test-log.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.writestr("info.txt", "note")
                zipf.writestr("logs/ok-script.log", "log content")
                zipf.writestr("screenshots/a_original.png", kept_bytes)
                zipf.writestr("screenshots/b_original.png", b"unique")
                zipf.writestr(DEDUP_INFO_FILENAME, json.dumps(build_dedup_info([
                    {"hash": md5_hex(kept_bytes), "kept": "screenshots/a_original.png",
                     "duplicate": "screenshots/a_dup.png"},
                ])))

            output_dir = Path(tmp) / "restored"
            restored = restore_duplicates(zip_path, output_dir)

            self.assertEqual(restored, ["screenshots/a_dup.png"])
            self.assertEqual((output_dir / "screenshots/a_original.png").read_bytes(), kept_bytes)
            self.assertEqual((output_dir / "screenshots/a_dup.png").read_bytes(), kept_bytes)
            self.assertEqual((output_dir / "screenshots/b_original.png").read_bytes(), b"unique")
            self.assertEqual((output_dir / "logs/ok-script.log").read_text(), "log content")
            self.assertEqual((output_dir / "info.txt").read_text(), "note")
            self.assertTrue((output_dir / DEDUP_INFO_FILENAME).is_file())

    def test_restore_duplicates_without_info_extracts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "plain-log.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.writestr("screenshots/a_original.png", b"x")
            output_dir = Path(tmp) / "restored"
            restored = restore_duplicates(zip_path, output_dir)
            self.assertEqual(restored, [])
            self.assertEqual((output_dir / "screenshots/a_original.png").read_bytes(), b"x")

    def test_restore_skips_record_with_missing_kept_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "broken-log.zip"
            with zipfile.ZipFile(zip_path, "w") as zipf:
                zipf.writestr(DEDUP_INFO_FILENAME, json.dumps(build_dedup_info([
                    {"hash": "h", "kept": "screenshots/ghost.png",
                     "duplicate": "screenshots/a_dup.png"},
                ])))
            output_dir = Path(tmp) / "restored"
            restored = restore_duplicates(zip_path, output_dir)
            self.assertEqual(restored, [])
            self.assertFalse((output_dir / "screenshots/a_dup.png").exists())


if __name__ == "__main__":
    unittest.main()
