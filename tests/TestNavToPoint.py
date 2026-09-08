# -*- coding: utf-8 -*-
"""导航到坐标点任务：无图像实例化与配置测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from ok.test.TaskTestCase import TaskTestCase
from src.config import config
from src.nav.nav_runner import RunnerConfig
from src.tasks.onetime.NavToPointTask import NavToPointTask


class TestNavToPointTask(TaskTestCase):
    task_class = NavToPointTask
    config = config

    def test_task_instantiation_and_metadata(self):
        task = self.task
        self.assertEqual(task.name, "导航到坐标点")
        for key in ("目标X", "目标Y", "目标Z", "地图id", "网格模式", "允许传送",
                    "吸附半径(米)", "允许冒险", "起点坐标(留空用当前位置)",
                    "yaw_per_pixel(负值)", "朝向偏移(度)", "到达半径(米)",
                    "到达目标半径(米)",
                    "卡住判定时间(秒)", "卡住最小位移(米)",
                    "content", "地图账号"):
            self.assertIn(key, task.default_config)
        self.assertEqual(task.default_config["网格模式"], "自动")
        self.assertEqual(
            task.config_type["网格模式"]["options"],
            ["自动", "3D(轨迹网格)", "2D(编辑器网格)"])
        self.assertFalse(task.default_config["允许传送"])
        self.assertTrue(task.default_config["允许冒险"])
        self.assertEqual(task.default_config["到达目标半径(米)"], 3.0)

    def test_runner_config_from_task_config(self):
        task = self.task
        cfg = task._runner_config()
        self.assertIsInstance(cfg, RunnerConfig)
        # 显式改配置验证映射关系（改完恢复，避免污染持久化配置）
        saved = {k: task.config.get(k) for k in
                 ("到达半径(米)", "卡住判定时间(秒)", "卡住最小位移(米)",
                  "吸附半径(米)", "允许传送")}
        try:
            task.config["允许传送"] = False
            task.config["到达半径(米)"] = 2.5
            task.config["卡住判定时间(秒)"] = 4.0
            task.config["卡住最小位移(米)"] = 0.7
            task.config["吸附半径(米)"] = 12.0
            cfg = task._runner_config()
            self.assertFalse(cfg.allow_teleport)
            self.assertAlmostEqual(cfg.arrive_radius, 2.5)
            self.assertAlmostEqual(cfg.stuck_window_s, 4.0)
            self.assertAlmostEqual(cfg.stuck_min_dist, 0.7)
            self.assertAlmostEqual(cfg.snap_radius, 12.0)
        finally:
            for k, v in saved.items():
                task.config[k] = v

    def test_start_point_parsing(self):
        task = self.task
        self.assertIsNone(task._start_point())  # 空默认
        task.config["起点坐标(留空用当前位置)"] = "1.5, -2.0, 3"
        self.assertEqual(task._start_point(), (1.5, -2.0, 3.0))
        task.config["起点坐标(留空用当前位置)"] = "1.5，-2.0，3"  # 全角逗号
        self.assertEqual(task._start_point(), (1.5, -2.0, 3.0))
        task.config["起点坐标(留空用当前位置)"] = "abc"
        self.assertIsNone(task._start_point())
        task.config["起点坐标(留空用当前位置)"] = ""

    def test_grid_loading_missing_file_returns_none(self):
        task = self.task
        grid = task._load_grid_for("不存在的图id")
        self.assertIsNone(grid)
        self.assertIsNone(task._grid)

    def test_grid_loading_real_file(self):
        task = self.task
        from pathlib import Path
        if not Path("assets/nav/map01.grid.json").exists():
            self.skipTest("缺少 assets/nav/map01.grid.json，先运行建网格 CLI")
        grid = task._load_grid_for("map01")
        self.assertIsNotNone(grid)
        self.assertGreater(len(grid.cells), 0)
        # 复用缓存：再次加载返回同一实例
        self.assertIs(task._load_grid_for("map01"), grid)


def _write_grid(root: Path, name: str, cells: list):
    (root / name).write_text(json.dumps({
        "origin": [0.0, 0.0, 0.0], "cell_size": 1.0,
        "cells": cells, "blocked": [], "teleports": [], "zips": [],
    }, ensure_ascii=False), encoding="utf-8")


class TestNavToPointGridMode(TaskTestCase):
    """网格模式（自动/3D/2D）与临时网格目录的加载逻辑。"""

    task_class = NavToPointTask
    config = config

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        task = self.task
        task._grid = None
        task._grid_map_id = None
        self._saved_dir = task._grid_dir
        task._grid_dir = Path(self._tmp.name)

    def tearDown(self):
        task = self.task
        task._grid = None
        task._grid_map_id = None
        task._grid_dir = self._saved_dir
        self._tmp.cleanup()

    def _load(self, map_id):
        task = self.task
        task._grid = None
        task._grid_map_id = None
        return task._load_grid_for(map_id)

    def test_mode_3d_uses_3d_grid(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m1.grid.json", [[0, 0, 0], [1, 0, 1]])
        self.task.config["网格模式"] = "3D(轨迹网格)"
        grid = self._load("m1")
        self.assertIsNotNone(grid)
        self.assertFalse(grid.is_2d)
        self.assertEqual(len(grid.cells), 2)

    def test_mode_3d_missing_returns_none(self):
        self.task.config["网格模式"] = "3D(轨迹网格)"
        self.assertIsNone(self._load("nope"))

    def test_mode_2d_ignores_3d_file(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m2.grid.json", [[0, 0, 0], [1, 0, 1]])      # 3D 同名前缀
        _write_grid(root, "m2_4_2d.grid.json", [[0, 0], [1, 1], [2, 2]])  # 2D 网格
        self.task.config["网格模式"] = "2D(编辑器网格)"
        grid = self._load("m2")
        self.assertIsNotNone(grid)
        self.assertTrue(grid.is_2d)
        self.assertEqual(len(grid.cells), 3)

    def test_mode_2d_accepts_renamed_2d_file(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m3.grid.json", [[0, 0], [1, 1]])  # 用户改名的 2D 文件
        self.task.config["网格模式"] = "2D(编辑器网格)"
        grid = self._load("m3")
        self.assertIsNotNone(grid)
        self.assertTrue(grid.is_2d)

    def test_mode_2d_missing_returns_none(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m4.grid.json", [[0, 0, 0]])  # 只有 3D 文件
        self.task.config["网格模式"] = "2D(编辑器网格)"
        self.assertIsNone(self._load("m4"))

    def test_mode_auto_prefers_3d(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m5.grid.json", [[0, 0, 0], [1, 0, 1]])
        _write_grid(root, "m5_4_2d.grid.json", [[0, 0]] * 10)
        self.task.config["网格模式"] = "自动"
        grid = self._load("m5")
        self.assertIsNotNone(grid)
        self.assertFalse(grid.is_2d)
        self.assertEqual(len(grid.cells), 2)

    def test_mode_auto_falls_back_to_2d(self):
        root = Path(self._tmp.name)
        _write_grid(root, "m6_4_2d.grid.json", [[x, 0] for x in range(7)])
        self.task.config["网格模式"] = "自动"
        grid = self._load("m6")
        self.assertIsNotNone(grid)
        self.assertTrue(grid.is_2d)
        self.assertEqual(len(grid.cells), 7)


if __name__ == "__main__":
    unittest.main()
