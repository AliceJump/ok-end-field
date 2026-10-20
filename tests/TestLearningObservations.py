"""Raw acquisitions remain auditable across settings, outliers and replay projections."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.camera_input_config import read_saved_camera_settings
from src.core.learning.environment import LearningEnvironment, environment_for_context
from src.core.learning.lookup import workflow_record
from src.core.learning.models import LearningRecord, MotionStatistics
from src.core.learning.policy import choose_hint
from src.core.learning.routes import RouteLearning
from src.core.learning.samples import MouseInput
from src.core.learning.session import create_learning_session
from src.core.learning.store import LearningStore
from src.tasks.mixin.navigation_learning import initial_alignment
from src.tasks.mixin.zip_line_yaw_cache import load_arrow_turn_motion, record_arrow_turn_motion
from tests import TestInitialAlignmentLearning as alignment_fixture
from tests.TestLearningCore import workflow_context
from tests.TestRouteLearning import route_context


class TestLearningObservations(unittest.TestCase):
    def test_signed_conversion_uses_independent_width_height_and_sensitivities(self):
        original = LearningEnvironment()
        target = LearningEnvironment(speed_x=5, speed_y=-5, reverse_y=True, capture_size=(3840, 1080))
        x, y = original.convert(150, 20, target)
        self.assertAlmostEqual(x, 200)
        self.assertAlmostEqual(y, -40)
        back = target.convert(x, y, original)
        self.assertAlmostEqual(back[0], 150)
        self.assertAlmostEqual(back[1], 20)

    def test_complete_observations_not_source_averages_determine_weight(self):
        record = LearningRecord()
        for timestamp, dx in enumerate((100, 100, 100)):
            record = record.successful(dx, 20, now=timestamp)
        other = LearningEnvironment(speed_x=5, capture_size=(3840, 2160))
        record = record.successful(160, 40, now=4, environment=other)
        # The last observation is equivalent to 120 at the reference settings.
        self.assertAlmostEqual(record.motion_for(LearningEnvironment()).mean_dx, 105)
        self.assertEqual([sample.dx for sample in record.observations], [100, 100, 100, 160])
        self.assertEqual(record.observations[-1].environment, other)

    def test_outlier_is_retained_and_reclassified_when_full_history_changes(self):
        record = LearningRecord()
        for timestamp, dx in enumerate((100, 101, 99, 1000)):
            record = record.successful(dx, 20, now=timestamp)
        self.assertEqual(len(record.observations), 4)
        self.assertTrue(record.observations[-1].outlier)
        self.assertEqual((record.motion.samples, record.motion.mean_dx), (3, 100))
        for timestamp in range(4, 9):
            record = record.successful(1000, 20, now=timestamp)
        self.assertEqual((record.motion.samples, record.motion.mean_dx), (6, 1000))
        self.assertFalse(record.observations[3].outlier)
        self.assertTrue(record.observations[0].outlier)

    def test_roundtrip_preserves_seven_settings_input_trace_and_outlier(self):
        environment = LearningEnvironment(2, -1, True, False, (2560, 1440), True, 121.7, ("reverse_x",))
        inputs = (MouseInput(100, 20, 0.2, 1), MouseInput(-10, 5, 0.4, 2))
        record = LearningRecord().successful(
            90, 25, now=10, environment=environment, inputs=inputs, verification="gold_gate"
        )
        restored = LearningRecord.from_dict(json.loads(json.dumps(record.to_dict())))
        self.assertEqual(restored, record)
        self.assertEqual(restored.observations[0].environment, environment)
        self.assertEqual(restored.observations[0].inputs, inputs)

    def test_workflow_cross_resolution_replay_uses_raw_records_and_preserves_entry_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LearningStore(Path(folder) / "samples.json")
            source = workflow_context()
            record = LearningRecord()
            for now in range(3):
                record = record.successful(100, 20, now=now, environment=environment_for_context(source))
            store.put(source.fingerprint, record)
            target = replace(source, capture_profile=replace(source.capture_profile, width=3840, height=2160))
            environment = replace(environment_for_context(target), speed_x=5, reverse_y=True)
            projected = workflow_record(target, store.snapshot(), environment)
            self.assertEqual(choose_hint(projected, "high").action, (133, -40))
            self.assertIsNone(workflow_record(replace(target, entry="other"), store.snapshot(), environment))
            self.assertEqual(store.get(source.fingerprint), record)

    def test_route_private_and_prefix_sharing_convert_every_source_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LearningStore(Path(folder) / "samples.json")
            learning = RouteLearning(store)
            source = route_context()
            for now in range(3):
                learning.record_success(source, 100, 20, now=now)
            profile = replace(source.capture_profile, width=3840, height=2160)
            target = replace(source, capture_profile=profile)
            environment = replace(environment_for_context(target), reverse_x=True)
            self.assertEqual(learning.lookup(target, environment=environment).action, (-200, 40))
            shared = learning.lookup(replace(target, config_key="other"), environment=environment)
            self.assertEqual((shared.action, shared.source), ((-200, 40), "shared-prefix"))
            self.assertIsNone(learning.lookup(replace(target, segment_index=3), environment=environment))
            self.assertIsNone(
                learning.lookup(
                    replace(target, capture_profile=replace(profile, camera="other")), environment=environment
                )
            )

    def test_legacy_aggregate_cannot_invent_acquisition_settings_or_strengthen_replay(self):
        record = LearningRecord(motion=MotionStatistics(100, 100, 20), success_count=100)
        self.assertIsNone(choose_hint(record, "high", environment=LearningEnvironment()).action)
        record = record.successful(110, 22, now=1)
        self.assertEqual((record.motion.samples, record.motion.legacy_samples), (1, 100))
        self.assertEqual(len(record.observations), 1)

    def test_failed_cross_resolution_replay_blocks_until_a_new_verified_success(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LearningStore(Path(folder) / "samples.json")
            learning = RouteLearning(store)
            source = route_context()
            for now in range(3):
                learning.record_success(source, 100, 20, now=now)
            target = replace(source, capture_profile=replace(source.capture_profile, width=3840, height=2160))
            environment = environment_for_context(target)
            session = create_learning_session(target, store, started_at=0, environment=environment)
            self.assertEqual(session.take_action_prior(), (200, 40))
            self.assertTrue(session.fail(now=10))
            self.assertIsNone(learning.lookup(target, environment=environment).action)
            self.assertTrue(learning.record_success(target, 200, 40, now=11, environment=environment))
            self.assertEqual(learning.lookup(target, environment=environment).action, (200, 40))

    def test_rejected_shared_model_cannot_be_reused_at_another_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            store = LearningStore(Path(folder) / "samples.json")
            learning = RouteLearning(store)
            source = route_context()
            for now in range(3):
                learning.record_success(source, 100, 20, now=now)
            target = replace(source, config_key="new")
            learning.reject_shared(target, now=10)
            large = replace(target, capture_profile=replace(target.capture_profile, width=3840, height=2160))
            self.assertIsNone(learning.lookup(large, environment=environment_for_context(large)).action)

    def test_missing_and_invalid_registry_fields_fall_back_independently(self):
        import winreg

        with (
            patch.object(winreg, "OpenKey", return_value=MagicMock()),
            patch.object(
                winreg,
                "QueryValueEx",
                side_effect=[FileNotFoundError(), (1, winreg.REG_DWORD), (0, winreg.REG_DWORD), (3, winreg.REG_DWORD)],
            ),
        ):
            settings, fallback = read_saved_camera_settings()
        self.assertEqual(settings, {"speed_x": 0, "speed_y": 0, "reverse_x": True, "reverse_y": False})
        self.assertEqual(fallback, ("speed_x", "reverse_y"))

    def test_environment_change_during_alignment_disallows_mixed_sample(self):
        fixture = alignment_fixture.TestInitialAlignmentLearning()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        task = fixture.workflow_task([])
        first = ({"speed_x": 0, "speed_y": 0, "reverse_x": False, "reverse_y": False}, ())
        second = ({**first[0], "speed_x": 5}, ())
        with (
            patch("src.tasks.mixin.navigation_learning.read_saved_camera_settings", side_effect=[first, second]),
            initial_alignment(task, workflow_context()) as learning,
        ):
            task.active_and_send_mouse_delta(100, 20)
            learning.frame_acquired(object())
            self.assertFalse(learning.confirm_alignment((960, 540)))
        self.assertEqual(fixture.store.snapshot(), {})

    def test_yaw_cache_keeps_raw_samples_and_converts_resolution_sensitivity_and_reverse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "yaw.json"
            source = LearningEnvironment(start_yaw=120)
            target = replace(source, speed_x=5, reverse_x=True, capture_size=(3840, 2160))
            with patch("src.tasks.mixin.zip_line_yaw_cache._DEFAULT_CACHE_PATH", path):
                for dx in (300, 301, 299, 900):
                    self.assertTrue(record_arrow_turn_motion(1920, 1080, 120, 90, dx, environment=source))
                self.assertEqual(load_arrow_turn_motion(3840, 2160, 120, 90, environment=target), (-400, 3))
                self.assertIsNone(load_arrow_turn_motion(3840, 2160, 120, 90, environment=replace(target, scroll=True)))
            entry = next(iter(json.loads(path.read_text())["turns"].values()))
            self.assertEqual([sample["dx"] for sample in entry["observations"]], [300, 301, 299, 900])
            self.assertTrue(entry["observations"][-1]["outlier"])


if __name__ == "__main__":
    unittest.main()
