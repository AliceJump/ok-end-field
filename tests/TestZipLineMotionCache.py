import json
import tempfile
import unittest
from contextlib import suppress
from itertools import chain, cycle
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.mixin.zip_line_motion_cache import (
    build_zip_line_route_fingerprint,
    load_nearest_zip_line_segment_motion,
    load_zip_line_segment_motion,
    record_zip_line_segment_motion,
)
from src.tasks.mixin.zip_line_motion_tracking import track_zip_line_mouse_motion
from src.tasks.mixin.zip_line_yaw_cache import (
    build_zip_line_motion_route_fingerprint,
    load_arrow_turn_motion,
    record_arrow_turn_motion,
)


class TestZipLineMotionCache(unittest.TestCase):
    def test_route_fingerprint_requires_same_key_and_full_list(self):
        base = build_zip_line_route_fingerprint("route", [108, 64, 108])
        self.assertEqual(base, build_zip_line_route_fingerprint("route", [108, 64, 108]))
        for key, values in (("other", [108, 64, 108]), ("route", [108, 108, 64]), ("route", [108, 64])):
            self.assertNotEqual(base, build_zip_line_route_fingerprint(key, values))
        self.assertIsNone(build_zip_line_route_fingerprint(None, [108]))

    def test_duplicate_distances_use_independent_segment_indices(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
        ):
            route = build_zip_line_route_fingerprint("route", [108, 64, 108])
            self.assertTrue(record_zip_line_segment_motion(route, 0, 100, 20))
            self.assertTrue(record_zip_line_segment_motion(route, 2, -70, 40))
            self.assertEqual(load_zip_line_segment_motion(route, 0), (100, 20, 1))
            self.assertEqual(load_zip_line_segment_motion(route, 2), (-70, 40, 1))
            self.assertIsNone(load_zip_line_segment_motion(route, 1))

    def test_starting_yaws_have_independent_motion_records(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
        ):
            route = build_zip_line_route_fingerprint("route", [108])
            for yaw, motion in ((30, (100, 20)), (90, (-70, 40)), (150, (-900, 80))):
                key = build_zip_line_motion_route_fingerprint(route, 1920, 1080, yaw)
                self.assertTrue(record_zip_line_segment_motion(key, 0, *motion))
            for yaw, motion in ((30, (100, 20)), (90, (-70, 40)), (150, (-900, 80))):
                key = build_zip_line_motion_route_fingerprint(route, 1920, 1080, yaw + 1)
                self.assertEqual(load_zip_line_segment_motion(key, 0), (*motion, 1))

    def test_nearest_history_respects_route_segment_size_and_wrap(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
        ):
            route = build_zip_line_route_fingerprint("route", [108, 108])
            for yaw, segment, size, dx in (
                (90, 0, (1920, 1080), 100),
                (350, 0, (1920, 1080), 200),
                (10, 1, (1920, 1080), 300),
                (5, 0, (3840, 2160), 400),
            ):
                key = build_zip_line_motion_route_fingerprint(route, *size, yaw)
                record_zip_line_segment_motion(key, segment, dx, 20)
            self.assertEqual(load_nearest_zip_line_segment_motion(route, 0, 1920, 1080, 5), (350, 200, 20, 1))
            self.assertEqual(load_nearest_zip_line_segment_motion(route, 1, 1920, 1080, 5), (10, 300, 20, 1))
            self.assertIsNone(load_nearest_zip_line_segment_motion(route, 2, 1920, 1080, 5))
            self.assertIsNone(load_nearest_zip_line_segment_motion("other", 0, 1920, 1080, 5))
            self.assertIsNone(load_nearest_zip_line_segment_motion(route, 0, 1280, 720, 5))

    def test_corrupt_entries_and_old_schema_fail_soft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.json"
            route = build_zip_line_route_fingerprint("route", [64])
            key = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 90)
            with patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", path):
                for version, samples in ((4, float("inf")), (2, 1)):
                    path.write_text(
                        json.dumps(
                            {
                                "version": version,
                                "routes": {
                                    key: {"segments": {"0": {"samples": samples, "mean_dx": 300, "mean_dy": 120}}},
                                    "bad-json-key": {},
                                    '["route",[1920,1080],true]': {},
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    self.assertIsNone(load_zip_line_segment_motion(key, 0))
                    self.assertIsNone(load_nearest_zip_line_segment_motion(route, 0, 1920, 1080, 30))

    def test_previous_calibrated_history_remains_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.json"
            route = build_zip_line_route_fingerprint("route", [64])
            key = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 90)
            path.write_text(
                json.dumps(
                    {"version": 3, "routes": {key: {"segments": {"0": {"samples": 1, "mean_dx": 100, "mean_dy": 20}}}}}
                ),
                encoding="utf-8",
            )
            with patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", path):
                self.assertEqual(load_nearest_zip_line_segment_motion(route, 0, 1920, 1080, 30), (90, 100, 20, 1))
                self.assertTrue(record_zip_line_segment_motion(key, 0, 120, 40))
                self.assertEqual(load_zip_line_segment_motion(key, 0), (110, 30, 2))
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 4)

    def test_fingerprint_isolates_capture_size_and_normalizes_wrap(self):
        route = build_zip_line_route_fingerprint("route", [104, 80])
        self.assertEqual(
            build_zip_line_motion_route_fingerprint(route, 1920, 1080, 121.1),
            build_zip_line_motion_route_fingerprint(route, 1920, 1080, 121.9),
        )
        self.assertEqual(
            build_zip_line_motion_route_fingerprint(route, 1920, 1080, 359.0),
            build_zip_line_motion_route_fingerprint(route, 1920, 1080, 1.0),
        )
        self.assertNotEqual(
            build_zip_line_motion_route_fingerprint(route, 1920, 1080, 121.1),
            build_zip_line_motion_route_fingerprint(route, 3840, 2160, 121.1),
        )

    def test_route_fingerprint_consumes_matching_pending_config_or_explicit_key(self):
        values = [108, 64]
        for explicit, expected in ((None, "old-key"), ("new-key", "new-key")):
            stub = SimpleNamespace(_pending_zip_line_motion_route=("old-key", tuple(values)))
            self.assertEqual(
                ZipLineMixin._zip_line_motion_route_fingerprint(stub, values, config_key=explicit),
                build_zip_line_route_fingerprint(expected, values),
            )
            self.assertIsNone(stub._pending_zip_line_motion_route)

    def test_generic_arrow_turn_cache_still_reuses_previous_learning(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_yaw_cache._DEFAULT_CACHE_PATH", Path(directory) / "yaw.json"),
        ):
            self.assertTrue(record_arrow_turn_motion(1920, 1080, 121.0, 90.0, 360))
            self.assertEqual(load_arrow_turn_motion(1920, 1080, 121.9, 90.9), (360, 1))
            self.assertIsNone(load_arrow_turn_motion(3840, 2160, 121.0, 90.0))
            self.assertIsNone(load_arrow_turn_motion(1920, 1080, 140.0, 90.0))

    def test_tracker_counts_successful_raw_input_and_restores_override(self):
        sender = Mock(side_effect=[True, True, True, False, True])
        stub = SimpleNamespace(active_and_send_mouse_delta=sender)
        with track_zip_line_mouse_motion(stub) as total:
            stub.active_and_send_mouse_delta(dx=361, dy=0)
            stub.active_and_send_mouse_delta(-361, 0)
            stub.active_and_send_mouse_delta(160, 80, steps=5)
            stub.active_and_send_mouse_delta(1000, 1000)
            stub.active_and_send_mouse_delta(500, 500, only_activate=True)
        self.assertEqual((total.dx, total.dy, total.complete), (160, 80, True))
        self.assertIs(stub.active_and_send_mouse_delta, sender)

    def test_tracker_restores_class_method_after_exception_and_marks_partial_input(self):
        class Stub:
            def active_and_send_mouse_delta(self, *args, **kwargs):
                raise OSError("possibly partial input")

        stub = Stub()
        with self.assertRaises(OSError), track_zip_line_mouse_motion(stub) as total:
            stub.active_and_send_mouse_delta(100, 20)
        self.assertFalse(total.complete)
        self.assertNotIn("active_and_send_mouse_delta", vars(stub))

    @staticmethod
    def _yaw_stub(readings):
        return SimpleNamespace(
            width=1920,
            height=1080,
            _ZIP_LINE_ARROW_MIN_SCORE=0.60,
            _ZIP_LINE_ARROW_TOLERANCE=3.0,
            _ZIP_LINE_ARROW_MAX_STEP_DEGREES=30.0,
            _ZIP_LINE_ARROW_MAX_STEPS=6,
            _ZIP_LINE_ARROW_YAW_PER_MOUSE=-0.083,
            _ZIP_LINE_ARROW_SETTLE_DELAY=0.08,
            _zip_line_angle_delta=ZipLineMixin._zip_line_angle_delta,
            _read_zip_line_arrow_angle=Mock(side_effect=readings),
            _read_zip_line_settled_arrow_angle=Mock(side_effect=readings),
            _send_zip_line_yaw_delta=Mock(return_value=True),
            sleep=Mock(),
            log_info=Mock(),
        )

    def test_existing_matching_yaw_does_not_turn(self):
        stub = self._yaw_stub([(91.5, 0.9)])
        self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (91.5, True))
        stub._send_zip_line_yaw_delta.assert_not_called()

    def test_calibrated_turn_normalizes_and_learns_turn(self):
        stub = self._yaw_stub([(120.0, 0.9), (90.0, 0.9)])
        with (
            patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None),
            patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", return_value=True) as record,
        ):
            result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90)
        self.assertEqual(result, (90.0, True))
        stub._send_zip_line_yaw_delta.assert_called_once_with(361)
        record.assert_called_once_with(1920, 1080, 120.0, 90.0, 361)

    def test_cached_turn_updates_memory_with_its_final_correction(self):
        stub = self._yaw_stub([(120.0, 0.9), (100.0, 0.9), (90.0, 0.9)])
        with patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=(240, 3)), patch(
            "src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", return_value=True
        ) as record:
            self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (90.0, True))
        self.assertEqual([call.args[0] for call in stub._send_zip_line_yaw_delta.call_args_list], [240, 120])
        record.assert_called_once_with(1920, 1080, 120.0, 90.0, 360)

    def test_failed_cached_turn_cannot_contaminate_turn_learning(self):
        stub = self._yaw_stub([(120.0, 0.9)])
        stub._send_zip_line_yaw_delta.return_value = False
        with patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=(240, 3)), patch(
            "src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion"
        ) as record:
            self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (90.0, False))
        stub._send_zip_line_yaw_delta.assert_called_once_with(240)
        record.assert_not_called()

    def test_invalid_reading_or_no_improvement_rolls_back_cached_and_calibrated_turn(self):
        for hint in (None, (350, 3)):
            for reading in (None, (90.0, 0.4), (120.0, 0.9)):
                with self.subTest(hint=hint, reading=reading):
                    stub = self._yaw_stub([(120.0, 0.9), reading])
                    with (
                        patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=hint),
                        patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion") as record,
                    ):
                        result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90)
                    self.assertEqual(result, (90.0, False))
                    moves = [call.args[0] for call in stub._send_zip_line_yaw_delta.call_args_list]
                    self.assertEqual(len(moves), 2)
                    self.assertEqual(sum(moves), 0)
                    record.assert_not_called()

    def test_multi_step_failure_rolls_back_all_input(self):
        stub = self._yaw_stub([(150.0, 0.9), (120.0, 0.9), None])
        with patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None):
            self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (90.0, False))
        self.assertEqual([call.args[0] for call in stub._send_zip_line_yaw_delta.call_args_list], [361, 361, -722])

    def test_low_confidence_does_not_turn(self):
        stub = self._yaw_stub([(120.0, 0.4)])
        self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (None, False))
        stub._send_zip_line_yaw_delta.assert_not_called()

    def _settling_stub(self, readings):
        stub = self._yaw_stub(readings)
        now = [0.0]
        stub.active_time = lambda: now[0]
        stub.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
        stub._ZIP_LINE_ARROW_STABLE_SAMPLES = 3
        stub._ZIP_LINE_ARROW_STABLE_TOLERANCE = 0.5
        stub._ZIP_LINE_ARROW_SETTLE_TIMEOUT = 1.5
        stub._read_zip_line_settled_arrow_angle = lambda: ZipLineMixin._read_zip_line_settled_arrow_angle(stub)
        return stub

    def test_cached_turn_waits_until_character_has_followed_camera(self):
        readings = [(120.0, 0.9)] * 3 + [(110.0, 0.9), (100.0, 0.9)] + [(90.0, 0.9)] * 3
        stub = self._settling_stub(readings)
        with (
            patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=(361, 3)),
            patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", return_value=True),
        ):
            self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (90.0, True))
        stub._send_zip_line_yaw_delta.assert_called_once_with(361)
        self.assertEqual(stub._read_zip_line_arrow_angle.call_count, len(readings))

    def test_unsettled_arrow_times_out_and_rolls_back(self):
        stub = self._settling_stub(chain([(120.0, 0.9)] * 3, cycle([(90.0, 0.9), (95.0, 0.9)])))
        with patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=(361, 3)):
            self.assertEqual(ZipLineMixin._normalize_zip_line_segment_yaw(stub, 90), (90.0, False))
        self.assertEqual([call.args[0] for call in stub._send_zip_line_yaw_delta.call_args_list], [361, -361])

    def test_settling_rejects_low_confidence_and_handles_wrap(self):
        stub = self._settling_stub([(90.0, 0.9), (90.0, 0.4)])
        self.assertIsNone(ZipLineMixin._read_zip_line_settled_arrow_angle(stub))
        stub = self._settling_stub([(359.8, 0.9), (0.0, 0.9), (0.2, 0.9)])
        self.assertEqual(ZipLineMixin._read_zip_line_settled_arrow_angle(stub), (0.2, 0.9))

    def _route_stub(self, readings):
        stub = self._yaw_stub(readings)
        stub.lang = SimpleNamespace(zip_line_mixin=SimpleNamespace(k_2f4f4a2f="move", k_0b1e4f35="leave"))
        stub.active_time = Mock(return_value=0)
        stub.next_frame = Mock()
        stub.send_key = Mock()
        stub.ocr = Mock(return_value=[object()])
        stub.box_of_screen = Mock()
        stub.active_and_send_mouse_delta = Mock(return_value=True)
        stub._send_zip_line_yaw_delta = lambda dx: ZipLineMixin._send_zip_line_yaw_delta(stub, dx)
        stub._zip_line_motion_route_fingerprint = lambda *a, **k: ZipLineMixin._zip_line_motion_route_fingerprint(
            stub, *a, **k
        )
        stub._normalize_zip_line_segment_yaw = Mock(
            side_effect=lambda *a, **k: ZipLineMixin._normalize_zip_line_segment_yaw(stub, *a, **k)
        )

        def align(_distance, **kwargs):
            if kwargs.get("initial_move") is not None:
                stub.active_and_send_mouse_delta(*kwargs["initial_move"])
            stub.active_and_send_mouse_delta(40, 20)
            return True

        stub._align_zip_line_distance = Mock(side_effect=align)
        stub._try_click_on_zip_line = Mock(return_value=(True, None))
        stub._align_and_activate_zip_line = lambda *a, **k: ZipLineMixin._align_and_activate_zip_line(stub, *a, **k)
        return stub

    def test_borrow_nearest_history_then_learn_original_yaw_and_hit_it_next_time(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
            patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None),
            patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", return_value=True),
        ):
            route = build_zip_line_route_fingerprint("route", [108])
            key90 = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 90)
            key30 = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 30)
            record_zip_line_segment_motion(key90, 0, -400, 100)
            stub = self._route_stub([(30.0, 0.9), (60.0, 0.9), (90.0, 0.9), (30.0, 0.9)])
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            self.assertEqual(load_zip_line_segment_motion(key30, 0), (-1082, 120, 1))
            self.assertEqual(load_zip_line_segment_motion(key90, 0), (-400, 100, 1))
            stub._normalize_zip_line_segment_yaw.assert_called_once_with(90.0, initial_reading=(30.0, 0.9))
            stub._normalize_zip_line_segment_yaw.reset_mock()
            stub._align_zip_line_distance.reset_mock()
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            stub._normalize_zip_line_segment_yaw.assert_not_called()
            self.assertEqual(stub._align_zip_line_distance.call_args.kwargs["initial_move"], (-1082, 120))
            self.assertEqual(load_zip_line_segment_motion(key30, 0), (-1062, 130, 2))

    def test_empty_history_uses_visual_alignment_and_gate_retries_are_in_the_total(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
        ):
            stub = self._route_stub([(150.0, 0.9)])
            stub._try_click_on_zip_line.side_effect = [(False, "gate"), (True, None)]
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            key = build_zip_line_motion_route_fingerprint(
                build_zip_line_route_fingerprint("route", [108]), 1920, 1080, 150
            )
            self.assertEqual(load_zip_line_segment_motion(key, 0), (80, 40, 1))
            stub._normalize_zip_line_segment_yaw.assert_not_called()
            self.assertIsNone(stub._align_zip_line_distance.call_args_list[0].kwargs["initial_move"])
            self.assertNotIn("initial_move", stub._align_zip_line_distance.call_args_list[1].kwargs)

    def test_failed_nearest_yaw_verification_uses_visual_and_learns_net_motion(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
            patch("src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None),
        ):
            route = build_zip_line_route_fingerprint("route", [108])
            record_zip_line_segment_motion(build_zip_line_motion_route_fingerprint(route, 1920, 1080, 90), 0, -400, 100)
            stub = self._route_stub([(30.0, 0.9), (30.0, 0.9)])
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            self.assertIsNone(stub._align_zip_line_distance.call_args.kwargs["initial_move"])
            key30 = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 30)
            self.assertEqual(load_zip_line_segment_motion(key30, 0), (40, 20, 1))

    def test_unreliable_initial_yaw_and_failed_segment_do_not_learn(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
            patch("src.tasks.mixin.zip_line_mixin.record_zip_line_segment_motion") as record,
        ):
            stub = self._route_stub([(30.0, 0.4)])
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            record.assert_not_called()
            stub = self._route_stub([(30.0, 0.9)])
            stub._align_zip_line_distance.side_effect = RuntimeError("alignment failed")
            with self.assertRaises(RuntimeError):
                ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            record.assert_not_called()

    def test_nearest_replay_uses_confirmed_yaw_not_requested_yaw(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", Path(directory) / "motion.json"),
        ):
            route = build_zip_line_route_fingerprint("route", [108])
            record_zip_line_segment_motion(build_zip_line_motion_route_fingerprint(route, 1920, 1080, 90), 0, -400, 100)
            stub = self._route_stub([(30.0, 0.9)])
            stub._normalize_zip_line_segment_yaw = Mock(return_value=(95.0, True))
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
            self.assertIsNone(stub._align_zip_line_distance.call_args.kwargs["initial_move"])

    def test_partial_input_exception_prevents_learning_even_if_visual_recovery_succeeds(self):
        stub = self._route_stub([(30.0, 0.9)])
        stub.active_and_send_mouse_delta.side_effect = [OSError("partial input"), True]

        def recovered_alignment(*args, **kwargs):
            with suppress(OSError):
                stub.active_and_send_mouse_delta(160, 80)
            stub.active_and_send_mouse_delta(40, 20)

        stub._align_and_activate_zip_line = recovered_alignment
        with (
            patch("src.tasks.mixin.zip_line_mixin.load_zip_line_segment_motion", return_value=(160, 80, 1)),
            patch("src.tasks.mixin.zip_line_mixin.record_zip_line_segment_motion") as record,
        ):
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
