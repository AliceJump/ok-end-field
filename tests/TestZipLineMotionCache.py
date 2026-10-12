import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.tasks.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.mixin.zip_line_motion_cache import (
    build_zip_line_route_fingerprint,
    load_zip_line_segment_motion,
    record_zip_line_segment_motion,
)
from src.tasks.mixin.zip_line_yaw_cache import (
    build_zip_line_motion_route_fingerprint,
    load_arrow_turn_motion,
    load_zip_line_segment_anchor,
    record_arrow_turn_motion,
    record_zip_line_segment_anchor,
)


class TestZipLineMotionCache(unittest.TestCase):
    def test_route_fingerprint_requires_same_key_and_full_list(self):
        base = build_zip_line_route_fingerprint("通向武陵城送货点", [108, 64, 89])
        self.assertEqual(base, build_zip_line_route_fingerprint("通向武陵城送货点", [108, 64, 89]))
        self.assertNotEqual(base, build_zip_line_route_fingerprint("通向试验园区送货点", [108, 64, 89]))
        self.assertNotEqual(base, build_zip_line_route_fingerprint("通向武陵城送货点", [108, 64, 90]))
        self.assertIsNone(build_zip_line_route_fingerprint(None, [108, 64, 89]))

    def test_duplicate_distances_use_independent_segment_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.json"
            route = build_zip_line_route_fingerprint("route", [108, 64, 108])
            with patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", path):
                self.assertTrue(record_zip_line_segment_motion(route, 0, 100, 20))
                self.assertTrue(record_zip_line_segment_motion(route, 2, -70, 40))
                self.assertEqual(load_zip_line_segment_motion(route, 0), (100, 20, 1))
                self.assertEqual(load_zip_line_segment_motion(route, 2), (-70, 40, 1))
                self.assertIsNone(load_zip_line_segment_motion(route, 1))

    def test_changed_key_or_list_cannot_load_other_route(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.json"
            route = build_zip_line_route_fingerprint("A", [108, 64])
            other_key = build_zip_line_route_fingerprint("B", [108, 64])
            other_list = build_zip_line_route_fingerprint("A", [108, 65])
            with patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", path):
                self.assertTrue(record_zip_line_segment_motion(route, 0, 120, -30))
                self.assertIsNone(load_zip_line_segment_motion(other_key, 0))
                self.assertIsNone(load_zip_line_segment_motion(other_list, 0))

    def test_corrupt_infinite_sample_count_fails_soft(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "motion.json"
            route = build_zip_line_route_fingerprint("route", [64])
            path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "routes": {
                            route: {
                                "segments": {
                                    "0": {"samples": float("inf"), "mean_dx": 300, "mean_dy": 120}
                                }
                            }
                        },
                    },
                    allow_nan=True,
                ),
                encoding="utf-8",
            )
            with patch("src.tasks.mixin.zip_line_motion_cache._DEFAULT_CACHE_PATH", path):
                hint = load_zip_line_segment_motion(route, 0)

        self.assertIsNone(hint)

    def test_alignment_applies_initial_move_once_and_reports_total_motion(self):
        target = SimpleNamespace(x=100, y=100, width=20, height=20)

        def fake_align(zip_line, **kwargs):
            kwargs["ocr_target_mover"](target)
            return (960, 540)

        stub = SimpleNamespace(
            width=1920,
            height=1080,
            _ZIP_LINE_TEMPLATE_MISS_LIMIT=3,
            _align_zip_line_distance_ocr=Mock(side_effect=fake_align),
            _move_to_zip_line_target=Mock(return_value=(40, 20)),
            _zip_line_distance_pattern=Mock(return_value="pattern"),
            make_hsv_isolator=Mock(side_effect=lambda value: value),
            _find_zip_line_template_target=Mock(return_value=(None, None)),
            active_and_send_mouse_delta=Mock(return_value=True),
            log_info=Mock(),
            sleep=Mock(),
        )
        learned = Mock()

        result = ZipLineMixin._align_zip_line_distance(
            stub,
            108,
            max_time=2,
            initial_move=(160, 80),
            on_success_motion=learned,
        )

        self.assertEqual(result, (960, 540))
        stub.active_and_send_mouse_delta.assert_called_once_with(dx=160, dy=80, activate=True, steps=5)
        stub._move_to_zip_line_target.assert_called_once_with(target)
        learned.assert_called_once_with(200, 100)

    def test_fallback_result_does_not_record_incomplete_motion_history(self):
        fallback_target = SimpleNamespace(x=100, y=100, width=20, height=20)

        def fake_align(zip_line, **kwargs):
            detector = kwargs["ocr_target_detector"]
            self.assertIsNone(detector("frame-1"))
            self.assertIsNone(detector("frame-2"))
            detected = detector("frame-3")
            self.assertTrue(detected)
            kwargs["ocr_target_mover"](detected)
            return (960, 540)

        stub = SimpleNamespace(
            width=1920,
            height=1080,
            _ZIP_LINE_TEMPLATE_MISS_LIMIT=3,
            _align_zip_line_distance_ocr=Mock(side_effect=fake_align),
            _move_to_zip_line_target=Mock(return_value=(80, 0)),
            _zip_line_distance_pattern=Mock(return_value="pattern"),
            make_hsv_isolator=Mock(side_effect=lambda value: value),
            _find_zip_line_template_target=Mock(return_value=None),
            ocr=Mock(return_value=[fallback_target]),
            active_time=Mock(return_value=0),
            active_and_send_mouse_delta=Mock(return_value=True),
            log_info=Mock(),
            sleep=Mock(),
        )
        learned = Mock()

        result = ZipLineMixin._align_zip_line_distance(
            stub,
            108,
            max_time=2,
            initial_move=(100, 0),
            on_success_motion=learned,
        )

        self.assertEqual(result, (960, 540))
        learned.assert_not_called()

    def test_route_fingerprint_falls_back_to_pending_matching_config_read(self):
        route_values = [108, 64]
        stub = SimpleNamespace(_pending_zip_line_motion_route=("route-key", tuple(route_values)))
        fingerprint = ZipLineMixin._zip_line_motion_route_fingerprint(stub, route_values)
        self.assertEqual(fingerprint, build_zip_line_route_fingerprint("route-key", route_values))
        self.assertIsNone(stub._pending_zip_line_motion_route)

    def test_explicit_config_key_overrides_pending_key(self):
        route_values = [108, 64]
        stub = SimpleNamespace(_pending_zip_line_motion_route=("old-key", tuple(route_values)))
        fingerprint = ZipLineMixin._zip_line_motion_route_fingerprint(stub, route_values, config_key="new-key")
        self.assertEqual(fingerprint, build_zip_line_route_fingerprint("new-key", route_values))
        self.assertIsNone(stub._pending_zip_line_motion_route)

    def test_anchor_is_exact_per_segment_and_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yaw.json"
            route = build_zip_line_route_fingerprint("route", [104, 80, 68])
            with patch("src.tasks.mixin.zip_line_yaw_cache._DEFAULT_CACHE_PATH", path):
                self.assertTrue(record_zip_line_segment_anchor(route, 1, 1920, 1080, 123.4))
                self.assertAlmostEqual(load_zip_line_segment_anchor(route, 1, 1920, 1080), 123.4)
                self.assertIsNone(load_zip_line_segment_anchor(route, 0, 1920, 1080))
                self.assertIsNone(load_zip_line_segment_anchor(route, 1, 3840, 2160))
                self.assertTrue(record_zip_line_segment_anchor(route, 1, 1920, 1080, 200.0))
                self.assertAlmostEqual(load_zip_line_segment_anchor(route, 1, 1920, 1080), 123.4)

    def test_motion_fingerprint_includes_size_and_anchor_bucket(self):
        route = build_zip_line_route_fingerprint("route", [104, 80])
        a = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 121.1)
        b = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 121.9)
        other_size = build_zip_line_motion_route_fingerprint(route, 3840, 2160, 121.1)
        other_yaw = build_zip_line_motion_route_fingerprint(route, 1920, 1080, 130.0)
        self.assertEqual(a, b)
        self.assertNotEqual(a, other_size)
        self.assertNotEqual(a, other_yaw)

    def test_arrow_turn_cache_requires_same_size_and_yaw_buckets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "yaw.json"
            with patch("src.tasks.mixin.zip_line_yaw_cache._DEFAULT_CACHE_PATH", path):
                self.assertTrue(record_arrow_turn_motion(1920, 1080, 121.0, 90.0, 360))
                self.assertEqual(load_arrow_turn_motion(1920, 1080, 121.9, 90.9), (360, 1))
                self.assertIsNone(load_arrow_turn_motion(3840, 2160, 121.0, 90.0))
                self.assertIsNone(load_arrow_turn_motion(1920, 1080, 140.0, 90.0))

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
            _send_zip_line_yaw_delta=Mock(return_value=True),
            sleep=Mock(),
            log_info=Mock(),
        )

    def test_first_reliable_yaw_learns_anchor_without_turn(self):
        stub = self._yaw_stub([(123.0, 0.9)])
        with patch("src.tasks.mixin.zip_line_mixin.load_zip_line_segment_anchor", return_value=None), patch(
            "src.tasks.mixin.zip_line_mixin.record_zip_line_segment_anchor", return_value=True
        ) as record_anchor:
            result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, "route", 0)

        self.assertEqual(result, (123.0, True))
        record_anchor.assert_called_once_with("route", 0, 1920, 1080, 123.0)
        stub._send_zip_line_yaw_delta.assert_not_called()

    def test_existing_anchor_within_tolerance_skips_turn(self):
        stub = self._yaw_stub([(91.5, 0.9)])
        with patch("src.tasks.mixin.zip_line_mixin.load_zip_line_segment_anchor", return_value=90.0):
            result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, "route", 1)

        self.assertEqual(result, (90.0, True))
        stub._send_zip_line_yaw_delta.assert_not_called()

    def test_calibrated_turn_normalizes_and_learns_turn(self):
        stub = self._yaw_stub([(120.0, 0.9), (90.0, 0.9)])
        with patch("src.tasks.mixin.zip_line_mixin.load_zip_line_segment_anchor", return_value=90.0), patch(
            "src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None
        ), patch("src.tasks.mixin.zip_line_mixin.record_arrow_turn_motion", return_value=True) as record_turn:
            result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, "route", 1)

        self.assertEqual(result, (90.0, True))
        stub._send_zip_line_yaw_delta.assert_called_once_with(361)
        record_turn.assert_called_once_with(1920, 1080, 120.0, 90.0, 361)

    def test_non_improving_turn_rolls_back_and_disables_history(self):
        stub = self._yaw_stub([(120.0, 0.9), (120.0, 0.9)])
        with patch("src.tasks.mixin.zip_line_mixin.load_zip_line_segment_anchor", return_value=90.0), patch(
            "src.tasks.mixin.zip_line_mixin.load_arrow_turn_motion", return_value=None
        ):
            result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, "route", 1)

        self.assertEqual(result, (90.0, False))
        self.assertEqual(
            [call.args[0] for call in stub._send_zip_line_yaw_delta.call_args_list],
            [361, -361],
        )

    def test_low_score_disables_history_without_motion(self):
        stub = self._yaw_stub([(120.0, 0.4)])
        result = ZipLineMixin._normalize_zip_line_segment_yaw(stub, "route", 1)
        self.assertEqual(result, (None, False))
        stub._send_zip_line_yaw_delta.assert_not_called()


if __name__ == "__main__":
    unittest.main()
