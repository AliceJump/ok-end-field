# -*- coding: utf-8 -*-
"""小地图区域检查任务：实例化/配置与"标注图可生成"的冒烟测试。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from ok.test.TaskTestCase import TaskTestCase
from src.config import config
from src.tasks.test.MinimapRegionCheck import MinimapRegionCheck


class TestMinimapRegionCheckTask(TaskTestCase):
    """验证小地图区域检查与里程计掩膜一致。"""

    task_class = MinimapRegionCheck
    config = config

    def test_task_instantiation_and_metadata(self):
        task = self.task
        self.assertEqual(task.name, "小地图区域检查")
        self.assertTrue(task.requires_foreground)
        for key in ("圆心x比例(占宽)", "圆心y比例(占高)", "外圈半径比例(占宽)",
                    "内圈半径比例(占宽)", "裁剪放大倍数", "标记外扩比例", "保存目录"):
            self.assertIn(key, task.default_config)
        # 默认几何与里程计一致
        self.assertAlmostEqual(task.default_config["圆心x比例(占宽)"], 0.084)
        self.assertAlmostEqual(task.default_config["圆心y比例(占高)"], 0.154)
        self.assertAlmostEqual(task.default_config["外圈半径比例(占宽)"], 0.044)
        self.assertAlmostEqual(task.default_config["内圈半径比例(占宽)"], 0.014)

    def test_run_writes_annotated_images(self):
        task = self.task
        frame = np.zeros((1440, 2560, 3), np.uint8)
        frame[120:340, 110:330] = 200          # 默认圆心附近放一块亮区（模拟小地图）
        task.next_frame = lambda *a, **k: frame

        saved = task.config.get("保存目录")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                task.config["保存目录"] = tmp
                task.run()
                files = sorted(Path(tmp).glob("*.png"))
                # 临时目录销毁前先取大小
                sizes = {f.name: f.stat().st_size for f in files}
            finally:
                task.config["保存目录"] = saved
        # 生成整帧标注图 + 放大图
        self.assertEqual(len(files), 2, [f.name for f in files])
        self.assertTrue(any("_region_full" in f.name for f in files))
        self.assertTrue(any("_region_zoom" in f.name for f in files))
        # 文件非空（确实写入了画面）
        for name, size in sizes.items():
            self.assertGreater(size, 0, name)

    def test_run_without_frame_warns(self):
        """截不到帧时不抛异常、不写文件。"""
        task = self.task
        task.next_frame = lambda *a, **k: None
        saved = task.config.get("保存目录")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                task.config["保存目录"] = tmp
                task.run()
                files = list(Path(tmp).glob("*.png"))
            finally:
                task.config["保存目录"] = saved
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
