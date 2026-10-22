import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from src.image.hsv_config import HSVRange as hR
from src.tasks.navigation.mixin.zip_line_mixin import ZipLineMixin


class TestZipLineTargetSelection(unittest.TestCase):
    @staticmethod
    def _candidate(state, radius, score):
        return {
            "state": state,
            "radius": radius,
            "score": score,
            "center_x": 960,
            "center_y": 540,
        }

    def test_white_candidate_is_checked_before_nearer_gold_candidate(self):
        white = self._candidate("white", 1.0, 0.95)
        gold = self._candidate("gold", 0.5, 0.80)
        target = SimpleNamespace(x=0, y=0, width=10, height=10)
        processor = object()
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            _detect_zip_line_icon_candidates=Mock(return_value=[white, gold]),
            _zip_line_distance_box=Mock(side_effect=lambda candidate: candidate["state"]),
            make_hsv_isolator=Mock(return_value=processor),
            ocr=Mock(return_value=[target]),
        )

        candidate, matched_target = ZipLineMixin._find_zip_line_template_target(stub, 106, frame="frame")

        self.assertIs(candidate, white)
        self.assertIs(matched_target, target)
        self.assertEqual(stub.ocr.call_args.kwargs["box"], "white")
        stub.make_hsv_isolator.assert_called_once_with(hR.WHITE)

    def test_distance_box_uses_70x30px_region_at_1080p(self):
        stub = SimpleNamespace(
            width=1920,
            height=1080,
            scale_distance=lambda value: value,
            box_of_screen=lambda left, top, right, bottom: (left, top, right, bottom),
        )
        candidate = {"center_x": 960, "center_y": 540}

        left, top, right, bottom = ZipLineMixin._zip_line_distance_box(stub, candidate)

        self.assertAlmostEqual(left, 925 / 1920)
        self.assertAlmostEqual(right, 995 / 1920)
        self.assertAlmostEqual(top, 568 / 1080)
        self.assertAlmostEqual(bottom, 598 / 1080)

    def test_local_ocr_retries_white_gold_union_after_state_filter_miss(self):
        candidate = self._candidate("white", 0.4, 0.82)
        target = SimpleNamespace(x=0, y=0, width=10, height=10)
        state_processor = object()
        combined_processor = object()
        combined_range = tuple(hR.WHITE) + tuple(hR.GOLD_TEXT)
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            _detect_zip_line_icon_candidates=Mock(return_value=[candidate]),
            _zip_line_distance_box=Mock(return_value="box"),
            make_hsv_isolator=Mock(side_effect=[state_processor, combined_processor]),
            ocr=Mock(side_effect=[[], [target]]),
        )

        matched_candidate, matched_target = ZipLineMixin._find_zip_line_template_target(stub, 80, frame="frame")

        self.assertIs(matched_candidate, candidate)
        self.assertIs(matched_target, target)
        stub.make_hsv_isolator.assert_has_calls([call(hR.WHITE), call(combined_range)])
        self.assertEqual(stub.ocr.call_count, 2)
        self.assertIs(stub.ocr.call_args_list[0].kwargs["frame_processor"], state_processor)
        self.assertIs(stub.ocr.call_args_list[1].kwargs["frame_processor"], combined_processor)

    def test_local_ocr_retries_raw_frame_after_filtered_misses(self):
        candidate = self._candidate("gold", 0.3, 0.78)
        target = SimpleNamespace(x=0, y=0, width=10, height=10)
        state_processor = object()
        combined_processor = object()
        stub = SimpleNamespace(
            _zip_line_distance_pattern=ZipLineMixin._zip_line_distance_pattern,
            _detect_zip_line_icon_candidates=Mock(return_value=[candidate]),
            _zip_line_distance_box=Mock(return_value="box"),
            make_hsv_isolator=Mock(side_effect=[state_processor, combined_processor]),
            ocr=Mock(side_effect=[[], [], [target]]),
        )

        matched_candidate, matched_target = ZipLineMixin._find_zip_line_template_target(stub, 57, frame="frame")

        self.assertIs(matched_candidate, candidate)
        self.assertIs(matched_target, target)
        self.assertEqual(stub.ocr.call_count, 3)
        self.assertIsNone(stub.ocr.call_args_list[2].kwargs["frame_processor"])


if __name__ == "__main__":
    unittest.main()
