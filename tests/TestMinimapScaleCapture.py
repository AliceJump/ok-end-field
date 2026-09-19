# -*- coding: utf-8 -*-
"""小地图比例尺采集任务：实例化/配置与"成对落盘"的冒烟测试。

定位源用假对象替身：只驱动本任务自己的逻辑（等待连续静止、落盘、索引），
不依赖游戏窗口或真实 WS。
"""

import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
from ok.test.TaskTestCase import TaskTestCase

from src.config import config
from src.tasks.test.MinimapScaleCapture import (
    CONFIG_INTERVAL,
    CONFIG_REST_TICKS,
    CONFIG_SAMPLES,
    CONFIG_SAVE_DIR,
    CONFIG_TIMEOUT,
    MinimapScaleCapture,
)

# 用真实采集过的分辨率(1920x1080)当默认帧；另有用例换 2560x1440 验证分组会跟着变
FRAME_W, FRAME_H = 1920, 1080
OTHER_W, OTHER_H = 2560, 1440


def _state(*, rest=True, ws=(123.5, -456.25), x=123.4, z=-456.2, map_id="map02", heading=90.0):
    return {
        "rest": rest,
        "ws": ws,
        "x": x,
        "z": z,
        "map_id": map_id,
        "heading": heading,
    }


class _FakePositionTask:
    """MinimapPositionTask 的替身：按预设序列吐状态。"""

    enabled = True

    def __init__(self, states):
        self._states = list(states)
        self.calls = 0

    def start_minimap_position(self, *, wait_stable=True):
        return True

    def minimap_position(self, frame=None, *, now=None, feed_ws=True, allow_sync=True):
        st = self._states[min(self.calls, len(self._states) - 1)]
        self.calls += 1
        return dict(st)


