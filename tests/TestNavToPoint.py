# -*- coding: utf-8 -*-
"""导航到坐标点任务：无图像实例化与配置测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from ok.test.TaskTestCase import TaskTestCase
from src.config import config
from src.nav.nav_runner import RunnerConfig
from src.tasks.test.NavToPointTask import NavToPointTask


class TestNavToPointTask(TaskTestCase):
    task_class = NavToPointTask
    config = config

    def test_task_instantiation_and_metadata(self):
        task = self.task
        self.assertEqual(task.name, "导航到坐标点")
        # #4 回归：导航驱动游戏移动/视角，需前台模式（后台模式下启用被拦截）
        self.assertTrue(task.requires_foreground)
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

    def test_heading_reuses_template_interface(self):
        """#6 回归：朝向走「箭头角度实时读取」同源接口 get_arrow_angle，且复用传入的帧。

        不再优先几何法 infer_arrow_heading（实测不可信）；关闭低分沿用旧角的平滑；
        屏幕角 45°(右/东) 按朝向偏移映射为世界角。
        """
        from unittest import mock

        task = self.task
        offset = float(task.config.get('朝向偏移(度)', 0.0))
        frame = object()          # 哨兵：验证用的是外部传入的那一帧
        task._controls.begin_tick()
        with mock.patch.object(task, "get_arrow_angle", return_value=(45.0, 0.9)) as m:
            angle, score = task._read_arrow(frame)
        self.assertAlmostEqual(angle, 45.0)
        self.assertAlmostEqual(score, 0.9)
        # 关键：必须把外部帧透传进去，**不能自己取帧**。旧实现不传 target_image，
        # get_arrow_angle 内部会再调 next_frame()——每轮多取一帧，而且窗口不可捕获时
        # next_frame() 是无限阻塞（不是超时返回），循环就此静默冻死。
        _, kw = m.call_args
        self.assertIs(kw.get("target_image"), frame)
        self.assertIs(kw.get("smoothing_threshold"), None)  # 低分沿用旧角已关
        self.assertIs(kw.get("two_stage"), True)
        self.assertEqual(kw.get("benchmark_width"), 2560)
        # 屏幕角 45°(右/东) -> 世界 yaw = (-45 + 朝向偏移) mod 360
        yaw = task._controls.heading()
        self.assertIsNotNone(yaw)
        self.assertAlmostEqual(yaw, (315.0 + offset) % 360.0)
        self.assertAlmostEqual(task._controls.last_arrow_angle, 45.0)

    def test_heading_gates_low_score(self):
        """低置信度（箭头被遮挡/不可读）不给朝向：先停，绝不用旧角或几何法转向。"""
        from unittest import mock

        task = self.task
        task._controls.begin_tick()
        with mock.patch.object(task, "get_arrow_angle", return_value=(45.0, 0.1)):
            angle, _ = task._read_arrow(object())
        self.assertIsNone(angle)
        self.assertIsNone(task._controls.heading())

    def test_heading_cleared_each_tick(self):
        """每拍开头清朝向：本拍读不出箭头时不得沿用上一拍的角度。"""
        from unittest import mock

        task = self.task
        with mock.patch.object(task, "get_arrow_angle", return_value=(45.0, 0.9)):
            task._read_arrow(object())
        self.assertIsNotNone(task._controls.heading())
        task._controls.begin_tick()
        self.assertIsNone(task._controls.heading())

    def test_fusion_arrow_func_wired_to_task(self):
        """融合必须注入 arrow_func：否则朝向会退回自己取帧（每轮多取一帧）。"""
        task = self.task
        fusion = task._build_fusion()
        # 绑定方法每次访问都是新对象，比较底层函数与宿主
        self.assertIs(fusion._arrow_func.__func__, task._read_arrow.__func__)
        self.assertIs(fusion._arrow_func.__self__, task)

    def test_turn_is_non_blocking(self):
        """转向不再阻塞：只发鼠标位移 + 进入 settle 阶段，收尾交给主循环的 tick()。"""
        from unittest import mock

        task = self.task
        task._controls.release()
        with mock.patch.object(task, "active_and_send_mouse_delta") as send:
            task._controls.turn(30.0)
        send.assert_called_once()
        self.assertFalse(task._controls._w_down)              # 转向当下不按 W
        self.assertEqual(task._controls._turn_stage, "settle")
        self.assertIsNotNone(task._controls._stage_until)
        task._controls.release()

    def test_turn_cycle_steps_with_w_then_releases(self):
        """视角稳定到期后必须真的按 W 迈一步，再按期松开。

        这个游戏转视角后角色身体（=小地图箭头）不会立即转向，**必须按前进迈一步**才
        更新朝向——这是朝向闭环能收敛的唯一途径，不能只在状态机里"记一笔"。
        """
        from unittest import mock

        task = self.task
        task._controls.release()
        with mock.patch.object(task, "active_and_send_mouse_delta"), \
                mock.patch.object(task, "send_key_down") as down, \
                mock.patch.object(task, "send_key_up") as up:
            task._controls.turn(30.0)
            task._controls.tick(task._controls._stage_until + 0.01)   # settle 到期 -> 迈一步
            down.assert_called_once_with("w")
            self.assertEqual(task._controls._turn_stage, "step")
            task._controls.tick(task._controls._stage_until + 0.01)   # step 到期 -> 松 W
            up.assert_called_once_with("w")
            self.assertEqual(task._controls._turn_stage, "idle")
            task._controls.release()

    def test_turn_ignores_new_request_until_cycle_done(self):
        """周期未收尾时忽略新转向请求。

        调用方 `_move_toward` 每拍都会重新判一次朝向；箭头要等"迈一步"之后才更新，
        所以这段窗口内必然会重复请求转向。若允许打断，就会无限原地转。
        """
        from unittest import mock

        task = self.task
        task._controls.release()
        with mock.patch.object(task, "active_and_send_mouse_delta") as send, \
                mock.patch.object(task, "send_key_down"):
            task._controls.turn(30.0)
            task._controls.tick(task._controls._stage_until + 0.01)   # 进入 step
            self.assertEqual(task._controls._turn_stage, "step")
            task._controls.turn(80.0)                                 # 周期内：应被忽略
            self.assertEqual(send.call_count, 1)
            self.assertEqual(task._controls._turn_stage, "step")
            task._controls.release()

    def test_walk_cannot_release_step(self):
        """「迈一步」期间 walk(False) 不得松开 W。

        因为 `_move_toward` 每拍都是先 `walk(False)` 再 `turn(delta)`——若不保护，
        那一步会被立刻松开，身体永远追不上视角。
        """
        from unittest import mock

        task = self.task
        task._controls.release()
        with mock.patch.object(task, "active_and_send_mouse_delta"), \
                mock.patch.object(task, "send_key_down"), \
                mock.patch.object(task, "send_key_up") as up:
            task._controls.turn(30.0)
            task._controls.tick(task._controls._stage_until + 0.01)   # 进入 step
            task._controls.walk(False)                                # 调用方先松 W
            up.assert_not_called()
            task._controls.release()

    def test_release_always_frees_w(self):
        """release() 必须无条件收尾：清周期并保证 W 已松开（防止卡键一直往前走）。"""
        from unittest import mock

        task = self.task
        task._controls.release()
        with mock.patch.object(task, "active_and_send_mouse_delta"), \
                mock.patch.object(task, "send_key_down"), \
                mock.patch.object(task, "send_key_up") as up:
            task._controls.turn(30.0)
            task._controls.tick(task._controls._stage_until + 0.01)   # step：W 按住
            self.assertTrue(task._controls._owns_w)
            task._controls.release()
            up.assert_called_once_with("w")
            self.assertFalse(task._controls._owns_w)
            self.assertEqual(task._controls._turn_stage, "idle")

    def test_fused_pos_without_frame_is_dead_and_skips_odometry(self):
        """frame=None：直接判位置源失效，绝不让里程计再取一帧。

        旧实现把 None 透传给 fusion，fusion 转交里程计 sample(frame=None)，
        里程计再自己取一次帧——又一次无限阻塞。
        """
        from unittest import mock

        task = self.task
        task._build_fusion()
        with mock.patch.object(task._odometry, "sample") as sample:
            cur, health, why = task._fused_pos("map01", 1.0, 2.0, 3.0, None)
        sample.assert_not_called()
        self.assertEqual(cur, (1.0, 2.0, 3.0))
        self.assertEqual(health, "dead")
        self.assertTrue(why)

    def test_position_health_fresh_stale_dead(self):
        """健康度：ok=True 记 fresh；连续无有效样本先 stale、超阈值转 dead。"""
        from unittest import mock

        task = self.task
        task._build_fusion()
        task._stale_ticks = 0
        with mock.patch.object(task._odometry, "last_result",
                               return_value={"ok": True, "sampled": True, "reason": "ok"}):
            self.assertEqual(task._position_health()[0], "fresh")
        with mock.patch.object(task._odometry, "last_result",
                               return_value={"ok": False, "sampled": True,
                                             "reason": "low_response"}):
            self.assertEqual(task._position_health()[0], "stale")
            for _ in range(10):
                state, _why = task._position_health()
            self.assertEqual(state, "dead")           # 连续无有效样本 -> dead

    def test_fused_pos_reraises_control_flow_exception(self):
        """框架控制流异常必须向外抛出，不能被 `except Exception` 吞掉。

        吞掉的后果：TaskExecutor 收不到结束信号，任务停不下来、executor 状态错乱。
        """
        from unittest import mock

        from ok import TaskDisabledException

        task = self.task
        task._build_fusion()
        with mock.patch.object(task._fusion, "state",
                               side_effect=TaskDisabledException()):
            with self.assertRaises(TaskDisabledException):
                task._fused_pos("map01", 1.0, 2.0, 3.0, object())

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
