import re
import unittest

from src.core.BattleConfig import (
    BATTLE_CONFIG_TYPE,
    DEFAULT_BATTLE_CONFIG,
    KEY_TIMING_DECISION_WINDOW,
    KEY_TIMING_ROTATION,
)
from src.gui.combat_decision_window import (
    _format_entry,
    clear_combat_decisions,
    combat_decision_revision,
    combat_decision_snapshot,
    publish_combat_decision,
)


class TestCombatDecisionTrace(unittest.TestCase):
    def setUp(self):
        clear_combat_decisions()

    def test_timing_window_is_opt_in_child_of_timing_mode(self):
        self.assertFalse(DEFAULT_BATTLE_CONFIG[KEY_TIMING_DECISION_WINDOW])
        sub_configs = BATTLE_CONFIG_TYPE[KEY_TIMING_ROTATION]["sub_configs"]
        self.assertIn(KEY_TIMING_DECISION_WINDOW, sub_configs[True])
        self.assertNotIn(KEY_TIMING_DECISION_WINDOW, sub_configs[False])

    def test_same_semantic_decision_updates_live_row_without_spamming_history(self):
        start_revision = combat_decision_revision()
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
