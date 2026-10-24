import re
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from src.core.BattleConfig import (
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
    KEY_TIMING_DECISION_WINDOW,
    KEY_TIMING_ROTATION,
)
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstDecision
from src.data.skill_timing import load_skill_timings
from src.gui.combat_decision_window import (
    _format_entry,
    clear_combat_decisions,
    combat_decision_history_revision,
    combat_decision_revision,
    combat_decision_snapshot,
    publish_combat_decision,
)
from src.patches import combat_decision_trace_patch
from src.tasks.mixin.battle_mixin import BattleMixin
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestSkillTiming import FakeTask, confirm_dark_highlights, settle_highlights


class TestCombatDecisionTrace(unittest.TestCase):
    def setUp(self):
        clear_combat_decisions()

    def test_timing_window_is_opt_in_child_of_timing_mode(self):
        self.assertFalse(DEFAULT_BATTLE_CONFIG[KEY_TIMING_DECISION_WINDOW])
        sub_configs = BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"]
        self.assertIn(KEY_TIMING_DECISION_WINDOW, sub_configs[True])
        self.assertNotIn(KEY_TIMING_DECISION_WINDOW, sub_configs[False])

    def test_installed_trace_preserves_step_precast_revalidation_and_free_cast(self):
        # Install the real startup wrappers, then restore every patched method.
        # Ordinary scheduler tests intentionally do not run startup hooks.
        for enabled in (False, True):
            for changed in (False, True):
                with self.subTest(enabled=enabled, changed=changed), ExitStack() as stack:
                    for cls, names in (
                        (TimedCombatLogic, ('run', 'step', '_allowed', '_try_battle_token',
                                           '_enemy_operation_paused', '_probe_action_feedback')),
                        (BattleMixin, ('is_link_skill_ready', 'use_link_skill', 'use_ult')),
                    ):
                        for name in names:
                            stack.enter_context(patch.object(cls, name, getattr(cls, name)))
                    stack.enter_context(patch.object(combat_decision_trace_patch, '_PATCH_INSTALLED', False))
                    combat_decision_trace_patch.install_combat_decision_trace_patch()
                    task = FakeTask()
                    task.get_battle_config = lambda key, default=None: (
                        True if key == KEY_TIMING_ROTATION else enabled if key == KEY_TIMING_DECISION_WINDOW else default)
                    task.sp = 267
                    task.detect_current_char_index = lambda: 0
                    task.battle_pulses = {'1'}
                    logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
                    logic._configure_team(['庄方宜', '佩丽卡', '艾维文娜', '梨诺'], reset_runtime=True)
                    logic.release_burst.confirm('1', 'ult', 0)
                    logic.free_battle_once.add('1')
                    task.now = 3.2
                    settle_highlights(logic)
                    frames = []

                    def refresh():
                        task.now += .05
                        frames.append(task.now)
                        if changed:
                            task.battle_pulses.clear()

                    task.next_frame = refresh
                    decision = ReleaseBurstDecision(ReleaseBurstAction('1', 'battle', .55, 211550, 0, 0),
                                                     (('1', 'battle'),), 211550, 211550)
                    with patch.object(logic, '_release_burst_decision', return_value=decision):
                        logic.step()
                    self.assertEqual(len(frames), 4)
                    self.assertEqual(task.keys, [] if changed else ['1'])
                    self.assertTrue(logic.release_burst.stance_at('1', task.now).free_battle)
                    if changed:
                        self.assertIsNone(logic.pending)
                    else:
                        self.assertEqual(logic.pending[2], 0)
                        task.battle_pulses.clear()
                        confirm_dark_highlights(logic)
                        self.assertFalse(logic.release_burst.stance_at('1', task.now).free_battle)

    def test_same_semantic_decision_updates_live_row_without_spamming_history(self):
        start_revision = combat_decision_revision()
        start_history_revision = combat_decision_history_revision()
        publish_combat_decision(
            "余烬",
            "连携技",
            "等待",
            "上一动作还没到允许其他角色接手的时间点。",
            "已执行 0.391s / 至少需要 0.483s。",
            dedupe_key=("link", "wait"),
        )
        publish_combat_decision(
            "余烬",
            "连携技",
            "等待",
            "上一动作还没到允许其他角色接手的时间点。",
            "已执行 0.421s / 至少需要 0.483s。",
            dedupe_key=("link", "wait"),
        )

        current, history = combat_decision_snapshot()
        self.assertEqual(len(history), 1)
        self.assertEqual(current.detail, "已执行 0.421s / 至少需要 0.483s。")
        self.assertEqual(combat_decision_revision(), start_revision + 2)
        self.assertEqual(combat_decision_history_revision(), start_history_revision + 1)

    def test_new_decision_key_appends_history(self):
        publish_combat_decision("洁尔佩塔", "战技", "等待", "技力不足。", dedupe_key=("battle", "wait"))
        publish_combat_decision(
            "洁尔佩塔",
            "战技",
            "准备释放",
            "技力已经达到释放门槛。",
            dedupe_key=("battle", "ready"),
        )
        _current, history = combat_decision_snapshot()
        self.assertEqual(len(history), 2)

    def test_history_revision_keeps_changing_after_history_reaches_capacity(self):
        for index in range(200):
            publish_combat_decision(
                "测试角色",
                "战技",
                "等待",
                f"第 {index} 条决策。",
                dedupe_key=("capacity", index),
            )
        _current, history = combat_decision_snapshot()
        self.assertEqual(len(history), 200)
        before = combat_decision_history_revision()

        publish_combat_decision(
            "测试角色",
            "战技",
            "准备释放",
            "新的决策应替换最旧历史并继续触发窗口刷新。",
            dedupe_key=("capacity", 200),
        )
        _current, history = combat_decision_snapshot()
        self.assertEqual(len(history), 200)
        self.assertEqual(combat_decision_history_revision(), before + 1)
        self.assertEqual(history[-1].state, "准备释放")

    def test_display_text_uses_meaning_instead_of_internal_field_names(self):
        publish_combat_decision(
            "未知角色",
            "连携技",
            "允许尝试",
            "上一角色的技能已经达到允许其他角色接手的时间点。",
            "技能首次实际生效时间约为 0.433s；允许其他角色接手的时间点约为 0.483s。",
            dedupe_key=("link", "ready"),
        )
        current, _history = combat_decision_snapshot()
        text = _format_entry(current, multiline=True)
        self.assertNotIn("handoff", text.lower())
        self.assertNotIn("effect_start", text.lower())
        self.assertNotIn("active_slot", text.lower())
        self.assertRegex(text, re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}"))
        self.assertIn("允许其他角色接手", text)
        self.assertIn("首次实际生效", text)


if __name__ == "__main__":
    unittest.main()
