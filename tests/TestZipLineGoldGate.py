import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

import cv2
import numpy as np

from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


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
        )
        stub._zip_line_stop_state = lambda: ZipLineMixin._zip_line_stop_state(stub)
        stub._try_click_on_zip_line = lambda *args, **kwargs: ZipLineMixin._try_click_on_zip_line(stub, *args, **kwargs)
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

    def test_template_alignment_falls_back_after_three_consecutive_misses(self):
        stub = SimpleNamespace(
            _ZIP_LINE_TEMPLATE_MISS_LIMIT=3,
            scale_distance=Mock(return_value=50),
            next_frame=Mock(return_value="frame"),
            _find_zip_line_template_target=Mock(return_value=None),
            log_info=Mock(),
            _align_zip_line_distance_ocr=Mock(return_value="fallback"),
            sleep=Mock(),
        )

        result = ZipLineMixin._align_zip_line_distance(stub, 108)

        self.assertEqual(result, "fallback")
        self.assertEqual(stub._find_zip_line_template_target.call_count, 3)
        stub._align_zip_line_distance_ocr.assert_called_once_with(
            108,
            need_scroll=None,
            tolerance=50,
            max_time=100,
            raise_if_fail=True,
        )

    def test_template_hit_resets_consecutive_miss_counter(self):
        candidate = {
            "x": 230,
            "y": 585,
            "width": 20,
            "height": 30,
            "center_x": 240,
            "center_y": 600,
            "radius": 1.0,
        }
        target = SimpleNamespace(x=230, y=636, width=30, height=14)
        profile = {"max_step": 210, "min_step": 35, "slow_radius": 350, "deadzone": 8}
        stub = SimpleNamespace(
            _ZIP_LINE_TEMPLATE_MISS_LIMIT=3,
            scale_distance=Mock(return_value=50),
            next_frame=Mock(return_value="frame"),
            _find_zip_line_template_target=Mock(
                side_effect=[None, None, (candidate, target), None, None, None]
            ),
            screen_center=Mock(return_value=(960, 540)),
            _zip_line_move_profile=Mock(return_value=profile),
            move_to_target_once=Mock(),
            log_info=Mock(),
            _align_zip_line_distance_ocr=Mock(return_value="fallback"),
            sleep=Mock(),
        )

        result = ZipLineMixin._align_zip_line_distance(stub, 108)

        self.assertEqual(result, "fallback")
        self.assertEqual(stub._find_zip_line_template_target.call_count, 6)
        stub.move_to_target_once.assert_called_once()

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


if __name__ == "__main__":
    unittest.main()
