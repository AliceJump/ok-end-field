import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class TestZipLineGoldGate(unittest.TestCase):
    @staticmethod
    def _stub():
        lang = SimpleNamespace(zip_line_mixin=SimpleNamespace(k_2f4f4a2f="move", k_0b1e4f35="leave"))
        return SimpleNamespace(
            lang=lang,
            box_of_screen=lambda *_args: "stop_box",
            next_frame=lambda: object(),
            active_time=Mock(return_value=0.0),
            sleep=Mock(),
            click=Mock(),
            send_key=Mock(),
            ocr=Mock(),
        )

    def test_distance_pattern_rejects_longer_numeric_distance(self):
        pattern = ZipLineMixin._zip_line_distance_pattern(108)

        self.assertIsNotNone(pattern.search("108m"))
        self.assertIsNone(pattern.search("1080m"))
        self.assertIsNone(pattern.search("2108m"))

    def test_alignment_uses_generic_hsv_ocr_processors(self):
        gold_processor = object()
        white_processor = object()
        stub = SimpleNamespace(
            make_hsv_isolator=Mock(side_effect=[gold_processor, white_processor]),
            align_ocr_or_find_target_to_center=Mock(return_value=True),
        )

        result = ZipLineMixin._align_zip_line_distance(stub, 108, need_scroll=True)

        self.assertTrue(result)
        stub.make_hsv_isolator.assert_has_calls([call(hR.GOLD_TEXT), call(hR.WHITE)])
        kwargs = stub.align_ocr_or_find_target_to_center.call_args.kwargs
        self.assertEqual(kwargs["ocr_frame_processor_list"], [gold_processor, white_processor])
        self.assertTrue(kwargs["is_num"])
        self.assertTrue(kwargs["need_scroll"])

    def test_gold_center_check_uses_generic_hsv_ocr_processor(self):
        processor = object()
        target = SimpleNamespace(x=950, y=569, width=20, height=20)
        stub = SimpleNamespace(
            make_hsv_isolator=Mock(return_value=processor),
            ocr=Mock(return_value=[target]),
            screen_center=Mock(return_value=(960, 540)),
            scale_distance=Mock(return_value=20),
            height=1080,
            next_frame=Mock(),
        )

        result = ZipLineMixin._zip_line_target_is_gold_and_centered(stub, 108, frame="frame")

        self.assertTrue(result)
        stub.make_hsv_isolator.assert_called_once_with(hR.GOLD_TEXT)
        self.assertIs(stub.ocr.call_args.kwargs["frame_processor"], processor)
        self.assertEqual(stub.ocr.call_args.kwargs["frame"], "frame")

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


if __name__ == "__main__":
    unittest.main()
