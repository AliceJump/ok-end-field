import ast
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from src.core import BattleConfig
from src.core.BattleConfig import DEFAULT_BATTLE_CONFIG, KEY_REACTIVE_DODGE
from src.tasks.onetime.ReactiveDodge import MaaEndDodgeDetector, ReactiveDodge, needs_dodge


def box(name="EnemyDodge", x=0, y=0, width=10, height=10):
    return SimpleNamespace(name=name, x=x, y=y, width=width, height=height)


class FakeDetector:
    def __init__(self):
        self.boxes = [box()]
        self.closed = False
        self.calls = 0
        self.fail = False

    def detect(self, frame, compatibility=True):
        self.calls += 1
        if self.fail:
            raise RuntimeError("inference failed")
        return self.boxes

    def release(self):
        self.closed = True


class FakeTask:
    def __init__(self):
        self.config = dict(DEFAULT_BATTLE_CONFIG)
        self.config[KEY_REACTIVE_DODGE] = True
        self.now = 1.0
        self.frame = SimpleNamespace(shape=(720, 1280, 3))
        self.events = []

    def get_battle_config(self, key, default=None):
        return self.config.get(key, default)

    def active_time(self):
        return self.now

    def press_key(self, key, **kwargs):
        self.events.append(key)

    def log_warning(self, message):
        self.events.append("warning")

    def sleep(self, seconds):
        self.now += seconds


