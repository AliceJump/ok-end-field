"""Fresh head identity, queue precedence and release-window decisions."""

import importlib.util
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ok import FinishedException, TaskDisabledException

from src.data.combo_queue import bind_combo_queue, read_combo_buttons, same_combo_head
from src.data.release_burst_planner import ReleaseBurstAction, ReleaseBurstPlanner
from src.data.skill_timing import load_skill_timings
from src.image.combo_skill_detector import ComboObservation, ComboSlotObservation
from src.tasks.onetime.TimedCombatLogic import TimedCombatLogic
from tests.TestComboSkillDetector import _Task, render_heads
from tests.TestSkillTiming import FakeTask


def observed(team, native_ids, now=0):
    buttons = read_combo_buttons()
    slots = []
    for pos, native in enumerate(native_ids, 1):
        character = next((b["character"] for b in buttons if b["owner_native_id"] == native), None)
        slot = team.index(character) + 1 if character in team else None
        slots.append(ComboSlotObservation(pos, (0, 0, 1, 1), character, native, slot, .99, .4))
    return ComboObservation(True, now, tuple(team), (1920, 1080), tuple(slots), "observed")


class QueueTask(FakeTask):
    def __init__(self, team, ids):
        super().__init__()
        self._battle_team = list(team)
        self.ids = ids
        self.link = True
        self.sp = 0
        self.expected = []
        self.accept = True

    def observe_link_skills(self, **kwargs):
        return observed(self._battle_team, self.ids, self.now)

    def use_link_skill(self, *, expected=None):
        self.expected.append(expected)
        if not self.accept:
            return False
        self.now += .05  # A refreshed identity costs time before the input.
        self.link_skill_input_at = self.now
        return super().use_link_skill()


