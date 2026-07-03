# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tasks.daily.finally_file import create_daily_finally_note, create_daily_summary_report, resolve_daily_finally_directory


class TestDailyTaskFinallyFile(unittest.TestCase):
    def test_resolve_daily_finally_directory_prefers_desktop(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            desktop = home / "Desktop"
            desktop.mkdir()

            with patch("src.tasks.daily.finally_file.windll.shell32.SHGetFolderPathW", return_value=1):
                with patch("src.tasks.daily.finally_file.Path.home", return_value=home):
                    self.assertEqual(resolve_daily_finally_directory(), desktop)

    def test_create_daily_finally_note_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            target_subdir = directory / "惊喜口牙"
            target_subdir.mkdir()
            existing_file = target_subdir / "惊喜口牙.txt"
            existing_file.write_text("old", encoding="utf-8")

            created_file = create_daily_finally_note(directory, content="new")

            self.assertEqual(existing_file.read_text(encoding="utf-8"), "old")
            self.assertNotEqual(created_file, existing_file)
            self.assertTrue(created_file.name.startswith("惊喜口牙_压根_QWQ"))
            self.assertEqual(created_file.read_text(encoding="utf-8"), "new")
            self.assertEqual(created_file.parent.name, "惊喜口牙")

    def test_create_daily_finally_note_deletes_old_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 创建一个超过7天的旧文件
            target_subdir = directory / "惊喜口牙"
            target_subdir.mkdir()
            old_file_beyond = target_subdir / "惊喜口牙_压根_QWQ000.txt"
            old_file_beyond.write_text("old content beyond 7 days", encoding="utf-8")
            
            # 手动设置文件修改时间为8天前
            old_mtime = time.time() - (8 * 24 * 3600)
            os.utime(old_file_beyond, (old_mtime, old_mtime))
            
            self.assertTrue(old_file_beyond.exists())
            
            # 创建新的惊喜文件
            created_file = create_daily_finally_note(directory, keep_days=7)
            
            # 验证超过7天的旧文件已删除，新文件已创建
            self.assertFalse(old_file_beyond.exists(), "超过7天的旧文件应该被删除")
            self.assertTrue(created_file.exists(), "新的惊喜文件应该存在")

    def test_create_daily_finally_note_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 创建一个最近3天的文件
            target_subdir = directory / "惊喜口牙"
            target_subdir.mkdir()
            recent_file = target_subdir / "惊喜口牙_压根_QWQ001.txt"
            recent_file.write_text("recent content within 7 days", encoding="utf-8")
            
            # 手动设置文件修改时间为3天前
            recent_mtime = time.time() - (3 * 24 * 3600)
            os.utime(recent_file, (recent_mtime, recent_mtime))
            
            self.assertTrue(recent_file.exists())
            
            # 创建新的惊喜文件
            created_file = create_daily_finally_note(directory, keep_days=7)
            
            # 验证最近的文件被保留，新文件已创建
            self.assertTrue(recent_file.exists(), "最近7天内的旧文件应该被保留")
            self.assertTrue(created_file.exists(), "新的惊喜文件应该存在")

    def test_create_daily_summary_report_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 测试成功情况（无失败任务）
            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }
            
            created_file = create_daily_summary_report(directory, summary_info)
            
            self.assertTrue(created_file.exists())
            self.assertTrue(created_file.name.startswith("日常执行情况_"))
            self.assertEqual(created_file.parent.name, "日常执行情况")
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("执行轮数: 1", content)
            self.assertIn("✅ 所有任务执行成功！", content)

    def test_create_daily_summary_report_with_per_round_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)

            # 测试包含每轮账号信息的汇总
            summary_info = {
                "actual_repeat_total": 1,
                "per_round": [
                    {
                        "round": 1,
                        "account_user": "alice",
                        "account_id": "alice-1234",
                        "success": ["任务A"],
                        "failed": [],
                        "skipped": [],
                        "all": ["任务A"],
                    }
                ],
            }

            created_file = create_daily_summary_report(directory, summary_info)

            self.assertTrue(created_file.exists())
            content = created_file.read_text(encoding="utf-8")
            # 断言文件中包含账号显示内容
            self.assertIn("第 1 轮 (账号: alice)", content)

    def test_create_daily_summary_report_with_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 测试失败情况
            summary_info = {
                "all_fail_tasks": [(1, ["任务A", "任务B"]), (2, ["任务C"])],
                "actual_repeat_total": 2,
            }
            
            created_file = create_daily_summary_report(directory, summary_info)
            
            self.assertTrue(created_file.exists())
            self.assertEqual(created_file.parent.name, "日常执行情况")
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("执行轮数: 2", content)
            self.assertIn("❌ 失败任务统计:", content)
            self.assertIn("第 1 轮: 任务A, 任务B", content)
            self.assertIn("第 2 轮: 任务C", content)

    def test_create_daily_summary_report_with_failure_details_grouped_by_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)

            summary_info = {
                "actual_repeat_total": 2,
                "per_round": [
                    {
                        "round": 1,
                        "account_user": "Alice",
                        "account_id": "alice-1",
                        "success": [],
                        "failed": ["任务A"],
                        "skipped": [],
                        "all": ["任务A"],
                    },
                    {
                        "round": 2,
                        "account_user": "Bob",
                        "account_id": "bob-1",
                        "success": [],
                        "failed": ["任务B"],
                        "skipped": [],
                        "all": ["任务B"],
                    },
                ],
                "failure_details": {
                    "alice-1": {
                        "任务A": "Alice 的失败消息",
                    },
                    "bob-1": {
                        "任务B": "Bob 的失败消息",
                    },
                },
            }

            created_file = create_daily_summary_report(directory, summary_info)

            self.assertTrue(created_file.exists())
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("失败消息:", content)
            self.assertIn("=== 账号: Alice ===", content)
            self.assertIn("- 任务A : Alice 的失败消息", content)
            self.assertIn("=== 账号: Bob ===", content)
            self.assertIn("- 任务B : Bob 的失败消息", content)

    def test_create_daily_summary_report_deletes_old_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 创建一个超过7天的旧文件
            target_subdir = directory / "日常执行情况"
            target_subdir.mkdir()
            old_file_beyond = target_subdir / "日常执行情况_20260101_120000.txt"
            old_file_beyond.write_text("old content beyond 7 days", encoding="utf-8")
            
            # 手动设置文件修改时间为8天前
            old_mtime = time.time() - (8 * 24 * 3600)
            os.utime(old_file_beyond, (old_mtime, old_mtime))
            
            self.assertTrue(old_file_beyond.exists())
            
            # 创建新的汇总文件
            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }
            created_file = create_daily_summary_report(directory, summary_info, keep_days=7)
            
            # 验证超过7天的旧文件已删除，新文件已创建
            self.assertFalse(old_file_beyond.exists(), "超过7天的旧文件应该被删除")
            self.assertTrue(created_file.exists(), "新的汇总文件应该存在")

    def test_create_daily_summary_report_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            
            # 创建一个最近3天的文件
            target_subdir = directory / "日常执行情况"
            target_subdir.mkdir()
            recent_file = target_subdir / "日常执行情况_20260515_120000.txt"
            recent_file.write_text("recent content within 7 days", encoding="utf-8")
            
            # 手动设置文件修改时间为3天前
            recent_mtime = time.time() - (3 * 24 * 3600)
            os.utime(recent_file, (recent_mtime, recent_mtime))
            
            self.assertTrue(recent_file.exists())
            
            # 创建新的汇总文件
            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }
            created_file = create_daily_summary_report(directory, summary_info, keep_days=7)
            
            # 验证最近的文件被保留，新文件已创建
            self.assertTrue(recent_file.exists(), "最近7天内的旧文件应该被保留")
            self.assertTrue(created_file.exists(), "新的汇总文件应该存在")


if __name__ == "__main__":
    unittest.main()
