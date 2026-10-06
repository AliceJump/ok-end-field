import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_direction_diagnostics import _queue_direction_artifact
from src.image.enemy_direction_probe import EnemyDirectionObservation


class _CaptureTask:
    def __init__(self):
        self._enemy_direction_debug_sync_save = True
        self.debug = []

    def log_debug(self, message):
        self.debug.append(message)


class TestEnemyDirectionRawCapture(unittest.TestCase):
    def test_saved_bundle_keeps_raw_input_separate_from_annotation(self):
        task = _CaptureTask()
        frame = np.full((240, 320, 3), 60, dtype=np.uint8)
        original = frame.copy()
        observation = EnemyDirectionObservation(angle_deg=20.0, score=18.0)
        captured_at = datetime.now().astimezone()

        with TemporaryDirectory() as directory:
            output_dir = Path(directory)
            _queue_direction_artifact(
                task,
                output_dir,
                "sample",
                frame,
                observation,
                EnemyPresence.UNKNOWN,
                1,
                "tracking",
                None,
                None,
                1,
                captured_at,
            )

            raw_path = output_dir / "sample.raw.png"
            annotated_path = output_dir / "sample.png"
            inform_path = output_dir / "sample.inform.json"
            self.assertTrue(raw_path.exists())
            self.assertTrue(annotated_path.exists())
            self.assertTrue(inform_path.exists())

            raw = cv2.imread(str(raw_path))
            annotated = cv2.imread(str(annotated_path))
            self.assertIsNotNone(raw)
            self.assertIsNotNone(annotated)
            self.assertTrue(np.array_equal(raw, original))
            self.assertFalse(np.array_equal(annotated, original))
            self.assertTrue(np.array_equal(frame, original))

            payload = json.loads(inform_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["image"], "sample.png")
            self.assertEqual(payload["annotated_image"], "sample.png")
            self.assertEqual(payload["raw_image"], "sample.raw.png")


if __name__ == "__main__":
    unittest.main()
