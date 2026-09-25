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
    load_team_baseline_entries,
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


def _caps_map(**members) -> dict:
    from src.data.character_capabilities import CharacterCapabilities

    return {
        name: CharacterCapabilities(key=name, name=name,
                                     attach_elements=tuple(spec[0]), combo_applier=spec[1])
        for name, spec in members.items()
    }


class TestDependencyAwareOrdering(unittest.TestCase):
    """资源喂养约束：满口径依赖附着的角色排在其喂养者之后。"""

    _ENTRIES = [
        {"character": "提弗洛斯", "cycle_expect": 129661.5,
         "cycle_expect_conservative": 103074.8,
         "full_caliber_requires": {"attach": "自然"}},
        {"character": "莱万汀", "cycle_expect": 100000.0},
        {"character": "洁尔佩塔", "cycle_expect": 30000.0},
        {"character": "弭弗", "cycle_expect": 50000.0},
    ]

    _CAPS = _caps_map(
        提弗洛斯=((), False),
        莱万汀=((), False),
        洁尔佩塔=(("自然",), False),
        弭弗=((), False),
    )

    def setUp(self):
        clear_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "damage_baseline.json"
        self._path.write_text(json.dumps(self._ENTRIES, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        clear_cache()
        self._tmp.cleanup()

    def _rotate(self, team):
        return generate_damage_rotation(team, path=self._path, capabilities=self._CAPS)

    def test_feeder_ordered_before_consumer(self):
        # 纯伤害序：提弗洛斯(129661) > 莱万汀(100000) > 洁尔佩塔(30000)；
        # 喂养约束：洁尔佩塔（自然附着施加者）先于提弗洛斯出手
        self.assertEqual(self._rotate(["提弗洛斯", "莱万汀", "洁尔佩塔", "?"]), ["2", "3", "1", "4"])

    def test_no_feeder_falls_back_to_damage_order(self):
        # 无自然附着施加者：提弗洛斯回退保守口径（103074.8，仍最高），
        # 无喂养约束 → 纯伤害序
        self.assertEqual(self._rotate(["提弗洛斯", "莱万汀", "弭弗", "?"]), ["1", "2", "3", "4"])

    def test_any_of_multiple_feeders(self):
        # 任一喂养者先手即满足约束（不要求全部先手）
        entries = self._ENTRIES + [{"character": "噗切娜", "cycle_expect": 20000.0}]
        caps = dict(self._CAPS)
        caps["噗切娜"] = _caps_map(噗切娜=(("自然",), False))["噗切娜"]
        path2 = Path(self._tmp.name) / "two_feeders.json"
        path2.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        tokens = generate_damage_rotation(
            ["提弗洛斯", "莱万汀", "洁尔佩塔", "噗切娜"], path=path2, capabilities=caps
        )
        # 洁尔佩塔(30000) 先于噗切娜(20000) 满足约束即可，提弗洛斯随即出手
        self.assertEqual(tokens, ["2", "3", "1", "4"])

    def test_explicit_baseline_skips_dependency_check(self):
        # 显式 baseline：无依赖信息、不查能力表 → 纯伤害序
        baseline = {"提弗洛斯": 129661.5, "莱万汀": 100000.0, "洁尔佩塔": 30000.0}
        self.assertEqual(
            generate_damage_rotation(["提弗洛斯", "莱万汀", "洁尔佩塔", "?"], baseline),
            ["1", "2", "3", "4"],
        )

    def test_auto_rotation_segments_follow_feeder_order(self):
        rotation = generate_auto_rotation(
            ["提弗洛斯", "莱万汀", "洁尔佩塔", "?"], path=self._path, capabilities=self._CAPS
        )
        digits = [t for t in rotation if t.isdigit()]
        self.assertEqual(digits, ["2", "3", "1", "4"])
        # 段结构随新顺序：战技后紧跟对应号位终结技
        self.assertIn("ult_2", rotation)
        self.assertIn("ult_1", rotation)

    def test_entries_expose_requires_only_when_fed(self):
        entries = load_team_baseline_entries(
            ["提弗洛斯", "洁尔佩塔", "?", "?"], path=self._path, capabilities=self._CAPS
        )
        self.assertEqual(entries["提弗洛斯"]["requires_attach"], ["自然"])
        self.assertEqual(entries["提弗洛斯"]["value"], 129661.5)
        starved = load_team_baseline_entries(
            ["提弗洛斯", "弭弗", "?", "?"], path=self._path, capabilities=self._CAPS
        )
        self.assertIsNone(starved["提弗洛斯"]["requires_attach"])
        self.assertEqual(starved["提弗洛斯"]["value"], 103074.8)

    def test_attach_requirement_accepts_element_list(self):
        # requires.attach 支持列表（任一元素满足）——「非 X 附着」类依赖预留
        entries = [dict(self._ENTRIES[0], full_caliber_requires={"attach": ["灼热", "自然"]})]
        entries += self._ENTRIES[1:]
        caps = dict(self._CAPS)
        caps["莱万汀"] = _caps_map(莱万汀=(("灼热",), False))["莱万汀"]
        path2 = Path(self._tmp.name) / "list_requires.json"
        path2.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        tokens = generate_damage_rotation(
            ["提弗洛斯", "莱万汀", "洁尔佩塔", "?"], path=path2, capabilities=caps
        )
        # 莱万汀（灼热）先手即满足任一元素 → 提弗洛斯紧随其后
        self.assertEqual(tokens, ["2", "1", "3", "4"])

    def test_real_data_feeder_precedes_tifuluosi(self):
        from src.data.character_capabilities import load_character_capabilities

        real_caps = load_character_capabilities()
        feeders = [n for n, c in real_caps.items() if "自然" in c.attach_elements]
        self.assertTrue(feeders, "真实快照应含自然附着施加者（洁尔佩塔/噗切娜等）")
        tokens = generate_damage_rotation(["提弗洛斯", feeders[0], "?", "?"], capabilities=real_caps)
        # 提弗洛斯在 1 号位（token "1"），喂养者必须先手
        self.assertLess(tokens.index("2"), tokens.index("1"))


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
