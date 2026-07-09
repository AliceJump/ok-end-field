# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path

from src.tasks.daily.finally_file import create_task_summary_report


class _MockTask:
    """用于测试的模拟任务对象。"""
    def __init__(self, name: str = "日常任务"):
        self.name = name


class TestTaskSummaryReport(unittest.TestCase):
    def test_create_summary_report_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }

            created_file = create_task_summary_report(task, base_dir, summary_info)

            self.assertTrue(created_file.exists())
            self.assertTrue(created_file.name.startswith("日常任务_"))
            self.assertEqual(created_file.parent.name, "日常任务")
            self.assertEqual(created_file.parent.parent.name, "ok-ef")
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("执行轮数: 1", content)
            self.assertIn("✅ 所有任务执行成功！", content)

    def test_create_summary_report_with_per_round_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

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

            created_file = create_task_summary_report(task, base_dir, summary_info)

            self.assertTrue(created_file.exists())
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("第 1 轮 (账号: alice)", content)

    def test_create_summary_report_with_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

            summary_info = {
                "all_fail_tasks": [(1, ["任务A", "任务B"]), (2, ["任务C"])],
                "actual_repeat_total": 2,
            }

            created_file = create_task_summary_report(task, base_dir, summary_info)

            self.assertTrue(created_file.exists())
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("执行轮数: 2", content)
            self.assertIn("❌ 失败任务统计:", content)
            self.assertIn("第 1 轮: 任务A, 任务B", content)
            self.assertIn("第 2 轮: 任务C", content)

    def test_create_summary_report_with_failure_details_grouped_by_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

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

            created_file = create_task_summary_report(task, base_dir, summary_info)

            self.assertTrue(created_file.exists())
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("失败消息:", content)
            self.assertIn("=== 账号: Alice ===", content)
            self.assertIn("- 任务A : Alice 的失败消息", content)
            self.assertIn("=== 账号: Bob ===", content)
            self.assertIn("- 任务B : Bob 的失败消息", content)

    def test_create_summary_report_deletes_old_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

            # 创建一个超过7天的旧文件
            target_dir = base_dir / "ok-ef" / "日常任务"
            target_dir.mkdir(parents=True)
            old_file_beyond = target_dir / "日常任务_20260101_120000.txt"
            old_file_beyond.write_text("old content beyond 7 days", encoding="utf-8")

            # 手动设置文件修改时间为8天前
            old_mtime = time.time() - (8 * 24 * 3600)
            os.utime(old_file_beyond, (old_mtime, old_mtime))

            self.assertTrue(old_file_beyond.exists())

            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }
            created_file = create_task_summary_report(task, base_dir, summary_info, keep_days=7)

            self.assertFalse(old_file_beyond.exists(), "超过7天的旧文件应该被删除")
            self.assertTrue(created_file.exists(), "新的汇总文件应该存在")

    def test_create_summary_report_keeps_recent_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask()

            # 创建一个最近3天的文件
            target_dir = base_dir / "ok-ef" / "日常任务"
            target_dir.mkdir(parents=True)
            recent_file = target_dir / "日常任务_20260515_120000.txt"
            recent_file.write_text("recent content within 7 days", encoding="utf-8")

            # 手动设置文件修改时间为3天前
            recent_mtime = time.time() - (3 * 24 * 3600)
            os.utime(recent_file, (recent_mtime, recent_mtime))

            self.assertTrue(recent_file.exists())

            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }
            created_file = create_task_summary_report(task, base_dir, summary_info, keep_days=7)

            self.assertTrue(recent_file.exists(), "最近7天内的旧文件应该被保留")
            self.assertTrue(created_file.exists(), "新的汇总文件应该存在")

    def test_create_summary_report_custom_task_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            task = _MockTask(name="自定义任务")

            summary_info = {
                "all_fail_tasks": [],
                "actual_repeat_total": 1,
            }

            created_file = create_task_summary_report(task, base_dir, summary_info)

            self.assertTrue(created_file.exists())
            self.assertTrue(created_file.name.startswith("自定义任务_"))
            self.assertEqual(created_file.parent.name, "自定义任务")
            self.assertEqual(created_file.parent.parent.name, "ok-ef")
            content = created_file.read_text(encoding="utf-8")
            self.assertIn("自定义任务执行情况汇总", content)


if __name__ == "__main__":
    unittest.main()