class TestReactiveDodge(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()
        self.detector = FakeDetector()
        self.dodge = ReactiveDodge(self.task, lambda: self.detector)
        self.release = lambda: self.task.events.append("release")

    def ready(self):
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.detector.calls, 0)

    def test_default_off_never_loads(self):
        self.assertFalse(DEFAULT_BATTLE_CONFIG[KEY_REACTIVE_DODGE])
        self.task.config[KEY_REACTIVE_DODGE] = False
        self.assertFalse(self.dodge.step(self.release))
        self.assertIsNone(self.dodge.detector)

    def test_compatibility_edges_and_scaling(self):
        self.assertFalse(needs_dodge([box(x=500, y=265, width=0, height=0)], 1280, 720))
        self.assertTrue(needs_dodge([box(x=780, y=265, width=0, height=0)], 1280, 720))
        self.assertTrue(needs_dodge([box(x=500, y=590, width=0, height=0)], 1280, 720))
        self.assertFalse(needs_dodge([box(x=1000, y=265, width=0, height=0)], 2560, 720))
        self.assertTrue(needs_dodge([box(x=500, y=265)], 1280, 720, False))
        self.assertTrue(needs_dodge([box("EnemyAttackGroundDodge", x=500, y=265)], 1280, 720))
        self.assertFalse(needs_dodge([box("EnemyLocked")], 1280, 720))
        self.assertFalse(needs_dodge([box()], 0, 720))

    def test_repeated_warning_is_bounded_and_releases_attack_first(self):
        self.ready()
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.task.events, ["release", "lshift"])
        self.assertFalse(self.dodge.step(self.release))
        self.task.now += 0.61
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.detector.calls, 2)
        self.assertGreater(self.task._enemy_direction_no_center_until, self.task.now)

    def test_pursuit_guard_and_action_guard(self):
        self.ready()
        self.assertFalse(self.dodge.step(self.release, allowed=lambda: False))
        self.task._enemy_direction_next_dodge_at = 2.0
        self.assertFalse(self.dodge.step(self.release))
        self.assertEqual(self.detector.calls, 0)

    def test_deadline_and_stale_frame(self):
        self.ready()
        self.assertFalse(self.dodge.step(self.release, deadline=1.0))

        def slow(frame, compatibility=True):
            self.task.now += 0.51
            return [box()]

        self.detector.detect = slow
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.task.events, [])
        self.assertTrue(self.dodge.step(self.release))
        self.assertTrue(self.dodge.step(self.release))
        self.assertTrue(self.dodge.failed)
        self.assertFalse(self.dodge.step(self.release))
        self.assertEqual(self.task.events, ["warning"])

    def test_action_guard_rechecked_after_inference(self):
        self.ready()
        allowed = iter([True, False])
        self.assertFalse(self.dodge.step(self.release, allowed=lambda: next(allowed)))
        self.assertEqual(self.task.events, [])

    def test_timely_inference_resets_slow_frame_count(self):
        self.ready()

        def slow(frame, compatibility=True):
            self.task.now += 0.51
            return []

        self.detector.detect = slow
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.dodge.slow_frames, 1)
        self.detector.detect = lambda frame, compatibility=True: []
        self.assertFalse(self.dodge.step(self.release))
        self.assertEqual(self.dodge.slow_frames, 0)
        self.assertFalse(self.dodge.failed)

    def test_invalid_frame_is_skipped_without_disabling_detector(self):
        self.ready()
        self.task.frame = None
        self.assertTrue(self.dodge.step(self.release))
        self.task.frame = SimpleNamespace(shape=(0, 1280, 3))
        self.assertTrue(self.dodge.step(self.release))
        self.assertFalse(self.dodge.failed)
        self.assertEqual(self.detector.calls, 0)
        self.task.frame = SimpleNamespace(shape=(720, 1280, 3))
        self.assertTrue(self.dodge.step(self.release))
        self.assertEqual(self.detector.calls, 1)
        self.assertNotIn("warning", self.task.events)

    def test_old_capture_is_skipped_before_and_after_inference(self):
        self.ready()
        self.assertTrue(self.dodge.step(self.release, frame_captured_at=0.1))
        self.assertEqual(self.detector.calls, 0)

        def moderately_slow(frame, compatibility=True):
            self.task.now += 0.4
            return [box()]

        self.detector.detect = moderately_slow
        # 0.2s old at inference start, 0.6s old by its end.
        self.assertTrue(self.dodge.step(self.release, frame_captured_at=0.8))
        self.assertEqual(self.task.events, [])
        self.assertFalse(self.dodge.failed)
        self.assertEqual(self.dodge.slow_frames, 0)

    def test_inference_failure_disables_once_and_releases(self):
        self.ready()
        self.detector.fail = True
        self.assertTrue(self.dodge.step(self.release))
        self.assertFalse(self.dodge.step(self.release))
        self.assertTrue(self.detector.closed)
        self.assertEqual(self.task.events, ["warning"])

    def test_missing_model_disables_once(self):
        def missing():
            raise FileNotFoundError("missing model")

        self.dodge.factory = missing
        self.assertTrue(self.dodge.step(self.release))
        self.assertFalse(self.dodge.step(self.release))
        self.assertEqual(self.task.events, ["warning"])

    def test_close_releases_detector(self):
        self.ready()
        self.dodge.close()
        self.assertTrue(self.detector.closed)

    def test_input_control_flow_propagates(self):
        self.ready()

        class TaskStopped(Exception):
            pass

        def stopped(key, **kwargs):
            raise TaskStopped()

        self.task.press_key = stopped
        with self.assertRaises(TaskStopped):
            self.dodge.step(self.release)
        self.assertFalse(self.dodge.failed)
        self.assertNotIn("warning", self.task.events)

    def test_recovery_wait_is_bounded_by_deadline(self):
        self.ready()
        self.assertTrue(self.dodge.step(self.release, deadline=1.04))
        self.assertAlmostEqual(self.task.now, 1.04)

    def test_actual_approach_method_honors_reactive_guard(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/mixin/battle_mixin.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BattleMixin")
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "approach_enemy")
        namespace = {"KEY_REACTIVE_DODGE": KEY_REACTIVE_DODGE}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_approach", "exec"), namespace)
        task = SimpleNamespace(
            active_time=lambda: 10.0,
            _reactive_dodge_next_at=10.5,
            get_battle_config=lambda key, default: default,
            log_info=Mock(),
            click=Mock(),
            dodge_forward=Mock(),
            last_no_number_action_time=0,
        )
        namespace["approach_enemy"](task)
        task.dodge_forward.assert_not_called()
        task._reactive_dodge_next_at = 9.0
        namespace["approach_enemy"](task)
        task.dodge_forward.assert_called_once()
        self.assertEqual(task._reactive_dodge_next_at, 9.0)  # Disabled feature preserves old behavior.
        task.last_no_number_action_time = 0
        task.get_battle_config = lambda key, default: True if key == KEY_REACTIVE_DODGE else default
        namespace["approach_enemy"](task)
        self.assertAlmostEqual(task._reactive_dodge_next_at, 10.6)
        self.ready()
        self.task._reactive_dodge_next_at = 2.0
        self.assertFalse(self.dodge.step(self.release))
        self.assertEqual(self.detector.calls, 0)

    def test_single_inference_with_isolated_class_postprocessing(self):
        output = np.zeros((1, 26, 8400), dtype=np.float32)
        seen = []

        def postprocess(values, padding, shape, threshold, label):
            self.assertEqual(values.shape, (1, 26, 8400))
            self.assertEqual(values[0, 0, 0], 0)
            values[0, 0, 0] = 999
            self.assertEqual(threshold, 0.7)
            seen.append(label)
            return [box("EnemyAttackGroundDodge" if label == 12 else "EnemyDodge")]

        backend = SimpleNamespace(
            input_height=640,
            input_width=640,
            _preprocess=lambda frame: ("input", (0, 0)),
            compiled_model=Mock(return_value={"output": output}),
            input_layer="input",
            output_layer="output",
            _postprocess=postprocess,
        )
        adapter = MaaEndDodgeDetector.__new__(MaaEndDodgeDetector)
        adapter.detector = backend
        self.assertEqual(len(adapter.detect(self.task.frame)), 2)
        backend.compiled_model.assert_called_once()
        self.assertEqual(seen, [12, 14])
        self.assertEqual(output[0, 0, 0], 0)

    def test_compatibility_mask_prevents_nms_from_hiding_a_real_warning(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/yolo/openvino_detector.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_postprocess")
        namespace = {"np": np, "cv2": cv2, "Box": lambda x, y, w, h: box(x=x, y=y, width=w, height=h)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "real_decoder_compat", "exec"), namespace)
        raw = np.zeros((1, 26, 8400), dtype=np.float32)
        # Two overlapping EnemyDodge warnings straddle the exclusion boundary.
        # The higher-scored warning is inside the excluded area (x = 778).
        for index, center_x in enumerate((778, 782)):
            raw[0, :4, index] = [center_x / 2, 340, 40, 40]
            raw[0, 4 + 14, index] = 0.99 if index == 0 else 0.90
        backend = SimpleNamespace(
            input_height=640,
            input_width=640,
            iou_threshold=0.45,
            dic_labels={12: "EnemyAttackGroundDodge", 14: "EnemyDodge"},
            _preprocess=lambda frame: ("input", (140, 0)),
            compiled_model=Mock(return_value={"output": raw}),
            input_layer="input",
            output_layer="output",
        )
        backend._postprocess = lambda output, padding, shape, threshold, label: namespace["_postprocess"](
            backend, output, padding, shape, threshold, label
        )
        adapter = MaaEndDodgeDetector.__new__(MaaEndDodgeDetector)
        adapter.detector = backend
        unfiltered = adapter.detect(self.task.frame, compatibility=False)
        self.assertEqual(len(unfiltered), 1)
        self.assertFalse(needs_dodge(unfiltered, 1280, 720))
        filtered = adapter.detect(self.task.frame, compatibility=True)
        self.assertEqual(len(filtered), 1)
        self.assertTrue(needs_dodge(filtered, 1280, 720))
        self.assertGreaterEqual(filtered[0].x + filtered[0].width / 2, 780)
        self.assertAlmostEqual(float(raw[0, 4 + 14, 0]), 0.99, places=5)

    def test_real_decoder_with_22_class_output(self):
        # Load the actual pure decoder method without importing the Windows
        # device layer; no substitute decoder implementation is used.
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/yolo/openvino_detector.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_postprocess")
        namespace = {"np": np, "cv2": cv2, "Box": lambda x, y, w, h: box(x=x, y=y, width=w, height=h)}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_yolo_decoder", "exec"), namespace)
        backend = SimpleNamespace(
            input_height=640,
            input_width=640,
            iou_threshold=0.45,
            dic_labels={12: "EnemyAttackGroundDodge", 14: "EnemyDodge"},
        )
        output = np.zeros((1, 26, 8400), dtype=np.float32)
        for index, label in enumerate((0, 12, 14)):
            output[0, :4, index] = [320, 320, 40, 40]
            output[0, 4 + label, index] = 0.99 - index * 0.01
        decoded = []
        for label in (12, 14):
            decoded.extend(namespace["_postprocess"](backend, output.copy(), (140, 0), (720, 1280), 0.7, label))
        self.assertEqual([b.name for b in decoded], ["EnemyAttackGroundDodge", "EnemyDodge"])
        self.assertEqual((decoded[0].x, decoded[0].y, decoded[0].width), (600, 320, 80))

    def test_both_loops_have_hook_and_cleanup(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("AutoCombatLogic", "TimedCombatLogic"):
            source = (root / "src/tasks/onetime" / f"{name}.py").read_text(encoding="utf-8")
            tree = ast.parse(source)
            hooks = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "step"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "reactive_dodge"
            ]
            self.assertEqual(len(hooks), 2 if name == "AutoCombatLogic" else 1)
            self.assertIn("reactive_dodge.close()", source)
            self.assertIn("if no_battle:", source)
            if name == "TimedCombatLogic":
                self.assertIn("allowed=lambda: self._allowed()", source)
                self.assertIn("not self.main_control.waiting_for_confirmation", source)
            else:
                self.assertIn("if not self._exit_pending and", source)

    def test_actual_timed_loop_respects_guards_and_consumes_dodge_frame(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/onetime/TimedCombatLogic.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        for no_battle, exiting, skill_allowed, switching in (
            (False, False, True, False),
            (True, False, True, False),
            (False, True, True, False),
            (False, False, False, False),
            (False, False, True, True),
        ):
            with self.subTest(no_battle=no_battle, exiting=exiting, skill_allowed=skill_allowed, switching=switching):
                task = FakeTask()
                task.mouse_up = Mock()
                task.log_info = Mock()
                task.next_frame = lambda task=task: setattr(task, "now", task.now + 0.1)
                task._check_single_exit_condition = lambda exiting=exiting: exiting
                task.is_combat_ended = lambda condition: False
                task.click = Mock()
                calls = []

                def step(release, allowed, deadline, frame_captured_at=None, calls=calls):
                    result = allowed()
                    calls.append(result)
                    if result:
                        release()
                    return result

                dodge = SimpleNamespace(step=step, close=Mock())
                namespace = {
                    "ReactiveDodge": lambda task, dodge=dodge: dodge,
                    "reset_enemy_presence_probe": lambda task: None,
                }
                exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_timed_loop", "exec"), namespace)
                logic = SimpleNamespace(
                    task=task,
                    main_control=SimpleNamespace(
                        reset=Mock(), waiting_for_confirmation=lambda now, switching=switching: switching
                    ),
                    store={},
                    _hold=Mock(),
                    _enemy_operation_paused=lambda: False,
                    _allowed=lambda skill_allowed=skill_allowed: skill_allowed,
                    _clock=task.active_time,
                    _NORMAL_ATTACK_REASSERT_INTERVAL=0.25,
                    team=[],
                    _detect_team=Mock(),
                    step=Mock(),
                )
                namespace["run"](logic, no_battle=no_battle, deadline=1.4)
                dodge.close.assert_called_once()
                if no_battle or exiting:
                    self.assertEqual(calls, [])
                    logic.step.assert_not_called()
                elif skill_allowed and not switching:
                    self.assertTrue(calls)
                    self.assertTrue(all(calls))
                    logic.step.assert_not_called()
                else:
                    self.assertTrue(calls)
                    self.assertFalse(any(calls))
                    logic.step.assert_called()

    def test_timed_enemy_pause_is_updated_before_reactive_dodge(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/onetime/TimedCombatLogic.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        task = FakeTask()
        task.mouse_up = Mock()
        task.log_info = Mock()
        task.next_frame = lambda: setattr(task, "now", task.now + 0.1)
        task._check_single_exit_condition = lambda: False
        task.is_combat_ended = lambda condition: False
        events = []

        def paused():
            events.append("pause")
            return True

        def dodge_step(release, allowed, deadline, frame_captured_at):
            events.append("dodge")
            self.assertAlmostEqual(frame_captured_at, task.active_time())
            return True

        dodge = SimpleNamespace(step=dodge_step, close=Mock())
        namespace = {"ReactiveDodge": lambda task: dodge, "reset_enemy_presence_probe": lambda task: None}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_timed_pause", "exec"), namespace)
        logic = SimpleNamespace(
            task=task,
            main_control=SimpleNamespace(reset=Mock(), waiting_for_confirmation=lambda now: False),
            store={},
            _hold=Mock(),
            _enemy_operation_paused=paused,
            _allowed=lambda: True,
            _clock=task.active_time,
            _NORMAL_ATTACK_REASSERT_INTERVAL=0.25,
            team=[],
            _detect_team=Mock(),
            step=Mock(),
        )
        self.assertFalse(namespace["run"](logic, deadline=1.3))
        self.assertEqual(events[:2], ["pause", "dodge"])
        self.assertEqual(events.count("pause"), events.count("dodge"))
        logic.step.assert_not_called()
        dodge.close.assert_called_once()

    def test_actual_normal_loop_consumes_dodge_frame_and_skips_inactive_paths(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/onetime/AutoCombatLogic.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AutoCombatLogic")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_run_confirmed_combat")
        method.decorator_list = []
        for no_battle, exiting in ((False, False), (True, False), (False, True)):
            with self.subTest(no_battle=no_battle, exiting=exiting):
                task = FakeTask()
                task.get_battle_config = lambda key, default=None: True if key == KEY_REACTIVE_DODGE else default
                task._recommend_detector_in_combat = True
                task.debug = False
                task.mouse_up = Mock()
                task.log_info = Mock()
                task.active_and_send_mouse_delta = Mock()
                task.click = Mock()
                task.approach_enemy = Mock()
                task.next_frame = lambda task=task: setattr(task, "now", task.now + 0.1)
                task._check_single_exit_condition = lambda exiting=exiting: exiting
                task.is_combat_ended = lambda condition: False
                dodge = SimpleNamespace(step=Mock(return_value=True), close=Mock())
                namespace = dict(vars(BattleConfig))
                namespace.update(ReactiveDodge=lambda task, dodge=dodge: dodge, traceback=traceback)
                exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_normal_loop", "exec"), namespace)
                logic = SimpleNamespace(
                    task=task,
                    _periodic_search=Mock(),
                    _sync_normal_attack_hold=Mock(),
                    _last_exit_check_time=0,
                    _exit_check_interval=0.1,
                    _last_team_detect_time=0,
                    _TEAM_DETECT_INTERVAL=1,
                    _team_detect_attempts=0,
                    _TEAM_DETECT_MAX_ATTEMPTS=1,
                    _do_normal_combat_frame=Mock(),
                )
                result = namespace["_run_confirmed_combat"](logic, None, no_battle, 1.5)
                self.assertFalse(result)  # Reached deadline rather than swallowed exception.
                dodge.close.assert_called_once()
                if no_battle or exiting:
                    dodge.step.assert_not_called()
                else:
                    dodge.step.assert_called()
                    logic._do_normal_combat_frame.assert_not_called()
                    task.approach_enemy.assert_not_called()

    def test_normal_filler_reuses_battle_detector_and_obeys_exit(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/onetime/AutoCombatLogic.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AutoCombatLogic")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_exec_rotation_token")
        namespace = {"KEY_REACTIVE_DODGE": KEY_REACTIVE_DODGE}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "actual_filler_loop", "exec"), namespace)
        task = FakeTask()
        task.log_info = Mock()
        task.mouse_up = Mock()
        task.approach_enemy = Mock()
        task.next_frame = lambda: setattr(task, "now", task.now + 0.1)
        task._check_single_exit_condition = lambda: False
        dodge = SimpleNamespace(step=Mock(return_value=True))
        logic = SimpleNamespace(
            task=task,
            _reactive_dodge=dodge,
            _exit_pending=False,
            _periodic_search=Mock(),
            _sync_normal_attack_hold=Mock(),
            _last_exit_check_time=0,
            _exit_check_interval=0.1,
            _do_normal_combat_frame=Mock(),
            auto_rotation_active=True,
        )
        self.assertEqual(namespace["_exec_rotation_token"](logic, "normal_5", 1.5), (True, "return_false"))
        dodge.step.assert_called()
        logic._do_normal_combat_frame.assert_not_called()
        task.approach_enemy.assert_not_called()
        dodge.step.reset_mock()
        task.now = 2.0
        task._check_single_exit_condition = lambda: True
        self.assertEqual(namespace["_exec_rotation_token"](logic, "normal_5", 3.0), (True, "break"))
        dodge.step.assert_not_called()

    def _normal_runtime(self):
        """Run the real normal-loop methods with controlled capture and input."""
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "src/tasks/onetime/AutoCombatLogic.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AutoCombatLogic")
        methods = [
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name in ("_run_confirmed_combat", "_exec_rotation_token")
        ]
        for method in methods:
            method.decorator_list = []
        task = FakeTask()
        task.get_battle_config = lambda key, default=None: (
            task.config.get(key, default) if key in (KEY_REACTIVE_DODGE, "启用排轴", "排轴序列") else default
        )
        task._recommend_detector_in_combat = True
        task.debug = False
        task.mouse_up = Mock()
        task.log_info = Mock()
        task.active_and_send_mouse_delta = Mock()
        task.click = Mock()
        task.approach_enemy = Mock()
        task.next_frame = lambda: setattr(task, "now", task.now + 0.1)
        task._check_single_exit_condition = Mock(return_value=False)
        task.is_combat_ended = Mock(return_value=False)
        task._parse_skill_sequence = lambda text: text.split(",")
        dodge = SimpleNamespace(step=Mock(return_value=False), close=Mock())
        namespace = dict(vars(BattleConfig))
        namespace.update(ReactiveDodge=lambda task: dodge, traceback=traceback)
        exec(compile(ast.Module(body=methods, type_ignores=[]), "normal_runtime", "exec"), namespace)
        logic = SimpleNamespace(
            task=task,
            _periodic_search=Mock(),
            _sync_normal_attack_hold=Mock(),
            _last_exit_check_time=0,
            _exit_check_interval=0.1,
            _last_team_detect_time=0,
            _TEAM_DETECT_INTERVAL=1,
            _team_detect_attempts=0,
            _TEAM_DETECT_MAX_ATTEMPTS=1,
            _do_normal_combat_frame=Mock(),
        )
        logic._exec_rotation_token = lambda token, deadline: namespace["_exec_rotation_token"](logic, token, deadline)
        return namespace, logic, task, dodge

    def test_normal_and_filler_capture_deadlines_block_later_actions(self):
        for filler in (False, True):
            for expired_capture in (1, 2):
                with self.subTest(filler=filler, expired_capture=expired_capture):
                    namespace, logic, task, dodge = self._normal_runtime()
                    captures = []

                    def capture(task=task, captures=captures, expired_capture=expired_capture):
                        captures.append(task.now)
                        task.now += 1.1 if len(captures) == expired_capture else 0.1

                    task.next_frame = capture
                    if filler:
                        logic._reactive_dodge = dodge
                        logic._exit_pending = False
                        logic.auto_rotation_active = False
                        self.assertEqual(logic._exec_rotation_token("normal_5", 2.0), (True, "return_false"))
                    else:
                        self.assertFalse(namespace["_run_confirmed_combat"](logic, None, False, 2.0))
                    self.assertEqual(len(captures), expired_capture)
                    self.assertEqual(task.approach_enemy.call_count, expired_capture - 1)
                    self.assertEqual(dodge.step.call_count, expired_capture - 1)
                    logic._do_normal_combat_frame.assert_not_called()

    def test_pending_exit_is_shared_with_normal_filler_before_next_probe(self):
        namespace, logic, task, dodge = self._normal_runtime()
        task.config.update({"启用排轴": True, "排轴序列": "normal_5"})
        task._check_single_exit_condition.return_value = True
        logic._last_exit_check_time = -10.0
        logic._exit_check_interval = 10.0
        self.assertFalse(namespace["_run_confirmed_combat"](logic, None, False, 1.6))
        self.assertTrue(logic._exit_pending)
        task._check_single_exit_condition.assert_called_once()
        task.is_combat_ended.assert_called_once_with(True)
        dodge.step.assert_not_called()
        dodge.close.assert_called_once()

    def test_model_compilation_does_not_expire_manual_rotation(self):
        namespace, logic, task, _dodge = self._normal_runtime()
        task.config.update({"启用排轴": True, "排轴序列": "1"})
        detector = FakeDetector()
        detector.boxes = []

        def compile_model():
            task.now += 6.1
            return detector

        namespace["ReactiveDodge"] = lambda task: ReactiveDodge(task, compile_model)
        rotation_states = []

        def execute(token, deadline):
            rotation_states.append(logic.rotation_active)
            task.now = deadline
            return True, ""

        logic._exec_rotation_token = execute
        self.assertFalse(namespace["_run_confirmed_combat"](logic, None, False, 10.0))
        self.assertEqual(rotation_states, [True])
        self.assertTrue(detector.closed)
        logic._do_normal_combat_frame.assert_not_called()


if __name__ == "__main__":
    unittest.main()
