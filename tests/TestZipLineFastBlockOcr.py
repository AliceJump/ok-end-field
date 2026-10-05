import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from src.image.fast_block_ocr import HsvBlockOcrProcessor
from src.image.hsv_config import HSVRange as hR
from src.tasks.mixin.zip_line_mixin import ZipLineMixin


class _FakeRecOnlyBackend:
    def __init__(self, *, unsupported=False):
        self.unsupported = unsupported
        self.calls = []

    def ocr(self, images, *, det=True, rec=True, cls=True):
        self.calls.append((images, det, rec, cls))
        if self.unsupported:
            raise TypeError("rec-only is unsupported")
        return [[("108m", 0.99) for _ in images]]


class TestFastBlockOcr(unittest.TestCase):
    @staticmethod
    def _gold_text_frame():
        # 使用接近生产截图比例的 ROI，确保候选尺寸阈值按实际 ROI 语义验证，
        # 而不是为了单测把生产阈值改成依赖整帧尺寸。
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        hsv_color = np.uint8([[[30, 100, 230]]])
        bgr_color = tuple(int(value) for value in cv2.cvtColor(hsv_color, cv2.COLOR_HSV2BGR)[0, 0])
        cv2.putText(frame, "108m", (390, 285), cv2.FONT_HERSHEY_SIMPLEX, 1.2, bgr_color, 3, cv2.LINE_AA)
        return frame

    @staticmethod
    def _task(frame, backend):
        executor = SimpleNamespace(frame=frame, ocr_lib=lambda _lib: backend)
        return SimpleNamespace(
            executor=executor,
            ocr_default_threshold=0.2,
            fix_texts=lambda _boxes: None,
            fix_match_regex=lambda match: match,
            get_box_by_name=lambda _name: None,
        )

    def test_gold_text_is_reduced_to_candidate_blocks(self):
        frame = self._gold_text_frame()
        processor = HsvBlockOcrProcessor(hR.GOLD_TEXT)

        candidates = processor.candidate_blocks(frame)

        self.assertTrue(candidates)
        self.assertTrue(any(candidate.width < frame.shape[1] // 2 for candidate in candidates))
        self.assertTrue(any(candidate.height < frame.shape[0] // 2 for candidate in candidates))

    def test_recognition_uses_rec_only_batch_and_maps_box_back(self):
        frame = self._gold_text_frame()
        backend = _FakeRecOnlyBackend()
        processor = HsvBlockOcrProcessor(hR.GOLD_TEXT)
        task = self._task(frame, backend)

        result = processor.recognize(task, match=re.compile("108"), frame=frame)

        self.assertTrue(result)
        self.assertEqual(result[0].name, "108m")
        self.assertTrue(backend.calls)
        images, det, rec, cls = backend.calls[0]
        self.assertTrue(images)
        self.assertFalse(det)
        self.assertTrue(rec)
        self.assertFalse(cls)
        self.assertLess(result[0].width, frame.shape[1])
        self.assertLess(result[0].height, frame.shape[0])

    def test_unsupported_backend_requests_generic_ocr_fallback(self):
        frame = self._gold_text_frame()
        backend = _FakeRecOnlyBackend(unsupported=True)
        processor = HsvBlockOcrProcessor(hR.GOLD_TEXT)
        task = self._task(frame, backend)

        self.assertIsNone(processor.recognize(task, match=re.compile("108"), frame=frame))


class TestZipLineClickGate(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