class TestComboQueueScheduling(unittest.TestCase):
    def test_catalog_export_rejects_external_input_output_and_path_escape(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("combo_button_export", root / "scripts/skill-data/export_combo_buttons.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory).resolve()
            with self.assertRaisesRegex(ValueError, "main checkout"):
                module.export(external, root / "assets/data/combo_buttons")
            with self.assertRaisesRegex(ValueError, "escapes"):
                module.contained(root, "../outside.json")
            common = Path(module.subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], text=True).strip())
            with self.assertRaisesRegex(ValueError, "named verification"):
                module.export(common.parent / "tmp/capture", external)

    def test_binding_keeps_native_order_and_stops_at_unknown_without_promotion(self):
        team = ["佩丽卡", "弧光", "祀"]
        ids = ["chr_0004_pelica", "chr_0007_aurora", "chr_0036_jsspsi"]
        # Use catalog IDs rather than assuming a translated name is an ID.
        ids[1] = next(b["owner_native_id"] for b in read_combo_buttons() if b["character"] == "弧光")
        obs = observed(team, ids)
        queue = bind_combo_queue(obs, team, set(), load_skill_timings(), 0)
        self.assertEqual([c.slot for c in queue], ["1", "2", "3"])
        changed = replace(obs, slots=(obs.slots[0], replace(obs.slots[1], character=None), obs.slots[2]))
        self.assertEqual(len(bind_combo_queue(changed, team, set(), load_skill_timings(), 0)), 1)
        self.assertFalse(bind_combo_queue(obs, team, {"1"}, load_skill_timings(), 0))
        self.assertFalse(bind_combo_queue(obs, team[::-1], set(), load_skill_timings(), 0))
        self.assertFalse(bind_combo_queue(obs, team, set(), load_skill_timings(), .501))
        self.assertFalse(bind_combo_queue(obs, team, set(), load_skill_timings(), -.1))

    def test_shared_variants_and_cross_character_icons_are_not_base_quotes(self):
        for name in ("庄方宜", "卡缪", "提弗洛斯", "洛茜"):
            native = next(b["owner_native_id"] for b in read_combo_buttons() if b["character"] == name)
            self.assertFalse(bind_combo_queue(observed([name], [native]), [name], set(), load_skill_timings(), 0))
        native = next(b["owner_native_id"] for b in read_combo_buttons() if b["character"] == "陈千语")
        team = ["陈千语", "洛茜"]
        self.assertFalse(bind_combo_queue(observed(team, [native]), team, set(), load_skill_timings(), 0))

    def test_queue_precedence_and_blocker_opportunity_cost_change_first_action(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫", "陈千语"])
        head = ReleaseBurstAction("1", "link", 1, 100, queue_position=1)
        tail = ReleaseBurstAction("2", "link", 1, 10000, queue_position=2)
        ult = ReleaseBurstAction("3", "ult", 2, 2000)
        decision = planner.choose((head, tail, ult), 0, 0, horizon=3)
        self.assertEqual(decision.sequence, (("1", "link"), ("2", "link")))
        costly = replace(head, duration=3)
        decision = planner.choose((costly, tail, ult), 0, 0, horizon=3)
        self.assertEqual(decision.action.kind, "ult")
        self.assertNotIn(("2", "link"), decision.sequence)
        self.assertIsNone(planner.choose((tail,), 0, 0))

    def test_team_window_precedes_queue_and_does_not_invent_sp(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫", "陈千语"])
        actions = (ReleaseBurstAction("1", "ult", 2, 0),
                   ReleaseBurstAction("2", "link", 1, 1000, queue_position=1),
                   ReleaseBurstAction("3", "battle", 1, 99999, 100, 100))
        decision = planner.choose(actions, 0, 0)
        self.assertEqual(decision.sequence, (("1", "ult"), ("2", "link")))
        self.assertAlmostEqual(decision.damage, 1220.0000047683716)
        self.assertFalse(planner.bonuses)

    def test_four_links_keep_queue_order_and_expensive_actions_can_be_skipped(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫", "陈千语", "祀"])
        actions = tuple(ReleaseBurstAction(str(i), "link", .5, i * 1000, queue_position=i)
                        for i in range(1, 5))
        self.assertEqual(planner.choose(actions[::-1], 0, 0).sequence,
                         tuple((str(i), "link") for i in range(1, 5)))
        repeated = (actions[0], replace(actions[1], queue_position=3))
        self.assertIsNone(planner.choose(repeated, 0, 0))

    def test_real_step_opens_team_window_then_reobserves_head_for_e(self):
        native = next(b["owner_native_id"] for b in read_combo_buttons() if b["character"] == "狼卫")
        task = QueueTask(["安塔尔", "狼卫"], [native])
        task.ults = {"1"}
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(task._battle_team, reset_runtime=True)
        logic.step()
        self.assertEqual(task.keys, ["ult_1"])
        self.assertFalse(task.expected)
        logic.step()
        self.assertEqual(task.keys, ["ult_1", "e"])
        self.assertGreater(task.expected[0].observed_at, 0)
        self.assertEqual(logic.active_slot, "2")

    def test_full_combo_not_credited_for_buff_covering_only_handoff(self):
        planner = ReleaseBurstPlanner(["安塔尔", "狼卫"])
        planner.confirm("1", "ult", 0)
        basic = ReleaseBurstAction("2", "link", 1, 1000, queue_position=1, damage_duration=4)
        result = planner.choose((basic,), 0, 10)
        self.assertEqual(result.damage, 1000)
        early = planner.choose((basic,), 0, 2)
        self.assertGreater(early.damage, 1000)

    def test_normal_output_can_win_without_falling_through_to_automatic_e(self):
        planner = ReleaseBurstPlanner(["佩丽卡", "狼卫"])
        link = ReleaseBurstAction("1", "link", 3, 100, queue_position=1)
        normal = ReleaseBurstAction("2", "normal", 3, 1000)
        decision = planner.choose((link, normal), 0, 0, horizon=3)
        self.assertEqual(decision.action.kind, "normal")
        task = QueueTask(["佩丽卡", "狼卫"], ["chr_0004_pelica"])
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(task._battle_team, reset_runtime=True)
        with patch.object(logic, "_release_burst_decision", return_value=decision):
            logic.step()
        self.assertFalse(task.keys)
        self.assertTrue(logic._holding)

    def test_known_head_real_step_uses_only_its_profile_and_rechecks_identity(self):
        task = QueueTask(["佩丽卡", "狼卫"], ["chr_0004_pelica"])
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(task._battle_team, reset_runtime=True)
        logic.step()
        self.assertEqual(task.keys, ["e"])
        self.assertIsNotNone(task.expected[0])
        self.assertEqual(logic.active_slot, "1")
        self.assertEqual([p.skill_id for p in logic.active], ["chr_0004_pelica_combo_skill"])
        self.assertEqual(logic.started, task.link_skill_input_at)
        self.assertFalse(logic._combo_queue)

    def test_failed_or_negative_known_input_does_not_commit_bonus_or_lock(self):
        task = QueueTask(["佩丽卡"], ["chr_0004_pelica"])
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(task._battle_team, reset_runtime=True)
        task.accept = False
        logic.step()
        self.assertFalse(task.keys)
        self.assertFalse(logic.active)
        self.assertFalse(logic.release_burst.last_confirmed)
        task.accept = True
        task.probe_combat_action_block_reason = lambda: "during_skill"
        logic.step()
        self.assertEqual(task.keys, ["e"])
        self.assertFalse(logic.active)
        self.assertFalse(logic.release_burst.last_confirmed)

    def test_optional_observation_errors_fall_back_but_cancellation_propagates(self):
        task = QueueTask(["佩丽卡"], ["chr_0004_pelica"])
        logic = TimedCombatLogic(task, load_skill_timings(), clock=lambda: task.now)
        logic._configure_team(task._battle_team, reset_runtime=True)
        with patch.object(task, "observe_link_skills", side_effect=RuntimeError("frame")):
            logic.step()
        self.assertEqual(task.keys, ["e"])
        for error in (FinishedException, TaskDisabledException):
            with patch.object(task, "observe_link_skills", side_effect=error("stop")):
                logic._clear_active(task.now)
                with self.assertRaises(error):
                    logic._observe_combo_queue()

    def test_press_adapter_rejects_stale_changed_unknown_or_changed_team(self):
        for change in ("head", "unknown", "stale", "team", "ok"):
            task = _Task()
            task.next_frame = lambda: None
            expected = task.observe_link_skills()
            if change == "head":
                task.frame = render_heads(("chr_0012_avywen",))
            elif change == "unknown":
                task.frame = render_heads((None,))
            elif change == "stale":
                expected = replace(expected, observed_at=time.monotonic() - 1)
            elif change == "team":
                task._battle_team = list(reversed(task._battle_team))
            self.assertEqual(bool(task.use_link_skill(expected=expected)), change == "ok")
            self.assertEqual(task.keys, ["e"] if change == "ok" else [])
        self.assertTrue(same_combo_head(expected, expected))
