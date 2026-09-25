"""TestSkillRotation — 伤害优先技能释放序列与自动排轴的排序规则。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyautogui

from src.core.BattleConfig import KEY_DAMAGE_ROTATION, KEY_SKILL_ALLOWLIST
from src.core.rotation_ast import normalize_ast
from src.data.skill_rotation import (
    clear_cache,
    generate_auto_rotation,
    generate_damage_rotation,
    load_damage_baseline,
)
from src.tasks.onetime.AutoCombatLogic import AutoCombatLogic


class TestGenerateDamageRotation(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    def test_order_by_damage_desc(self):
        baseline = {"A": 3000.0, "B": 1000.0, "C": 2000.0}
        # D 无基准数据 → 排最后
        self.assertEqual(
            generate_damage_rotation(["A", "B", "C", "D"], baseline), ["1", "3", "2", "4"]
        )

    def test_unknown_member_placeholder_last(self):
        baseline = {"A": 100.0, "B": 200.0}
        # "?" 视为 0 伤害，排在已知角色之后（B 200 > A 100）；两个未知保持队位顺序
        self.assertEqual(generate_damage_rotation(["?", "A", "?", "B"], baseline), ["4", "2", "1", "3"])

    def test_missing_data_falls_back_to_position_order(self):
        self.assertEqual(generate_damage_rotation(["X", "Y", "Z", "W"], {}), ["1", "2", "3", "4"])

    def test_tie_keeps_position_order(self):
        baseline = {"A": 500.0, "B": 500.0, "C": 100.0}
        self.assertEqual(generate_damage_rotation(["A", "B", "C", "D"], baseline), ["1", "2", "3", "4"])

    def test_baseline_argument_overrides_cache(self):
        # 显式传入 baseline 时不读取缓存文件
        clear_cache()
        self.assertEqual(
            generate_damage_rotation(["A", "B"], {"A": 1.0}), ["1", "2"]
        )

    def test_real_baseline_loads_and_orders(self):
        baseline = load_damage_baseline()
        self.assertTrue(baseline, "damage_baseline.json 应存在且非空")
        # 真实队伍：位置1=赛希(0，纯辅助) 2=弭弗(最高) 3=莱万汀 4=噗切娜
        tokens = generate_damage_rotation(["赛希", "弭弗", "莱万汀", "噗切娜"], baseline)
        self.assertEqual(tokens, ["2", "3", "4", "1"])

    def test_load_prefers_cycle_expect(self):
        # 有 cycle_expect 的角色用它排序；缺失的回退单发战技期望
        clear_cache()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "baseline.json"
                p.write_text(json.dumps([
                    {"character": "甲", "cycle_expect": 999.0,
                     "skills": [{"type": "战技", "crit_expect": 5.0}]},
                    {"character": "乙",
                     "skills": [{"type": "战技", "crit_expect": 7.0}]},
                ], ensure_ascii=False), encoding="utf-8")
                baseline = load_damage_baseline(p)
        finally:
            clear_cache()
        self.assertEqual(baseline["甲"], 999.0)
        self.assertEqual(baseline["乙"], 7.0)


class TestGenerateAutoRotation(unittest.TestCase):
    """generate_auto_rotation — 全技能可重复循环轴的生成规则。"""

    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    def test_structure_contains_all_skill_types(self):
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        digits = [t for t in rotation if t.isdigit()]
        ults = [t for t in rotation if t.startswith("ult_")]
        links = [t for t in rotation if t == "e"]
        fills = [t for t in rotation if t.startswith("normal_")]
        # 覆盖队内全部 1-4 号位：4 战技 + 4 终结技 + 连携窗口 + 普攻回能填充段
        self.assertEqual(len(digits), 4)
        self.assertEqual(len(ults), 4)
        self.assertGreaterEqual(len(links), 1)
        self.assertEqual(len(fills), 4)

    def test_order_by_damage_desc(self):
        # A 伤害最高但在 4 号位 → 首个战技为 "4"
        rotation = generate_auto_rotation(["D", "C", "B", "A"], {"A": 400, "B": 300, "C": 200, "D": 100})
        first_skill = next(t for t in rotation if t.isdigit())
        self.assertEqual(first_skill, "4")

    def test_ult_follows_its_skill(self):
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        for i, token in enumerate(rotation):
            if token.isdigit():
                self.assertEqual(rotation[i + 1], f"ult_{token}", "每个战技后应紧跟对应号位的终结技")

    def test_tokens_valid_for_executor(self):
        # 轴必须能被排轴执行器 / 实时条件解析器接受（normalize_ast 无告警）
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        ast, warnings = normalize_ast(rotation)
        self.assertEqual(ast, rotation)
        self.assertEqual(warnings, [])

    def test_sp_balance_fills_match_skills(self):
        # 每段填充 ≈ 一个战技的技力恢复，整轮 SP 收支平衡
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        fills = [t for t in rotation if t.startswith("normal_")]
        digits = [t for t in rotation if t.isdigit()]
        self.assertEqual(len(fills), len(digits))

    def test_unknown_member_still_gets_segment(self):
        # "?"（未识别）照常生成段：空按无害， ult/e 由就绪检测兜底
        rotation = generate_auto_rotation(["?", "A", "?", "B"], {"A": 2, "B": 1})
        digits = [t for t in rotation if t.isdigit()]
        self.assertEqual(sorted(digits), ["1", "2", "3", "4"])

    def test_real_baseline_generates(self):
        baseline = load_damage_baseline()
        rotation = generate_auto_rotation(["赛希", "弭弗", "莱万汀", "噗切娜"], baseline)
        first_skill = next(t for t in rotation if t.isdigit())
        # 弭弗伤害最高（2 号位）先手，且轴含全部技能类型
        self.assertEqual(first_skill, "2")
        self.assertIn("ult_2", rotation)
        self.assertIn("e", rotation)
        self.assertTrue(any(t.startswith("normal_") for t in rotation))

    def test_cold_start_excludes_ult(self):
        # 冷启动（协议空间外）：轴不含 ult_N，其余结构（战技/连携窗口/填充段）不变
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1}, include_ult=False)
        self.assertFalse(any(t.startswith("ult_") for t in rotation))
        digits = [t for t in rotation if t.isdigit()]
        links = [t for t in rotation if t == "e"]
        fills = [t for t in rotation if t.startswith("normal_")]
        self.assertEqual(len(digits), 4)
        self.assertGreaterEqual(len(links), 1)
        self.assertEqual(len(fills), 4)

    def test_cold_start_tokens_valid_and_sp_balanced(self):
        # 冷启动轴同样过执行器校验，且 SP 配平不变（填充段数 = 战技数）
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1}, include_ult=False)
        ast, warnings = normalize_ast(rotation)
        self.assertEqual(ast, rotation)
        self.assertEqual(warnings, [])
        fills = [t for t in rotation if t.startswith("normal_")]
        digits = [t for t in rotation if t.isdigit()]
        self.assertEqual(len(fills), len(digits))


class _FakeAutoTask:
    """模拟 BaseEfTask 的最小子集，供 _do_auto_rotation_step 测试。"""

    def __init__(self, skill=0, ults=(), link=False):
        self._skill = skill
        self._ults = {str(u) for u in ults}
        self._link = link
        self._time = 0.0
        self.actions = []

    # ── 时间 / 帧 ──
    def active_time(self):
        return self._time

    def sleep(self, t):
        self._time += t

    def next_frame(self):
        self._time += 0.1

    # ── 交互 / 日志 ──
    def click(self, **k):
        pass

    def mouse_up(self, **k):
        pass

    def mouse_down(self, **k):
        pass

    def active_and_send_mouse_delta(self, **k):
        pass

    def approach_enemy(self):
        pass

    def log_info(self, *a, **k):
        pass

    def log_warning(self, *a, **k):
        pass

    # ── 检测 / 动作 ──
    def get_skill_bar_count(self):
        return self._skill

    def send_key(self, key):
        self.actions.append(key)

    def use_ult(self, ult_sequence=None):
        seq = [ult_sequence] if ult_sequence else ["1", "2", "3", "4"]
        for u in seq:
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

    def _check_single_exit_condition(self):
        return False


class TestAutoRotationStep(unittest.TestCase):
    """_do_auto_rotation_step — 自动排轴执行语义（失败跳过、可重复循环）。"""

    def _make_logic(self, task, rotation):
        logic = AutoCombatLogic(task)
        logic.auto_rotation_enabled = True
        logic.auto_rotation_active = True
        logic.auto_rotation_sequence = rotation
        logic.auto_rotation_index = 0
        # normal_ 填充段内嵌循环依赖的计时状态（run() 中初始化，这里手动补齐）
        logic._last_exit_check_time = 0.0
        logic._exit_check_interval = 0.5
        logic.normal_start_trigger = 2
        logic.normal_skill_sequence = ["1", "2", "3"]
        logic.normal_skill_index = 0
        return logic

    def test_not_ready_tokens_skipped(self):
        # 技力空 + 无终结技 + 无连携：digit 暂存重试超时跳过、ult/e 立即跳过，
        # 轴推进到 normal 填充段（必成功），不卡死
        task = _FakeAutoTask(skill=0)
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        logic = self._make_logic(task, rotation)
        # 轴头三个 token：digit "4" → ult_4 → e，全部未就绪
        steps = AutoCombatLogic._SKILL_RETRY_MAX_FRAMES + 3
        for _ in range(steps):
            logic._do_auto_rotation_step(None)
        self.assertEqual(logic.auto_rotation_sequence[logic.auto_rotation_index], "normal_12.5")

    def test_full_loop_returns_to_start(self):
        task = _FakeAutoTask(skill=3, ults=(1, 2, 3, 4), link=True)
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        logic = self._make_logic(task, rotation)
        for _ in range(len(rotation)):
            logic._do_auto_rotation_step(None)
        # 全就绪时逐步推进，跑完一轮 index 回到起点（可重复循环）
        self.assertEqual(logic.auto_rotation_index, 0)
        for digit in ("1", "2", "3", "4"):
            self.assertIn(digit, task.actions)
            self.assertIn(f"ult_{digit}", task.actions)

    def test_auto_step_does_not_advance_manual_rotation(self):
        task = _FakeAutoTask(skill=3)
        logic = self._make_logic(task, ["1"])
        logic.skill_sequence = ["4", "2"]
        logic.skill_index = 1
        logic._do_auto_rotation_step(None)
        self.assertEqual(logic.skill_sequence, ["4", "2"])
        self.assertEqual(logic.skill_index, 1)
        self.assertEqual(logic.auto_rotation_index, 0)

    def test_fill_segment_does_not_use_digits(self):
        # 填充段（normal_）期间技力须留给轴上战技：即使技力充足也不发数字键
        task = _FakeAutoTask(skill=3, ults=(), link=False)
        logic = self._make_logic(task, ["normal_0.3"])
        logic._do_auto_rotation_step(None)
        self.assertEqual(task.actions, [])
        self.assertEqual(logic.auto_rotation_index, 0)  # 单 token 轴取模回起点

    def test_link_window_tried_when_ready(self):
        task = _FakeAutoTask(skill=0, link=True)
        rotation = generate_auto_rotation(["A", "B", "C", "D"], {"A": 4, "B": 3, "C": 2, "D": 1})
        logic = self._make_logic(task, rotation)
        # 技力空时逐 token 跳过，轮到 e 时就绪即释放
        for _ in range(30):
            logic._do_auto_rotation_step(None)
        self.assertIn("e", task.actions)


class TestAutoRotationCombat(unittest.TestCase):
    """AutoCombatLogic.run() 自动排轴路径集成测试（mock task）。"""

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_manual_rotation_survives_team_detection_at_both_sites(self, _mu, _md):
        from tests.TestConditionalRotation import _FakeTask

        cfg = {
            KEY_SKILL_ALLOWLIST: True,
            KEY_DAMAGE_ROTATION: True,
            "启用排轴": True,
            "排轴序列": "4,2",
        }
        for start_sleep in (0.3, 0):
            with self.subTest(start_sleep=start_sleep):
                task = _FakeTask(cfg, skill=3)
                task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
                logic = AutoCombatLogic(task)
                logic.run(start_sleep=start_sleep)
                self.assertEqual(logic.skill_sequence, ["4", "2"])
                self.assertEqual(logic.auto_rotation_sequence, [])
                self.assertFalse(logic.auto_rotation_active)

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_run_activates_auto_rotation(self, _mu, _md):
        from tests.TestConditionalRotation import _FakeTask

        cfg = {KEY_SKILL_ALLOWLIST: True, KEY_DAMAGE_ROTATION: True}
        task = _FakeTask(cfg, ults=("2",), link=False, skill=3)
        task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
        # 检测到协议空间特征 → 热启动：轴含终结技
        task.find_feature = lambda feature=None, **k: True
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertTrue(logic.auto_rotation_active)
        self.assertTrue(logic.protocol_space_detected)
        # 轴覆盖全技能类型，且伤害最高的弭弗（2 号位）先手
        self.assertEqual(logic.auto_rotation_sequence[0], "2")
        self.assertIn("ult_2", logic.auto_rotation_sequence)
        self.assertIn("e", logic.auto_rotation_sequence)
        self.assertTrue(any(t.startswith("normal_") for t in logic.auto_rotation_sequence))

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_run_cold_start_excludes_ult(self, _mu, _md):
        # 协议空间特征检测未命中 → 冷启动：轴不含 ult，其余结构完整
        from tests.TestConditionalRotation import _FakeTask

        cfg = {KEY_SKILL_ALLOWLIST: True, KEY_DAMAGE_ROTATION: True}
        task = _FakeTask(cfg, ults=("2",), link=False, skill=3)
        task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
        task.find_feature = lambda feature=None, **k: False
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertTrue(logic.auto_rotation_active)
        self.assertFalse(logic.protocol_space_detected)
        self.assertFalse(any(t.startswith("ult_") for t in logic.auto_rotation_sequence))
        self.assertEqual(logic.auto_rotation_sequence[0], "2")
        self.assertIn("e", logic.auto_rotation_sequence)
        self.assertTrue(any(t.startswith("normal_") for t in logic.auto_rotation_sequence))

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_run_without_detector_defaults_cold_start(self, _mu, _md):
        # 无 find_feature 能力（异常环境）→ 保守按冷启动处理，不抛异常
        from tests.TestConditionalRotation import _FakeTask

        cfg = {KEY_SKILL_ALLOWLIST: True, KEY_DAMAGE_ROTATION: True}
        task = _FakeTask(cfg, ults=("2",), link=False, skill=3)
        task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertTrue(logic.auto_rotation_active)
        self.assertFalse(logic.protocol_space_detected)
        self.assertFalse(any(t.startswith("ult_") for t in logic.auto_rotation_sequence))

    @patch.object(pyautogui, "mouseDown")
    @patch.object(pyautogui, "mouseUp")
    def test_run_without_damage_rotation_keeps_allowlist(self, _mu, _md):
        # 关闭伤害优先排轴：维持原「自动技能列表」行为（不进入自动排轴）
        from tests.TestConditionalRotation import _FakeTask

        cfg = {KEY_SKILL_ALLOWLIST: True, KEY_DAMAGE_ROTATION: False}
        task = _FakeTask(cfg, ults=(), link=False, skill=3)
        task.stable_team_result = (["赛希", "弭弗", "莱万汀", "噗切娜"], True)
        logic = AutoCombatLogic(task)
        logic.run(start_sleep=0)
        self.assertFalse(logic.auto_rotation_active)
        self.assertFalse(any(t.startswith("ult_") or t.startswith("normal_") for t in logic.normal_skill_sequence))


class TestLoadDamageBaseline(unittest.TestCase):
    def tearDown(self):
        clear_cache()

    def test_loads_all_characters(self):
        clear_cache()
        baseline = load_damage_baseline()
        self.assertGreaterEqual(len(baseline), 30)
        self.assertGreater(baseline.get("弭弗", 0), baseline.get("赛希", 0))

    def test_missing_file_returns_empty(self):
        clear_cache()
        baseline = load_damage_baseline(__import__("pathlib").Path("Z:/nonexistent.json"))
        self.assertEqual(baseline, {})


if __name__ == "__main__":
    unittest.main()
