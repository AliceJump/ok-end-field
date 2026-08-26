# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import pyautogui

from src.core.rotation_ast import normalize_ast, eval_cond, iter_actions
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic


class _FakeProbe:
    """实现 CondProbe 协议的测试替身。"""

    def __init__(self, ults=(), link=False, skill=0):
        self._ults = {str(u) for u in ults}
        self._link = link
        self._skill = skill

    def ult_available(self, n):
        return str(n) in self._ults

    def link_available(self):
        return self._link

    def skill_count(self):
        return self._skill


class TestNormalizeAst(unittest.TestCase):
    def test_normalize_plain_actions_kept(self):
        ast, w = normalize_ast(["1", "2", "e", "ult_3", "sleep_1.5", "normal_2"])
        self.assertEqual(ast, ["1", "2", "e", "ult_3", "sleep_1.5", "normal_2"])
        self.assertEqual(w, [])

    def test_normalize_drops_invalid_action(self):
        ast, w = normalize_ast(["1", "ult_5", "foo", "normal_0", "sleep_-1"])
        self.assertEqual(ast, ["1"])
        self.assertEqual(len(w), 4)

    def test_normalize_drops_invalid_atom_in_condition(self):
        ast, w = normalize_ast([{"if": "ult9", "then": ["1"]}])
        self.assertEqual(ast, [])
        self.assertTrue(any("条件" in x for x in w))

    def test_normalize_skill_out_of_range(self):
        ast, w = normalize_ast([
            {"if": "skill>=4", "then": ["1"]},
            {"if": "skill>=0", "then": ["2"]},
            {"if": "skill>=3", "then": ["3"]},
        ])
        self.assertEqual(ast, [{"if": "skill>=3", "then": ["3"]}])
        # 每个非法 if 的 Condition 产生 2 条 warning（原子非法 + 整块丢弃），共 4 条
        self.assertEqual(len(w), 4)

    def test_normalize_top_level_not_list(self):
        ast, w = normalize_ast("ult_1,1")
        self.assertEqual(ast, [])
        self.assertEqual(len(w), 1)

    def test_normalize_condition_then_non_list_becomes_empty(self):
        ast, w = normalize_ast([{"if": "link", "then": "e"}])
        self.assertEqual(ast, [{"if": "link", "then": []}])
        self.assertTrue(any("then" in x for x in w))

    def test_normalize_condition_else_omitted_when_absent(self):
        ast, w = normalize_ast([{"if": "link", "then": ["e"]}])
        self.assertEqual(ast, [{"if": "link", "then": ["e"]}])
        self.assertNotIn("else", ast[0])
        self.assertEqual(w, [])

    def test_normalize_nested_all_any(self):
        ast, w = normalize_ast([
            {"if": {"all": ["ult1", {"any": ["link", "skill>=2"]}]}, "then": ["1"], "else": ["2"]}
        ])
        self.assertEqual(len(ast), 1)
        self.assertEqual(ast[0]["if"], {"all": ["ult1", {"any": ["link", "skill>=2"]}]})
        self.assertEqual(ast[0]["then"], ["1"])
        self.assertEqual(ast[0]["else"], ["2"])
        self.assertEqual(w, [])

    def test_normalize_empty_list(self):
        ast, w = normalize_ast([])
        self.assertEqual(ast, [])
        self.assertEqual(w, [])

    def test_normalize_structured_action_dropped(self):
        ast, w = normalize_ast([{"t": "dodge"}, "1"])
        self.assertEqual(ast, ["1"])
        self.assertEqual(len(w), 1)

    def test_normalize_invalid_sub_cond_in_all_dropped(self):
        ast, w = normalize_ast([{"if": {"all": ["ult1", "bad_atom"]}, "then": ["1"]}])
        self.assertEqual(ast, [{"if": {"all": ["ult1"]}, "then": ["1"]}])
        self.assertTrue(any("非法条件原子" in x for x in w))

    def test_normalize_all_fully_invalid_dropped(self):
        ast, w = normalize_ast([{"if": {"all": ["bad1", "bad2"]}, "then": ["1"]}])
        self.assertEqual(ast, [])

    def test_normalize_any_fully_invalid_kept_empty(self):
        ast, w = normalize_ast([{"if": {"any": ["bad1", "bad2"]}, "then": ["1"]}])
        self.assertEqual(ast, [{"if": {"any": []}, "then": ["1"]}])


class TestEvalCond(unittest.TestCase):
    def test_eval_atom_ult(self):
        self.assertTrue(eval_cond("ult1", _FakeProbe(ults=[1])))
        self.assertFalse(eval_cond("ult1", _FakeProbe(ults=[2])))

    def test_eval_atom_link(self):
        self.assertTrue(eval_cond("link", _FakeProbe(link=True)))
        self.assertFalse(eval_cond("link", _FakeProbe(link=False)))

    def test_eval_atom_skill(self):
        self.assertTrue(eval_cond("skill>=2", _FakeProbe(skill=2)))
        self.assertTrue(eval_cond("skill>=2", _FakeProbe(skill=3)))
        self.assertFalse(eval_cond("skill>=2", _FakeProbe(skill=1)))
        self.assertFalse(eval_cond("skill>=2", _FakeProbe(skill=-1)))

    def test_eval_all_short_circuit(self):
        self.assertFalse(eval_cond({"all": ["ult1", "link"]}, _FakeProbe(ults=[], link=True)))
        self.assertTrue(eval_cond({"all": ["ult1", "link"]}, _FakeProbe(ults=[1], link=True)))

    def test_eval_any_short_circuit(self):
        self.assertTrue(eval_cond({"any": ["ult1", "link"]}, _FakeProbe(ults=[], link=True)))
        self.assertFalse(eval_cond({"any": ["ult1", "link"]}, _FakeProbe(ults=[], link=False)))

    def test_eval_not(self):
        self.assertTrue(eval_cond({"not": "link"}, _FakeProbe(link=False)))
        self.assertFalse(eval_cond({"not": "link"}, _FakeProbe(link=True)))

    def test_eval_unknown_returns_false(self):
        self.assertFalse(eval_cond("unknown", _FakeProbe()))
        self.assertFalse(eval_cond(123, _FakeProbe()))
        self.assertFalse(eval_cond({"weird": []}, _FakeProbe()))


