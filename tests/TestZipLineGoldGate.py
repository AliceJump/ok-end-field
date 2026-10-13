import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import cv2
import numpy as np

from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.zip_line_motion_tracking import track_zip_line_mouse_motion
from src.tasks.navigation.mixin.navigation_mixin import NavigationMixin
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineMixin
from src.tasks.test.zip_line_scan_recording import (
    ZipLineFrameDetector,
    ZipLineScanRecorder,
    draw_scan_candidates,
    read_scan_image,
    replay_image_paths,
)
from src.tasks.test.ZipLineReplayDebugTask import ZipLineReplayDebugTask
from src.tasks.test.ZipLineScanDebugTask import ZipLineScanDebugTask


class TestZipLineGoldGate(unittest.TestCase):
    @staticmethod
    def _stub():
        lang = SimpleNamespace(zip_line_mixin=SimpleNamespace(k_2f4f4a2f="move", k_0b1e4f35="leave"))
        stub = SimpleNamespace(
            lang=lang,
            box_of_screen=lambda *_args: "stop_box",
            next_frame=lambda: object(),
            active_time=Mock(return_value=0.0),
            sleep=Mock(),
            click=Mock(),
            send_key=Mock(),
            ocr=Mock(),
            active_and_send_mouse_delta=Mock(return_value=True),
        )
        stub._zip_line_stop_state = lambda: ZipLineMixin._zip_line_stop_state(stub)
        stub._zip_line_motion_route_fingerprint = lambda *args, **kwargs: (
            ZipLineMixin._zip_line_motion_route_fingerprint(stub, *args, **kwargs)
        )
        stub._align_and_activate_zip_line = lambda *args, **kwargs: ZipLineMixin._align_and_activate_zip_line(
            stub, *args, **kwargs
        )
        stub._try_click_on_zip_line = lambda *args, **kwargs: ZipLineMixin._try_click_on_zip_line(stub, *args, **kwargs)
        # 合并后 zip_line_list_go 还要选路：网格导航那套参数与实际调用方的辅助方法
        stub.zip_line_target_select_mode = lambda: "直接对准"
        stub._normalize_values = ZipLineMixin._normalize_values
        stub._value_for_index = ZipLineMixin._value_for_index
        stub._ride_zip_line_by_ocr = lambda *args, **kwargs: ZipLineMixin._ride_zip_line_by_ocr(stub, *args, **kwargs)
        return stub

    @staticmethod
    def _detector_stub():
        stub = SimpleNamespace(
            width=1920,
            height=1080,
            _ZIP_LINE_TEMPLATE_THRESHOLD=ZipLineMixin._ZIP_LINE_TEMPLATE_THRESHOLD,
            _ZIP_LINE_ELLIPSE_RX=ZipLineMixin._ZIP_LINE_ELLIPSE_RX,
            _ZIP_LINE_ELLIPSE_RY=ZipLineMixin._ZIP_LINE_ELLIPSE_RY,
        )
        stub.resolution_scale = Mock(return_value=1.0)
        stub.screen_center = Mock(return_value=(960, 540))
        stub._zip_line_icon_template = lambda: ZipLineMixin._zip_line_icon_template(stub)
        stub._zip_line_ellipse_geometry = lambda: ZipLineMixin._zip_line_ellipse_geometry(stub)
        stub._zip_line_ellipse_radius = lambda x, y: ZipLineMixin._zip_line_ellipse_radius(stub, x, y)
        stub._zip_line_has_gold_ring = lambda mask, x, y: ZipLineMixin._zip_line_has_gold_ring(stub, mask, x, y)
        stub._zip_line_hsv_mask = ZipLineMixin._zip_line_hsv_mask
        return stub

    @staticmethod
    def _hsv_bgr(hue, saturation=255, value=255):
        pixel = np.asarray([[[hue, saturation, value]]], dtype=np.uint8)
        return cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0]

    def test_distance_pattern_rejects_longer_numeric_distance(self):
        pattern = ZipLineMixin._zip_line_distance_pattern(108)

        self.assertIsNotNone(pattern.search("108m"))
        self.assertIsNone(pattern.search("1080m"))
        self.assertIsNone(pattern.search("2108m"))

    def test_ocr_fallback_preserves_original_processors(self):
        gold_processor = object()
        white_processor = object()
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            make_hsv_isolator=Mock(side_effect=[gold_processor, white_processor]),
            align_ocr_or_find_target_to_center=Mock(return_value=True),
            _move_to_zip_line_target=Mock(),
        )

        result = ZipLineMixin._align_zip_line_distance_ocr(stub, 108, need_scroll=True)

        self.assertTrue(result)
        stub.make_hsv_isolator.assert_has_calls([call(hR.GOLD_TEXT), call(hR.WHITE)])
        kwargs = stub.align_ocr_or_find_target_to_center.call_args.kwargs
        self.assertEqual(kwargs["ocr_frame_processor_list"], [gold_processor, white_processor])
        self.assertTrue(kwargs["is_num"])
        self.assertTrue(kwargs["need_scroll"])
        self.assertEqual(kwargs["tolerance"], 50)
        self.assertTrue(kwargs["raise_if_fail"])

    def test_ocr_fallback_checks_shared_deadline_between_chunks(self):
        gold_processor = object()
        white_processor = object()
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            make_hsv_isolator=Mock(side_effect=[gold_processor, white_processor]),
            align_ocr_or_find_target_to_center=Mock(return_value=False),
            _move_to_zip_line_target=Mock(),
            active_time=Mock(side_effect=[0.0, 20.0]),
            _zip_line_alignment_failed=lambda raise_if_fail: ZipLineMixin._zip_line_alignment_failed(raise_if_fail),
        )

        result = ZipLineMixin._align_zip_line_distance_ocr(
            stub,
            108,
            max_time=10,
            raise_if_fail=False,
            deadline=20.0,
            max_iterations=17,
        )

        self.assertFalse(result)
        stub.align_ocr_or_find_target_to_center.assert_called_once()
        self.assertEqual(stub.align_ocr_or_find_target_to_center.call_args.kwargs["max_time"], 1)
        self.assertFalse(stub.align_ocr_or_find_target_to_center.call_args.kwargs["raise_if_fail"])

    def test_icon_detector_ignores_blue_and_red_and_prefers_white_before_yellow(self):
        stub = self._detector_stub()
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        template = ZipLineMixin._zip_line_icon_template(stub)
        mask = template > 0

        def paste_icon(x, y, color):
            roi = frame[y : y + template.shape[0], x : x + template.shape[1]]
            roi[mask] = color

        paste_icon(780, 500, (255, 255, 255))
        paste_icon(1080, 500, self._hsv_bgr(30))
        paste_icon(700, 620, self._hsv_bgr(110))
        paste_icon(1180, 620, self._hsv_bgr(0))

        candidates = ZipLineMixin._detect_zip_line_icon_candidates(stub, frame)

        self.assertEqual([candidate["state"] for candidate in candidates], ["white", "gold"])
        self.assertTrue(all(candidate["radius"] <= 1.0 for candidate in candidates))

    def test_ellipse_radius_uses_screen_center_and_expected_edge(self):
        stub = self._detector_stub()

        self.assertAlmostEqual(ZipLineMixin._zip_line_ellipse_radius(stub, 960, 540), 0.0)
        self.assertAlmostEqual(ZipLineMixin._zip_line_ellipse_radius(stub, 1680, 540), 1.0)
        self.assertGreater(ZipLineMixin._zip_line_ellipse_radius(stub, 1681, 540), 1.0)

    def test_template_target_checks_white_before_gold(self):
        white = {"state": "white"}
        gold = {"state": "gold"}
        target = SimpleNamespace(x=0, y=0, width=10, height=10)
        white_processor = object()
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            _detect_zip_line_icon_candidates=Mock(return_value=[white, gold]),
            _zip_line_distance_box=Mock(side_effect=lambda candidate: candidate["state"]),
            make_hsv_isolator=Mock(return_value=white_processor),
            ocr=Mock(return_value=[target]),
        )

        candidate, matched_target = ZipLineMixin._find_zip_line_template_target(stub, 108, frame="frame")

        self.assertIs(candidate, white)
        self.assertIs(matched_target, target)
        self.assertEqual(stub.ocr.call_args.kwargs["box"], "white")
        stub.make_hsv_isolator.assert_called_once_with(hR.WHITE)

    def test_outer_half_uses_faster_centering_profile(self):
        stub = SimpleNamespace(
            _ZIP_LINE_INNER_RADIUS=ZipLineMixin._ZIP_LINE_INNER_RADIUS,
            _ZIP_LINE_OUTER_MAX_BOOST=ZipLineMixin._ZIP_LINE_OUTER_MAX_BOOST,
        )

        inner = ZipLineMixin._zip_line_move_profile(stub, 0.5)
        outer = ZipLineMixin._zip_line_move_profile(stub, 1.0)

        self.assertEqual(inner["max_step"], 120)
        self.assertEqual(inner["min_step"], 20)
        self.assertEqual(outer["max_step"], 210)
        self.assertEqual(outer["min_step"], 35)
        self.assertEqual(outer["slow_radius"], inner["slow_radius"])

    @staticmethod
    def _alignment_stub():
        now = [0.0]
        stub = SimpleNamespace(
            width=1920,
            height=1080,
            _ZIP_LINE_TEMPLATE_MISS_LIMIT=3,
            scale_distance=lambda value: value,
            screen_center=lambda: (960, 540),
            box_of_screen=lambda *_args: None,
            active_time=lambda: now[0],
            next_frame=Mock(return_value="frame"),
            _find_zip_line_template_target=Mock(return_value=None),
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            make_hsv_isolator=Mock(side_effect=lambda value: value),
            ocr=Mock(return_value=[]),
            log_info=Mock(),
            do_scroll=Mock(),
            _move_to_zip_line_target=Mock(return_value=(40, 20)),
            move_to_target_once=Mock(),
            active_and_send_mouse_delta=Mock(),
            _read_zip_line_arrow_angle=Mock(return_value=(30.0, 0.9)),
            _wait_zip_line_prefeed_settled=Mock(return_value=True),
        )
        stub.sleep = lambda duration: now.__setitem__(0, now[0] + duration)
        stub.align_ocr_or_find_target_to_center = lambda *args, **kwargs: (
            NavigationMixin.align_ocr_or_find_target_to_center(stub, *args, **kwargs)
        )
        stub._zip_line_alignment_failed = ZipLineMixin._zip_line_alignment_failed
        stub._align_zip_line_distance_ocr = lambda *args, **kwargs: ZipLineMixin._align_zip_line_distance_ocr(
            stub, *args, **kwargs
        )
        return stub

    @staticmethod
    def _matched_icon(x=960, y=540):
        return (
            {"x": x - 10, "y": y - 10, "width": 20, "height": 20},
            SimpleNamespace(x=x - 10, y=y - 10 + int(1080 * (39 / 1080)), width=20, height=20),
        )

    def test_template_alignment_falls_back_after_three_consecutive_misses(self):
        stub = self._alignment_stub()
        target = self._matched_icon()[1]
        stub.ocr.return_value = [target]
        result = ZipLineMixin._align_zip_line_distance(stub, 108)
        self.assertEqual(result, (960, 540))
        self.assertEqual(stub._find_zip_line_template_target.call_count, 3)
        self.assertEqual(stub.ocr.call_count, 1)
        self.assertEqual(target.y, 568)  # Full-screen coordinate correction must not mutate OCR output.

    def test_template_recovers_after_full_screen_ocr_move(self):
        stub = self._alignment_stub()
        stub._find_zip_line_template_target.side_effect = [None, None, None, self._matched_icon()]
        stub.ocr.return_value = [self._matched_icon(1400, 800)[1]]
        result = ZipLineMixin._align_zip_line_distance(stub, 64, need_scroll=True)
        self.assertEqual(result, (960, 540))
        self.assertEqual(stub.ocr.call_count, 1)
        self.assertEqual(stub._find_zip_line_template_target.call_count, 4)
        self.assertEqual(stub.do_scroll.call_count, 4)
        stub._move_to_zip_line_target.assert_called_once()
        moved = stub._move_to_zip_line_target.call_args.args[0]
        self.assertEqual((moved.x + 10, moved.y + 10), (1400, 800))
        stub.move_to_target_once.assert_not_called()

    def test_template_hit_resets_consecutive_miss_counter(self):
        stub = self._alignment_stub()
        stub._find_zip_line_template_target.side_effect = [
            None,
            None,
            None,
            self._matched_icon(1400),
            None,
            None,
            self._matched_icon(),
        ]
        stub.ocr.return_value = [self._matched_icon(1400)[1]]
        self.assertEqual(ZipLineMixin._align_zip_line_distance(stub, 64), (960, 540))
        self.assertEqual(stub.ocr.call_count, 1)
        self.assertEqual(stub._move_to_zip_line_target.call_count, 2)

    def test_template_alignment_shares_retry_deadline_and_attempt_budget(self):
        for kwargs, count in [({"max_time": 2}, 4), ({"max_time": 100, "deadline": 0.05}, 2)]:
            with self.subTest(kwargs=kwargs):
                stub = self._alignment_stub()
                self.assertFalse(ZipLineMixin._align_zip_line_distance(stub, 108, raise_if_fail=False, **kwargs))
                self.assertEqual(stub._find_zip_line_template_target.call_count, count)

    def test_template_alignment_does_not_start_after_retry_deadline(self):
        stub = self._alignment_stub()
        self.assertFalse(ZipLineMixin._align_zip_line_distance(stub, 108, deadline=0, raise_if_fail=False))
        stub.next_frame.assert_not_called()
        stub.make_hsv_isolator.assert_not_called()

    def test_slow_fallback_cannot_move_or_report_success_after_deadline(self):
        stub = self._alignment_stub()

        def slow_ocr(**_kwargs):
            stub.sleep(1)
            return [self._matched_icon()[1]]

        stub.ocr.side_effect = slow_ocr
        self.assertFalse(ZipLineMixin._align_zip_line_distance(stub, 64, deadline=0.5, raise_if_fail=False))
        stub._move_to_zip_line_target.assert_not_called()
        self.assertEqual(stub.ocr.call_count, 1)

    def test_ocr_tracking_uses_ellipse_mover_across_misses(self):
        stub = self._alignment_stub()
        stub._find_zip_line_template_target.side_effect = [None, None, None, None, self._matched_icon()]
        stub.ocr.side_effect = [[self._matched_icon(1400, 800)[1]], [], []]
        self.assertEqual(ZipLineMixin._align_zip_line_distance(stub, 64), (960, 540))
        self.assertEqual(stub._move_to_zip_line_target.call_count, 2)
        stub.move_to_target_once.assert_not_called()
        stub.active_and_send_mouse_delta.assert_not_called()

    def test_standalone_full_screen_ocr_uses_ellipse_mover(self):
        stub = self._alignment_stub()
        stub.ocr.side_effect = [[self._matched_icon(1400, 800)[1]], [self._matched_icon()[1]]]
        self.assertEqual(ZipLineMixin._align_zip_line_distance_ocr(stub, 108), (960, 540))
        stub._move_to_zip_line_target.assert_called_once()
        stub.move_to_target_once.assert_not_called()

    def test_non_ocr_alignment_ignores_ocr_callbacks(self):
        stub = self._alignment_stub()
        results = [[self._matched_icon(1400, 800)[1]], [self._matched_icon()[1]]]
        detector, mover = Mock(), Mock()
        # Non-OCR targets are already at icon coordinates.
        for result in results:
            result[0].y -= int(1080 * (39 / 1080))
        stub.find_feature = Mock(side_effect=results)
        NavigationMixin.align_ocr_or_find_target_to_center(
            stub, "icon", ocr=False, max_time=1, ocr_target_detector=detector, ocr_target_mover=mover
        )
        detector.assert_not_called()
        mover.assert_not_called()
        stub.move_to_target_once.assert_called_once()

    def test_zipline_arrival_target_only_uses_ellipse_mover_for_ocr(self):
        for kind in ("ocr", "yolo", "feature"):
            with self.subTest(kind=kind):
                stub = self._stub()
                stub.wait_ocr = Mock(return_value=False)
                stub.ensure_main = Mock()
                stub.log_info = Mock()
                stub.align_ocr_or_find_target_to_center = Mock()
                stub._move_to_zip_line_target = Mock()
                ZipLineMixin.zip_line_list_go(stub, [], target=("destination", kind))
                kwargs = stub.align_ocr_or_find_target_to_center.call_args.kwargs
                self.assertIs(kwargs["ocr_target_mover"], stub._move_to_zip_line_target if kind == "ocr" else None)

    def test_history_runs_after_scroll_once_and_detects_a_fresh_frame(self):
        stub = self._alignment_stub()
        events = []
        stub.do_scroll.side_effect = lambda *args: events.append("scroll")
        stub.next_frame.side_effect = lambda: events.append("frame") or len(events)
        sender = stub.active_and_send_mouse_delta
        sender.side_effect = lambda **kwargs: events.append("prefeed" if kwargs["dx"] == 160 else "fine") or True
        stub._move_to_zip_line_target.side_effect = lambda target: stub.active_and_send_mouse_delta(dx=40, dy=20)
        targets = iter([self._matched_icon(1400, 800), self._matched_icon()])
        stub._find_zip_line_template_target.side_effect = (
            lambda *args, **kwargs: events.append("detect") or next(targets)
        )
        with track_zip_line_mouse_motion(stub) as total:
            result = ZipLineMixin._align_zip_line_distance(
                stub, 108, need_scroll=True, max_time=2, initial_move=(160, 80)
            )

        self.assertEqual(result, (960, 540))
        self.assertEqual(events, ["scroll", "frame", "prefeed", "frame", "detect", "fine", "scroll", "frame", "detect"])
        self.assertEqual(stub._find_zip_line_template_target.call_args_list[0].args[1], 4)
        self.assertEqual(sender.call_args_list[0], call(dx=160, dy=80, activate=True, steps=5))
        self.assertEqual((total.dx, total.dy, total.complete), (200, 100, True))

    def test_rejected_or_failed_prefeed_continues_visual_alignment(self):
        for outcome in (False, OSError("input unavailable")):
            with self.subTest(outcome=outcome):
                stub = self._alignment_stub()
                stub._find_zip_line_template_target.side_effect = [self._matched_icon(1400, 800), self._matched_icon()]
                sender = stub.active_and_send_mouse_delta
                sender.side_effect = [outcome, True]
                stub._move_to_zip_line_target.side_effect = lambda target: stub.active_and_send_mouse_delta(40, 20)
                with track_zip_line_mouse_motion(stub) as total:
                    result = ZipLineMixin._align_zip_line_distance(stub, 108, max_time=2, initial_move=(160, 80))

                self.assertEqual(result, (960, 540))
                self.assertEqual(sender.call_count, 2)
                self.assertEqual((total.dx, total.dy), (40, 20))
                self.assertEqual(total.complete, not isinstance(outcome, Exception))

    def test_full_screen_fallback_and_random_search_are_observed(self):
        stub = self._alignment_stub()
        stub._find_zip_line_template_target.side_effect = [None, None, None, self._matched_icon()]
        stub.active_and_send_mouse_delta.return_value = True
        with (
            patch("src.tasks.navigation.mixin.navigation_mixin.random.randint", return_value=25),
            track_zip_line_mouse_motion(stub) as total,
        ):
            result = ZipLineMixin._align_zip_line_distance(stub, 108, max_time=5, initial_move=(100, 20))
        self.assertEqual(result, (960, 540))
        self.assertEqual((total.dx, total.dy, total.complete), (125, 20, True))

    def test_history_is_not_reapplied_during_gate_retry(self):
        stub = self._stub()
        stub.width, stub.height = 1920, 1080
        stub._normalize_zip_line_segment_yaw = Mock(return_value=(90.0, True))
        stub._read_zip_line_settled_arrow_angle = Mock(return_value=(90.0, 0.9))
        stub._ZIP_LINE_ARROW_MIN_SCORE = 0.60
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(side_effect=[(False, "gate"), (True, None)])
        stub.log_info = Mock()
        stub.ocr.return_value = [SimpleNamespace(name="move")]
        with patch("src.tasks.navigation.mixin.zip_line_mixin.load_zip_line_segment_motion", return_value=(160, 80, 2)) as load:
            ZipLineMixin.zip_line_list_go(stub, [108], config_key="route", stay_on_zip_line=True)

        load.assert_called_once()
        calls = stub._align_zip_line_distance.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["initial_move"], (160, 80))
        self.assertNotIn("initial_move", calls[1].kwargs)
        self.assertNotIn("on_success_motion", calls[1].kwargs)

    def _motion_stub(self, width=1920, height=1080):
        stub = self._detector_stub()
        stub.width, stub.height = width, height
        stub.resolution_scale.return_value = min(width / 1920, height / 1080)
        stub.screen_center.return_value = (width // 2, height // 2)
        stub._ZIP_LINE_INNER_RADIUS = 0.5
        stub._ZIP_LINE_OUTER_MAX_BOOST = 1.75
        stub._zip_line_move_profile = lambda radius: ZipLineMixin._zip_line_move_profile(stub, radius)
        stub.active_and_send_mouse_delta = Mock()
        return stub

    @staticmethod
    def _motion_at(stub, nx, ny):
        cx, cy, rx, ry = stub._zip_line_ellipse_geometry()
        target = SimpleNamespace(x=cx + nx * rx, y=cy + ny * ry, width=0, height=0)
        return ZipLineMixin._move_to_zip_line_target(stub, target)

    def test_equal_ellipse_radii_have_equal_horizontal_and_vertical_speed(self):
        stub = self._motion_stub()
        for radius in (0.1, 0.5, 0.75, 1, 1.5):
            with self.subTest(radius=radius):
                dx, _ = self._motion_at(stub, radius, 0)
                _, dy = self._motion_at(stub, 0, radius)
                self.assertEqual(dx, dy)
        dx, dy = self._motion_at(stub, -0.6, 0.6)
        self.assertEqual(-dx, dy)
        self.assertGreater(dy, 90)

    def test_speed_increases_towards_edge_and_center_does_not_move(self):
        stub = self._motion_stub()
        self.assertEqual(self._motion_at(stub, 0, 0), (0, 0))
        stub.active_and_send_mouse_delta.assert_not_called()
        speeds = [self._motion_at(stub, 0, r)[1] for r in (0.1, 0.3, 0.5, 0.75, 1)]
        self.assertEqual(speeds, sorted(set(speeds)))
        self.assertLessEqual(speeds[-1], 210)
        # No jump at the extra acceleration threshold.
        self.assertLessEqual(abs(self._motion_at(stub, 0, 0.5001)[1] - self._motion_at(stub, 0, 0.5)[1]), 1)

    def test_rejected_visual_mouse_input_does_not_report_motion(self):
        stub = self._motion_stub()
        stub.active_and_send_mouse_delta.return_value = False
        self.assertEqual(self._motion_at(stub, 0.6, 0.6), (0, 0))
        stub.active_and_send_mouse_delta.assert_called_once()

    def test_motion_scales_with_width_at_different_resolutions(self):
        baseline = self._motion_at(self._motion_stub(), 0.6, -0.6)
        for width, height in ((1280, 720), (2560, 1440), (3840, 2160), (2560, 1080)):
            with self.subTest(resolution=(width, height)):
                actual = self._motion_at(self._motion_stub(width, height), 0.6, -0.6)
                for expected, value in zip(baseline, actual, strict=True):
                    self.assertAlmostEqual(value, expected * width / 1920, delta=1)
                self.assertEqual(actual[0], -actual[1])

    def test_gold_center_check_uses_fifty_pixel_tolerance(self):
        processor = object()
        target = SimpleNamespace(x=950, y=569, width=20, height=20)
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            make_hsv_isolator=Mock(return_value=processor),
            ocr=Mock(return_value=[target]),
            screen_center=Mock(return_value=(960, 540)),
            scale_distance=Mock(return_value=50),
            height=1080,
            next_frame=Mock(),
        )

        result = ZipLineMixin._zip_line_target_is_gold_and_centered(stub, 108, frame="frame")

        self.assertTrue(result)
        stub.make_hsv_isolator.assert_called_once_with(hR.GOLD_TEXT)
        self.assertIs(stub.ocr.call_args.kwargs["frame_processor"], processor)
        self.assertEqual(stub.ocr.call_args.kwargs["frame"], "frame")
        stub.scale_distance.assert_called_once_with(50)

    def test_white_or_unlocked_target_never_clicks(self):
        stub = self._stub()
        stub._zip_line_target_is_gold_and_centered = Mock(return_value=False)

        result = ZipLineMixin.ensure_click_on_zip_line(stub, 108, lock_timeout=0)

        self.assertFalse(result)
        stub.click.assert_not_called()
        stub.send_key.assert_not_called()
        stub.ocr.assert_not_called()

    def test_waits_across_white_frames_until_target_turns_gold(self):
        stub = self._stub()
        stub._zip_line_target_is_gold_and_centered = Mock(side_effect=[False, False, False, True])
        stub.ocr.return_value = []

        result = ZipLineMixin.ensure_click_on_zip_line(stub, 108, lock_timeout=1)

        self.assertTrue(result)
        self.assertEqual(stub._zip_line_target_is_gold_and_centered.call_count, 4)
        self.assertEqual(stub.sleep.call_count, 3)
        stub.click.assert_called_once()
        stub.send_key.assert_called_once_with("e")

    def test_gold_gate_is_checked_before_calibration_click(self):
        stub = self._stub()
        events = []
        stub._zip_line_target_is_gold_and_centered = Mock(
            side_effect=lambda *_args, **_kwargs: events.append("gold_check") or True
        )
        stub.click.side_effect = lambda **_kwargs: events.append("click")
        stub.send_key.side_effect = lambda key: events.append(key)
        stub.ocr.return_value = []

        result = ZipLineMixin.ensure_click_on_zip_line(stub, 108)

        self.assertTrue(result)
        self.assertEqual(events[:3], ["gold_check", "click", "e"])

    def test_e_has_no_second_gold_gate_after_click(self):
        stub = self._stub()
        stub._zip_line_target_is_gold_and_centered = Mock(return_value=True)
        stub.ocr.return_value = []

        result = ZipLineMixin.ensure_click_on_zip_line(stub, 108)

        self.assertTrue(result)
        stub._zip_line_target_is_gold_and_centered.assert_called_once()
        stub.click.assert_called_once()
        stub.send_key.assert_called_once_with("e")
        stub.ocr.assert_called_once()

    def test_e_retry_does_not_recheck_gold_after_click(self):
        stub = self._stub()
        stub._zip_line_target_is_gold_and_centered = Mock(return_value=True)
        stub.ocr.side_effect = [[SimpleNamespace(name="move")], []]

        result = ZipLineMixin.ensure_click_on_zip_line(stub, 108)

        self.assertTrue(result)
        stub._zip_line_target_is_gold_and_centered.assert_called_once()
        stub.click.assert_called_once()
        self.assertEqual(stub.send_key.call_count, 2)
        self.assertEqual(stub.ocr.call_count, 2)

    def test_gate_failure_realigns_with_same_fifty_pixel_tolerance(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(side_effect=[(False, "gate"), (True, None)])
        stub._legacy_click_on_zip_line = Mock(return_value=True)
        stub.log_info = Mock()
        stub.wait_ocr = Mock(return_value=False)
        stub.ensure_main = Mock()
        stub.ocr.return_value = [SimpleNamespace(name="move")]

        ZipLineMixin.zip_line_list_go(stub, [108], need_scroll=True)

        self.assertEqual(stub._align_zip_line_distance.call_count, 2)
        first_kwargs = stub._align_zip_line_distance.call_args_list[0].kwargs
        retry_kwargs = stub._align_zip_line_distance.call_args_list[1].kwargs
        self.assertEqual(first_kwargs["tolerance"], 50)
        self.assertEqual(retry_kwargs["tolerance"], 50)
        self.assertEqual(retry_kwargs["max_time"], 10)
        self.assertFalse(retry_kwargs["raise_if_fail"])
        self.assertEqual(retry_kwargs["deadline"], 20.0)
        stub._legacy_click_on_zip_line.assert_not_called()

    def test_gate_realign_failure_falls_back_without_raising_alignment_error(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(side_effect=[True, False])
        stub._try_click_on_zip_line = Mock(return_value=(False, "gate"))
        stub._legacy_click_on_zip_line = Mock(return_value=True)
        stub.log_info = Mock()
        stub.wait_ocr = Mock(return_value=False)
        stub.ensure_main = Mock()
        stub.ocr.return_value = [SimpleNamespace(name="move")]

        ZipLineMixin.zip_line_list_go(stub, [108])

        self.assertEqual(stub._align_zip_line_distance.call_count, 2)
        retry_kwargs = stub._align_zip_line_distance.call_args_list[1].kwargs
        self.assertFalse(retry_kwargs["raise_if_fail"])
        self.assertEqual(retry_kwargs["deadline"], 20.0)
        stub._legacy_click_on_zip_line.assert_called_once_with()

    def test_gate_retry_exhaustion_falls_back_to_direct_click_and_e(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(return_value=(False, "gate"))
        stub._legacy_click_on_zip_line = Mock(return_value=True)
        stub.log_info = Mock()
        stub.wait_ocr = Mock(return_value=False)
        stub.ensure_main = Mock()
        stub.ocr.return_value = [SimpleNamespace(name="move")]

        ZipLineMixin.zip_line_list_go(stub, [108])

        self.assertEqual(stub._try_click_on_zip_line.call_count, 3)
        self.assertEqual(stub._align_zip_line_distance.call_count, 3)
        for retry_call in stub._align_zip_line_distance.call_args_list[1:]:
            self.assertEqual(retry_call.kwargs["deadline"], 20.0)
        stub._legacy_click_on_zip_line.assert_called_once_with()

    def test_interaction_failure_does_not_use_unlocked_legacy_fallback(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(return_value=(False, "interaction"))
        stub._legacy_click_on_zip_line = Mock(return_value=True)
        stub.log_info = Mock()

        with self.assertRaisesRegex(RuntimeError, "点击后 E 连续未生效"):
            ZipLineMixin.zip_line_list_go(stub, [108])

        self.assertEqual(stub._try_click_on_zip_line.call_count, 3)
        stub._legacy_click_on_zip_line.assert_not_called()

    def test_interaction_failure_stays_sticky_if_later_retry_hits_gate(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(side_effect=[True, True, False])
        stub._try_click_on_zip_line = Mock(side_effect=[(False, "interaction"), (False, "gate")])
        stub._legacy_click_on_zip_line = Mock(return_value=True)
        stub.log_info = Mock()

        with self.assertRaisesRegex(RuntimeError, "点击后 E 连续未生效"):
            ZipLineMixin.zip_line_list_go(stub, [108])

        self.assertEqual(stub._try_click_on_zip_line.call_count, 2)
        stub._legacy_click_on_zip_line.assert_not_called()

    def test_legacy_fallback_repeats_click_and_e_without_gold_gate(self):
        stub = self._stub()
        stub.ocr.side_effect = [[SimpleNamespace(name="move")], []]

        result = ZipLineMixin._legacy_click_on_zip_line(stub, max_attempts=3)

        self.assertTrue(result)
        self.assertEqual(stub.click.call_count, 2)
        self.assertEqual(stub.send_key.call_count, 2)

    def test_test_route_completes_segments_without_dismount_or_target_search(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(return_value=(True, None))
        stub.ocr.return_value = [SimpleNamespace(name="move")]
        stub.log_info = Mock()
        stub.wait_ocr = Mock(return_value=True)
        stub.ensure_main = Mock()
        stub.align_ocr_or_find_target_to_center = Mock()
        stub.strafe_search = Mock()

        ZipLineMixin.zip_line_list_go(
            stub, [108, 36], target=("blue_marker", "feature"), need_v=True, stay_on_zip_line=True
        )

        self.assertEqual(stub._align_zip_line_distance.call_count, 2)
        self.assertEqual(stub._try_click_on_zip_line.call_count, 2)
        stub.click.assert_not_called()
        stub.wait_ocr.assert_not_called()
        stub.ensure_main.assert_not_called()
        stub.align_ocr_or_find_target_to_center.assert_not_called()
        stub.strafe_search.assert_not_called()

    def test_default_route_still_dismounts_at_end(self):
        stub = self._stub()
        stub._align_zip_line_distance = Mock(return_value=True)
        stub._try_click_on_zip_line = Mock(return_value=(True, None))
        stub.ocr.return_value = [SimpleNamespace(name="move")]
        stub.log_info = Mock()
        stub.wait_ocr = Mock(return_value=True)
        stub.ensure_main = Mock()

        ZipLineMixin.zip_line_list_go(stub, [108])

        stub.click.assert_called_once_with(key="right", after_sleep=2)
        stub.ensure_main.assert_called_once_with()

    def test_on_start_forwards_stay_on_zip_line_to_route(self):
        stub = self._stub()
        stub.ocr.return_value = [SimpleNamespace(name="move")]
        stub.get_zip_line_config_value = Mock(return_value="108,36")
        stub.zip_line_list_go = Mock()

        ZipLineMixin.on_zip_line_start(stub, "npc", need_v=False, stay_on_zip_line=True)

        stub.zip_line_list_go.assert_called_once_with(
            [108, 36], None, None, need_v=False, stay_on_zip_line=True, config_key="npc"
        )


class TestZipLineScanRecording(unittest.TestCase):
    @staticmethod
    def frame_with_icon(width=1920, height=1080):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        detector = ZipLineFrameDetector(frame, [])
        template = detector._zip_line_icon_template()
        th, tw = template.shape
        x, y = width // 2, height // 2
        frame[y : y + th, x : x + tw] = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)
        return frame

    def test_frame_geometry_uses_image_dimensions_without_device(self):
        frame = self.frame_with_icon(2560, 1440)
        detector = ZipLineFrameDetector(frame, [])
        candidate = detector._detect_zip_line_icon_candidates(frame)[0]
        box = detector._zip_line_distance_box(candidate)
        self.assertEqual(detector._zip_line_ellipse_geometry(), (1280, 720, 960, 540))
        self.assertLessEqual(abs(box.center()[0] - candidate["center_x"]), 2)
        self.assertGreater(box.y, candidate["center_y"])

    def test_unicode_output_preserves_every_frame_and_does_not_mutate_input(self):
        with tempfile.TemporaryDirectory() as root:
            task = SimpleNamespace(ocr=Mock(return_value=[]))
            recorder = ZipLineScanRecorder(task, Path(root) / "滑索记录", "live")
            blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
            hit = self.frame_with_icon()
            hit[-25:, 110:180] = 255
            original = hit.copy()
            self.assertEqual(recorder.record(blank, 1), [])
            candidates = recorder.record(hit, 2)
            self.assertEqual(len(candidates), 1)
            np.testing.assert_array_equal(hit, original)
            for index in (1, 2):
                folder = recorder.directory / f"{index:06d}"
                self.assertTrue((folder / "boxed.png").is_file())
                data = json.loads((folder / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(data["scan_index"], index)
                self.assertEqual(len(data["candidates"]), index - 1)
                self.assertEqual(read_scan_image(folder / "original.png").shape, hit.shape)
            self.assertEqual(task.ocr.call_count, 3)
            self.assertEqual(set(candidates[0]["ocr"]), {"pr", "white_gold", "raw"})
            saved = read_scan_image(recorder.directory / "000002" / "original.png")
            self.assertFalse(saved[-25:, 110:180].any())
            self.assertEqual(
                replay_image_paths(recorder.directory),
                [recorder.directory / "000001/original.png", recorder.directory / "000002/original.png"],
            )

    def test_ocr_failure_still_saves_evidence_and_propagates(self):
        with tempfile.TemporaryDirectory() as root:
            task = SimpleNamespace(ocr=Mock(side_effect=RuntimeError("OCR unavailable")))
            recorder = ZipLineScanRecorder(task, root, "replay")
            with self.assertRaisesRegex(RuntimeError, "OCR unavailable"):
                recorder.record(self.frame_with_icon(), 1)
            folder = recorder.directory / "000001"
            self.assertTrue((folder / "original.png").is_file())
            self.assertTrue((folder / "boxed.png").is_file())
            data = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            self.assertIn("OCR unavailable", data["error"])
            self.assertEqual(len(data["candidates"]), 1)

    def test_zero_hits_clear_previous_overlay(self):
        task = SimpleNamespace(clear_box=Mock(), draw_boxes=Mock())
        draw_scan_candidates(task, [])
        task.clear_box.assert_called_once()
        self.assertTrue(all(args.args[1] == [] for args in task.draw_boxes.call_args_list))

    def test_live_run_records_each_fresh_frame_before_manual_stop(self):
        stub = SimpleNamespace(
            config={"保存目录": "unused"},
            tr=lambda s: s,
            log_info=Mock(),
            clear_box=Mock(),
            draw_boxes=Mock(),
            next_frame=Mock(side_effect=[np.zeros((10, 10, 3)), np.ones((10, 10, 3))]),
            sleep=Mock(side_effect=[None, RuntimeError("manual stop")]),
        )
        with patch("src.tasks.test.ZipLineScanDebugTask.ZipLineScanRecorder") as recorder_class:
            recorder = recorder_class.return_value
            recorder.record.return_value = []
            with self.assertRaisesRegex(RuntimeError, "manual stop"):
                ZipLineScanDebugTask.run.__wrapped__(stub)
            self.assertEqual(stub.next_frame.call_count, 2)
            self.assertEqual([item.args[1] for item in recorder.record.call_args_list], [1, 2])
            self.assertFalse(
                np.array_equal(recorder.record.call_args_list[0].args[0], recorder.record.call_args_list[1].args[0])
            )
            self.assertGreaterEqual(stub.clear_box.call_count, 3)

    def test_replay_runs_without_capturing_game_frame(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "截图.png"
            cv2.imencode(".png", np.zeros((108, 192, 3), dtype=np.uint8))[1].tofile(path)
            stub = SimpleNamespace(
                config={"截图路径": str(path), "保存目录": str(Path(root) / "output")},
                tr=lambda s: s,
                log_info=Mock(),
                sleep=Mock(),
                clear_box=Mock(),
                next_frame=Mock(),
            )
            ZipLineReplayDebugTask.run.__wrapped__(stub)
            stub.next_frame.assert_not_called()
            stub.clear_box.assert_called_once()
            self.assertEqual(len(list((Path(root) / "output").rglob("result.json"))), 1)

    def test_task_constructors_register_offline_and_live_modes(self):
        executor = SimpleNamespace(scene=None, debug=True, pause_start=None)
        app = SimpleNamespace(tr=lambda s: s)
        with patch("src.core.BaseEfTask.Controller"), patch("src.core.BaseEfTask.get_global_config", return_value={}):
            live = ZipLineScanDebugTask(executor, app)
            replay = ZipLineReplayDebugTask(executor, app)
        self.assertTrue(replay.visible)
        self.assertFalse(replay.needs_frame)
        self.assertIn("扫描间隔(秒)", live.default_config)
        self.assertNotIn("扫描间隔(秒)", replay.default_config)
        self.assertIn("截图路径", replay.default_config)


if __name__ == "__main__":
    unittest.main()
