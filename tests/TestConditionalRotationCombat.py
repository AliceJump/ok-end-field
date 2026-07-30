# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import pyautogui

from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic


class _FakeTask:
    """模拟 BaseEfTask，供 AutoCombatLogic.run() 在无游戏环境下测试。

    记录所有释放动作到 self.actions，供断言。
    """

    def __init__(self, battle_config, ults=(), link=False, skill=3):
        self._cfg = battle_config
        self._ults = {str(u) for u in ults}
        self._link = link
        self._skill = skill
        self._time = 0.0
        self._frame = 0
        self._exited = False
        self.actions = []
        self.debug = False

    # ── 时间 / 帧 ──
    def active_time(self):
        return self._time

    def sleep(self, t):
        self._time += t

    def next_frame(self):
        self._time += 0.1
        self._frame += 1

    # ── 战斗状态 ──
    def in_combat(self, required_yellow=0):
        return not self._exited

    def in_team(self):
        return True

    def ocr_lv(self):
        return False

    def _check_single_exit_condition(self):
        return self._frame > 30

    def is_combat_ended(self):
        if self._frame > 30:
            self._exited = True
            return True
        return False

    def in_bg(self):
        return False

    # ── 交互（no-op）──
    def screenshot(self, *a, **k):
        pass

    def active_and_send_mouse_delta(self, **k):
        pass

    def click(self, **k):
        pass

    def approach_enemy(self):
        pass

    def log_info(self, *a, **k):
        pass

    def log_error(self, *a, **k):
        pass

    # ── 配置 ──
    def get_battle_config(self, key, default=None):
        return self._cfg.get(key, default)

    def _parse_skill_sequence(self, raw):
        if not raw:
            return ["1", "2", "3"]
        return [t.strip() for t in str(raw).replace("，", ",").split(",") if t.strip()]

    # ── 检测 ──
    def get_skill_bar_count(self):
        return self._skill

    def find_one(self, name, **k):
        if name.startswith("ult_"):
            return True if name[4:] in self._ults else None
        if name == "default_link_skill":
            return True if self._link else None
        return None

    # ── 动作 ──
    def use_ult(self, ult_sequence=None):
        ults = [ult_sequence] if ult_sequence else ["1", "2", "3", "4"]
        for u in ults:
            if u in self._ults:
                self.actions.append(f"ult_{u}")
                return True
        return False

    def use_link_skill(self):
        if self._link:
            self.actions.append("e")
            return True
        return False

    def send_key(self, key):
        self.actions.append(key)

    def press_combat_key(self, key):
        self.actions.append(key)


class TestConditionalRotationCombat(unittest.TestCase):
    """AutoCombatLogic.run() 条件排轴路径集成测试（mock task）。"""

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_executes_then_branch(self, _mu, _md):
        cfg = {
            "启用条件排轴": True,
            "条件排轴序列": ["1", {"if": "link", "then": ["e"]}],
        }
        task = _FakeTask(cfg, ults=(), link=True, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("1", task.actions)
        self.assertIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_else_branch(self, _mu, _md):
        cfg = {
            "启用条件排轴": True,
            "条件排轴序列": [{"if": "link", "then": ["e"], "else": ["2"]}],
        }
        task = _FakeTask(cfg, ults=(), link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("2", task.actions)
        self.assertNotIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_empty_falls_back_to_normal(self, _mu, _md):
        cfg = {
            "启用条件排轴": True,
            "条件排轴序列": [],
            "技能释放": ["1", "2", "3"],
            "启动技能点数": 2,
        }
        task = _FakeTask(cfg, ults=(), link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        # 回退普通模式：cond_rotation_enabled 最终为 False，走 _do_normal_combat_frame
        self.assertFalse(logic.cond_rotation_enabled)
        self.assertIn("1", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_no_match_runs_clean(self, _mu, _md):
        cfg = {
            "启用条件排轴": True,
            "条件排轴序列": [{"if": "link", "then": ["e"]}],
        }
        task = _FakeTask(cfg, ults=(), link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        # 条件不满足且无 else：不执行任何动作，但不崩
        self.assertNotIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_ult_action(self, _mu, _md):
        cfg = {
            "启用条件排轴": True,
            "条件排轴序列": [{"if": "ult2", "then": ["ult_2"]}],
        }
        task = _FakeTask(cfg, ults=[2], link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("ult_2", task.actions)


if __name__ == "__main__":
    unittest.main()