class TestIterActions(unittest.TestCase):
    def test_iter_plain_sequence(self):
        self.assertEqual(
            list(iter_actions(["1", "2", "e"], _FakeProbe())),
            ["1", "2", "e"],
        )

    def test_iter_condition_then_branch(self):
        ast = [{"if": "link", "then": ["e", "1"]}]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(link=True))),
            ["e", "1"],
        )

    def test_iter_condition_else_branch(self):
        ast = [{"if": "link", "then": ["e"], "else": ["1"]}]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(link=False))),
            ["1"],
        )

    def test_iter_condition_else_omitted_yields_nothing(self):
        ast = [{"if": "link", "then": ["e"]}]
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=False))), [])

    def test_iter_nested_all_any(self):
        ast = [
            {"if": {"all": ["ult1", {"any": ["link", "skill>=2"]}]}, "then": ["1"], "else": ["2"]},
        ]
        # all True（ult1 + link）
        self.assertEqual(list(iter_actions(ast, _FakeProbe(ults=[1], link=True))), ["1"])
        # all False（无 ult1，走 else）
        self.assertEqual(list(iter_actions(ast, _FakeProbe(ults=[], link=True, skill=3))), ["2"])

    def test_iter_empty_ast(self):
        self.assertEqual(list(iter_actions([], _FakeProbe())), [])

    def test_iter_mixed_plain_and_condition(self):
        ast = ["ult_1", {"if": "ult2", "then": ["2", "3"]}, "1"]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(ults=[2]))),
            ["ult_1", "2", "3", "1"],
        )

    def test_iter_unknown_structure_ignored(self):
        ast = ["1", {"weird": True}, "2"]
        self.assertEqual(list(iter_actions(ast, _FakeProbe())), ["1", "2"])

    def test_iter_not_branch(self):
        # link 不可用时走 then（not link 为 True）
        ast = [{"if": {"not": "link"}, "then": ["1"], "else": ["e"]}]
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=False))), ["1"])
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=True))), ["e"])


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

    def use_recommend_skill(self):
        return False

    def send_key(self, key):
        self.actions.append(key)

    def press_combat_key(self, key):
        self.actions.append(key)


class TestConditionalRotationCombat(unittest.TestCase):
    """AutoCombatLogic.run() 实时条件路径集成测试（mock task）。"""

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_executes_then_branch(self, _mu, _md):
        cfg = {
            "启用实时条件": True,
            "实时条件序列": ["1", {"if": "link", "then": ["e"]}],
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
            "启用实时条件": True,
            "实时条件序列": [{"if": "link", "then": ["e"], "else": ["2"]}],
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
            "启用实时条件": True,
            "实时条件序列": [],
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
            "启用实时条件": True,
            "实时条件序列": [{"if": "link", "then": ["e"]}],
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
            "启用实时条件": True,
            "实时条件序列": [{"if": "ult2", "then": ["ult_2"]}],
        }
        task = _FakeTask(cfg, ults=[2], link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("ult_2", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_instant_ult_release_when_no_cond_action(self, _mu, _md):
        """条件不满足、本帧无动作时，立即释放终结技生效。"""
        cfg = {
            "启用实时条件": True,
            "实时条件序列": [{"if": "link", "then": ["e"]}],  # link 不可用 → 无动作
            "立即释放终结技": True,
        }
        task = _FakeTask(cfg, ults=[2], link=False, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("ult_2", task.actions)
        self.assertNotIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_instant_link_release_when_no_cond_action(self, _mu, _md):
        """条件不满足、本帧无动作时，立即释放连携技生效。"""
        cfg = {
            "启用实时条件": True,
            "实时条件序列": [{"if": "ult2", "then": ["ult_2"]}],  # ult2 不可用 → 无动作
            "立即释放连携技": True,
        }
        task = _FakeTask(cfg, ults=(), link=True, skill=3)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_skill_timeout_keeps_next_token(self, _mu, _md):
        """战技技力不足重试超时后，继续下一个动作。"""
        cfg = {
            "启用实时条件": True,
            "实时条件序列": ["1", "e"],
        }
        task = _FakeTask(cfg, ults=(), link=True, skill=0)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertIn("e", task.actions)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_conditional_rotation_sleep_truncated_by_deadline(self, _mu, _md):
        cfg = {
            "启用实时条件": True,
            "实时条件序列": ["sleep_5"],
            "技能释放": ["1", "2", "3"],
            "启动技能点数": 2,
        }
        task = _FakeTask(cfg, ults=[], link=False, skill=0)
        logic = AutoCombatLogic(task)
        result = logic.run(start_sleep=0, deadline=1.0)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