class TestMinimapScaleCaptureTask(TaskTestCase):
    """验证采样判据与落盘产物。"""

    task_class = MinimapScaleCapture
    config = config

    def _prepare(self, states, tmp, *, target=1, rest_ticks=3, frame_size=(FRAME_W, FRAME_H)):
        task = self.task
        task.in_world = lambda: True
        frame_w, frame_h = frame_size
        task.next_frame = lambda *a, **k: np.zeros((frame_h, frame_w, 3), np.uint8)
        fake = _FakePositionTask(states)
        task.get_task_by_class = lambda cls: fake
        task.config[CONFIG_SAVE_DIR] = tmp
        task.config[CONFIG_SAMPLES] = target
        task.config[CONFIG_REST_TICKS] = rest_ticks
        task.config[CONFIG_INTERVAL] = 0.05   # 让多拍循环在测试里跑得快
        task.config[CONFIG_TIMEOUT] = 30.0
        return task, fake

    def test_task_instantiation_and_metadata(self):
        task = self.task
        self.assertEqual(task.name, "小地图比例尺采集")
        self.assertEqual(task.group_name, "工具与调试")
        self.assertTrue(task.requires_foreground)
        self.assertTrue(task.visible)
        for key in (CONFIG_INTERVAL, CONFIG_SAMPLES, CONFIG_REST_TICKS,
                    CONFIG_TIMEOUT, CONFIG_SAVE_DIR):
            self.assertIn(key, task.default_config)
            self.assertIn(key, task.config_description)
        # 默认目录必须在 logs/ 下：screenshots/ 每次启动会被清空
        self.assertTrue(task.default_config[CONFIG_SAVE_DIR].startswith("logs/"))
        # 配置读取小工具必须存在（本任务不继承带这几个 helper 的 mixin）
        self.assertIsInstance(task._cfg_float("不存在", 1.5), float)
        self.assertIsInstance(task._cfg_int("不存在", 3), int)

    def test_capture_writes_frame_and_index(self):
        """连续静止 N 拍后落盘：整帧 + index.json 里的分辨率与 WS 真值。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 前两拍还在动，第三拍起静止 -> 需要另外 3 拍才满足"静止稳定拍数"
            states = [_state(rest=False), _state(rest=False)] + [_state()] * 3
            task, fake = self._prepare(states, tmp, target=1, rest_ticks=3)
            task.run()

            pngs = sorted(Path(tmp).rglob("*.png"))
            self.assertEqual(len(pngs), 1, [str(p) for p in pngs])
            self.assertGreater(pngs[0].stat().st_size, 0)

            index = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["samples"]), 1)
            sample = index["samples"][0]
            self.assertEqual(sample["file"], f"1920x1080/map02/{pngs[0].name}")
            self.assertEqual((sample["width"], sample["height"]), (FRAME_W, FRAME_H))
            self.assertEqual(sample["x"], 123.4)
            self.assertEqual(sample["z"], -456.2)
            self.assertEqual(sample["map_id"], "map02")
            self.assertEqual(sample["heading"], 90.0)
            # region 是 region_geometry(width, height) 的四个值
            self.assertEqual(sample["region"], [161.28, 166.32, 26.88, 84.48])
            # 落盘发生在"第 5 拍"（2 拍移动 + 3 拍静止），不是第一拍静止就采
            self.assertEqual(fake.calls, 5)

    def test_frames_are_grouped_by_resolution_and_map(self):
        """按 分辨率/地图 两级归档；索引里的 file 是相对保存目录的路径，并带 group。"""
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state()], tmp, target=1, rest_ticks=1,
                                    frame_size=(OTHER_W, OTHER_H))
            task.run()

            expected_dir = Path(tmp) / "2560x1440" / "map02"
            pngs = sorted(expected_dir.glob("*.png"))
            self.assertEqual(len(pngs), 1, list(Path(tmp).rglob("*.png")))
            # 分组目录与 index.json 平级，截图不散在根目录
            self.assertEqual(set(Path(tmp).iterdir()),
                             {Path(tmp) / "index.json", Path(tmp) / "2560x1440"})

            sample = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))["samples"][0]
            self.assertEqual(sample["group"], "2560x1440/map02")
            self.assertEqual(sample["file"], f"2560x1440/map02/{pngs[0].name}")
            self.assertTrue((Path(tmp) / sample["file"]).is_file())
            # region 跟着新分辨率走
            self.assertEqual(sample["region"], [215.04, 221.76, 35.84, 112.64])

    def test_map_id_cannot_escape_save_dir(self):
        """map_id 来自 WS 报文，不能靠它把样本写到保存目录之外。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evil = "map02/../../etc"
            task, _ = self._prepare([_state(map_id=evil)], tmp, target=1, rest_ticks=1)
            task.run()

            pngs = sorted(root.rglob("*.png"))
            self.assertEqual(len(pngs), 1, [str(p) for p in pngs])
            self.assertTrue(pngs[0].is_relative_to(root), pngs[0])
            sample = json.loads((root / "index.json").read_text(encoding="utf-8"))["samples"][0]
            # 斜杠与点都被替换成下划线，仍是保存目录下的单层目录名；
            # 原始 map_id 值原样保留在字段里，不因为净化而丢信息。
            self.assertEqual(sample["group"], "1920x1080/map02_______etc")
            self.assertEqual(sample["map_id"], evil)
            self.assertTrue((root / sample["file"]).is_file())

    def test_each_image_gets_a_matching_sidecar(self):
        """每张图旁边有同名 .json，且内容与该图在 index.json 里的记录**逐字段相同**。

        两份记录必须同源——各写各的迟早会漂，而 region / x / z 都是要拿去算数的字段。
        """
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state()], tmp, target=2, rest_ticks=1)
            task.run()

            group_dir = Path(tmp) / "1920x1080" / "map02"
            # 2 张图 + 2 份 sidecar，sidecar 与图同目录、不散到别处
            self.assertEqual(len(list(group_dir.iterdir())), 4, list(group_dir.iterdir()))

            index = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["samples"]), 2)
            for sample in index["samples"]:
                png = Path(tmp) / sample["file"]
                sidecar = png.with_suffix(".json")
                self.assertTrue(png.is_file(), png)
                self.assertTrue(sidecar.is_file(), sidecar)
                self.assertEqual(sidecar.parent, png.parent)
                self.assertEqual(sidecar.stem, png.stem)
                self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8")), sample)

    def test_sidecar_failure_does_not_lose_the_image(self):
        """sidecar 写失败时，图片与该图在总索引里的记录都必须完好保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state()], tmp, target=1, rest_ticks=1)
            warned = []
            task.log_warning = lambda msg, **k: warned.append(str(msg))

            original = Path.write_text

            def boom(self, *a, **k):
                # 只让 sidecar 失败，index.json 照常写
                if self.suffix == ".json" and self.name != "index.json":
                    raise OSError("disk full")
                return original(self, *a, **k)

            Path.write_text = boom
            try:
                task.run()
            finally:
                Path.write_text = original

            sample = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))["samples"][0]
            png = Path(tmp) / sample["file"]
            self.assertTrue(png.is_file(), png)
            self.assertEqual(png.stat().st_size > 0, True)
            self.assertFalse(png.with_suffix(".json").exists())
            self.assertTrue(any("sidecar" in w for w in warned), warned)

    def test_sidecar_oserror_is_swallowed_and_logged(self):
        """sidecar 抛 OSError 时只告警、不向上抛——图片和总索引不能因此丢。"""
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state()], tmp, target=1, rest_ticks=1)
            warned = []
            task.log_warning = lambda msg, **k: warned.append(str(msg))

            original = Path.write_text

            def boom(self, *a, **k):
                if self.suffix == ".json":
                    raise OSError("disk full")
                return original(self, *a, **k)

            Path.write_text = boom
            try:
                # 不该抛出去
                task._write_sidecar(Path(tmp), "1920x1080/map02/x.png", {"a": 1})
            finally:
                Path.write_text = original
            self.assertTrue(any("sidecar" in w for w in warned), warned)

    def test_index_merges_across_runs(self):
        """跑两次任务，两次的样本都在 index.json 里。

        一次运行采多张，但用户为了换位置必然会跑多次；若每次都从空列表整份覆盖，
        前几次的 PNG 还在盘上、坐标却被抹掉，就成了对不上号的孤儿图。
        """
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state()], tmp, target=1, rest_ticks=1)
            task.run()
            first = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(first["samples"]), 1)

            # 文件名带秒级时间戳：等过秒边界，保证第二次是另一张图而不是同名覆盖
            prev_stamp = Path(first["samples"][0]["file"]).name[:15]
            for _ in range(60):
                if datetime.now().strftime("%Y%m%d_%H%M%S") != prev_stamp:
                    break
                time.sleep(0.05)

            task, _ = self._prepare([_state(x=200.0, z=300.0)], tmp, target=1, rest_ticks=1)
            task.run()

            merged = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(merged["samples"]), 2, merged["samples"])
            self.assertEqual({s["x"] for s in merged["samples"]}, {123.4, 200.0})
            # created 保留最早那次，updated 是最后写入
            self.assertEqual(merged["created"], first["created"])
            self.assertIn("updated", merged)
            # 两张图连同各自的 sidecar 都在
            for sample in merged["samples"]:
                png = Path(tmp) / sample["file"]
                self.assertTrue(png.is_file(), png)
                self.assertTrue(png.with_suffix(".json").is_file(), png)

    def test_broken_existing_index_is_replaced_not_fatal(self):
        """盘上已有的 index.json 损坏时照常采集，只告警并覆盖它。"""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "index.json").write_text("{ not json", encoding="utf-8")
            task, _ = self._prepare([_state()], tmp, target=1, rest_ticks=1)
            warned = []
            task.log_warning = lambda msg, **k: warned.append(str(msg))
            task.run()

            data = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["samples"]), 1)
            self.assertTrue(any("index.json" in w for w in warned), warned)

    def test_missing_map_id_falls_back_to_unknown_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            task, _ = self._prepare([_state(map_id=None)], tmp, target=1, rest_ticks=1)
            task.run()
            sample = json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8"))["samples"][0]
            self.assertEqual(sample["group"], "1920x1080/unknown")
            self.assertTrue((Path(tmp) / sample["file"]).is_file())

    def test_never_rest_captures_nothing(self):
        """一直判为移动中：不落盘、不抛异常。"""
        with tempfile.TemporaryDirectory() as tmp:
            states = [_state(rest=False)]
            task, _ = self._prepare(states, tmp, target=1, rest_ticks=2)
            task.config[CONFIG_TIMEOUT] = 0.3
            task.run()
            self.assertEqual(list(Path(tmp).rglob("*.png")), [])

    def test_missing_ws_breaks_the_still_streak(self):
        """中间缺 WS 真值的那拍必须清零连续计数，不能跨过去凑满静止拍数。

        序列 = 静止、静止、无WS、静止、静止……（之后一直是静止）。
        正确：第 3 拍清零，第 4/5 拍重新数到 2，第 6 拍数满 3 才落盘。
        错误（不清零）：第 3 拍就凑满 3 拍，第 3 拍落盘。
        """
        with tempfile.TemporaryDirectory() as tmp:
            states = [_state(), _state(), _state(ws=None), _state(), _state()]
            task, fake = self._prepare(states, tmp, target=1, rest_ticks=3)
            task.config[CONFIG_TIMEOUT] = 0.8
            task.run()

            pngs = list(Path(tmp).rglob("*.png"))
            self.assertEqual(len(pngs), 1, [p.name for p in pngs])
            self.assertEqual(fake.calls, 6, f"落盘发生在第 {fake.calls} 拍，说明连续计数没被清零")

    def test_requires_position_task(self):
        """没有定位触发任务时不硬跑。"""
        task = self.task
        task.in_world = lambda: True
        task.get_task_by_class = lambda cls: None
        with tempfile.TemporaryDirectory() as tmp:
            task.config[CONFIG_SAVE_DIR] = tmp
            task.run()
            self.assertEqual(list(Path(tmp).glob("*")), [])


if __name__ == "__main__":
    unittest.main()
