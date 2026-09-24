"""TestPulseProbe — 独立白色脉冲探针的记录规则。

覆盖：上升沿落盘字段、持续白只记一次、常态不记、全屏闪光过滤、
节流、开关关闭、队伍映射（有/无队伍两种路径）、战斗循环挂载点。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from src.core.BattleConfig import KEY_PULSE_PROBE
from src.image.pulse_probe import PulseProbe

W, H = 1280, 720
# 与 RECOMMEND_SKILL_REGIONS 一致的按钮中心（批次1-4，从左到右）
CX = {1: 0.820, 2: 0.870, 3: 0.920, 4: 0.970}
CY, R = 0.898, 0.037

COLOR_BG = (38, 32, 28)
COLOR_RIM_REST = (118, 112, 111)
COLOR_WHITE = (255, 255, 255)


def render_button(img, cx_n, signal_color):
    """在 img 上以游戏同心环结构画一个按钮（信号层用 signal_color）。"""
    cx, cy = int(cx_n * W), int(CY * H)
    r0 = int(R * min(W, H))
    cv2.circle(img, (cx, cy), int(r0 * 0.72), (45, 40, 36), -1)
    cv2.circle(img, (cx, cy), r0, signal_color, 3)
    cv2.circle(img, (cx, cy), int(r0 * 1.16), COLOR_RIM_REST, 2)


def frame_one(slot: int, white: bool) -> np.ndarray:
    """单按钮帧：slot 号按钮白（脉冲）或常态灰蓝。"""
    img = np.full((H, W, 3), COLOR_BG, np.uint8)
    for s in CX:
        render_button(img, CX[s], COLOR_WHITE if (white and s == slot) else COLOR_RIM_REST)
    return img


def frame_flash() -> np.ndarray:
    """全屏闪光帧：4 个按钮信号层同时全白（大招演出）。"""
    img = np.full((H, W, 3), COLOR_BG, np.uint8)
    for s in CX:
        render_button(img, CX[s], COLOR_WHITE)
    return img


class _FakeProbeTask:
    """探针观测所需的最小 task 表面。"""

    def __init__(self, t=1000.0, cfg=None, member_count=None, team=None):
        self.frame = None
        self._t = t
        self._cfg = cfg if cfg is not None else {}
        self._battle_member_count = member_count
        self._battle_team = team
        self.debug_logs = []

    def active_time(self):
        return self._t

    def get_battle_config(self, key, default=None):
        return self._cfg.get(key, default)

    def log_debug(self, msg):
        self.debug_logs.append(msg)


class TestPulseProbe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log_path = Path(self._tmp.name) / "pulse_probe_log.jsonl"
        self.probe = PulseProbe(log_path=self.log_path)

    def _task(self, **kwargs):
        return _FakeProbeTask(**kwargs)

    def _observe(self, task, frame, step=0.3):
        task.frame = frame
        task._t += step
        self.probe.observe(task)

    def _entries(self):
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_rising_edge_recorded_with_fields(self):
        task = self._task(member_count=4, team=["赛希", "弭弗", "莱万汀", "噗切娜"])
        self._observe(task, frame_one(3, white=True))
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["label"], "批次3")
        self.assertEqual(e["slot"], 3)
        self.assertEqual(e["key"], "3")
        self.assertEqual(e["char"], "莱万汀")
        self.assertEqual(e["team"], ["赛希", "弭弗", "莱万汀", "噗切娜"])
        self.assertEqual(e["member_count"], 4)
        self.assertEqual(e["task"], "_FakeProbeTask")
        self.assertGreaterEqual(e["ratio"], 0.8)
        self.assertIn("time", e)
        self.assertIn("active_t", e)

    def test_sustained_pulse_recorded_once(self):
        task = self._task(member_count=4)
        pulse = frame_one(2, white=True)
        for _ in range(5):
            self._observe(task, pulse)
        self.assertEqual(len(self._entries()), 1, "持续白只在上升沿记一次")

    def test_rearm_after_off_records_second_pulse(self):
        task = self._task(member_count=4)
        self._observe(task, frame_one(2, white=True))
        for _ in range(4):  # 回落累计 ≥3 帧，复位
            self._observe(task, frame_one(2, white=False))
        self._observe(task, frame_one(2, white=True))
        self.assertEqual(len(self._entries()), 2, "第二个脉冲周期应重新记录")

    def test_rest_state_no_record(self):
        task = self._task(member_count=4)
        for _ in range(6):
            self._observe(task, frame_one(1, white=False))
        self.assertEqual(self._entries(), [])

    def test_fullscreen_flash_filtered(self):
        task = self._task(member_count=4)
        self._observe(task, frame_flash())
        self.assertEqual(self._entries(), [], "全屏闪光不作为脉冲记录")

    def test_flash_resets_allow_later_pulse(self):
        task = self._task(member_count=4)
        self._observe(task, frame_flash())
        self._observe(task, frame_one(4, white=True))
        self.assertEqual(len(self._entries()), 1, "闪光复位后真实脉冲仍可记录")

    def test_throttle_skips_samples(self):
        task = self._task(member_count=4)
        task.frame = frame_one(1, white=True)
        # 步长 0.1s < 节流 0.25s：连续 3 次只应采样 1 次
        for _ in range(3):
            task._t += 0.1
            self.probe.observe(task)
        self.assertEqual(len(self._entries()), 1)

    def test_clock_rollback_resets_throttle_and_samples(self):
        task = self._task(member_count=4)
        task.frame = frame_one(1, white=True)
        self.probe.observe(task)
        task._t -= 1
        self.probe.observe(task)
        self.assertEqual(self.probe._last_sample_t, task._t)
        task._t += 0.1
        self.probe.observe(task)
        self.assertEqual(self.probe._last_sample_t, task._t - 0.1)

    def test_string_log_paths_create_parent_directories(self):
        task = self._task(member_count=4)
        injected = Path(self._tmp.name) / "injected" / "pulse.jsonl"
        PulseProbe(log_path=str(injected))._record(task, "批次1", 1, 1.0, 4)
        self.assertTrue(injected.is_file())

        default = Path(self._tmp.name) / "default" / "pulse.jsonl"
        with patch("src.core.paths.config_path", return_value=str(default)):
            PulseProbe()._record(task, "批次1", 1, 1.0, 4)
        self.assertTrue(default.is_file())

    def test_disabled_no_record(self):
        task = self._task(cfg={KEY_PULSE_PROBE: False}, member_count=4)
        for _ in range(4):
            self._observe(task, frame_one(1, white=True))
        self.assertEqual(self._entries(), [])

    def test_no_team_info_still_observes_all_four(self):
        # 无人数/队伍信息：监测全部 4 区域，字段记 null（独立探针语义）
        task = self._task()
        self._observe(task, frame_one(1, white=True))
        entries = self._entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["label"], "批次1")
        self.assertIsNone(e["char"])
        self.assertIsNone(e["team"])
        self.assertIsNone(e["member_count"])

    def test_unknown_member_maps_null_char(self):
        # 队伍里有未识别位（"?"）：该位脉冲 char 记 null
        task = self._task(member_count=4, team=["赛希", "?", "莱万汀", "噗切娜"])
        self._observe(task, frame_one(2, white=True))
        self.assertIsNone(self._entries()[0]["char"])

    def test_probe_called_in_battle_loop(self):
        # 挂载点：AutoCombatLogic.run 主循环每帧调用 probe_pulse
        from tests.TestConditionalRotation import _FakeTask

        cfg = {KEY_PULSE_PROBE: True}
        task = _FakeTask(cfg, ults=(), link=False, skill=3)
        task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
        calls = []
        task.probe_pulse = lambda: calls.append(1)
        with patch.object(cv2, "imshow", create=True):
            from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic

            AutoCombatLogic(task).run(start_sleep=0)
        self.assertTrue(calls, "战斗主循环应调用探针")

    def test_observe_exception_never_raises(self):
        # 探针内部异常绝不外抛（战斗优先）
        task = self._task(member_count=4)
        task.frame = frame_one(1, white=True)
        with patch.object(PulseProbe, "_record", side_effect=RuntimeError("boom")):
            self.probe._last_sample_t = 0.0
            task._t += 0.3
            self.probe.observe(task)  # 不应 raise


if __name__ == "__main__":
    unittest.main()
