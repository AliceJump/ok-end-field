"""Enemy HP-bar presence probe tests."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from src.data.combat_observation import EnemyPresence
from src.image.enemy_health_probe import (
    _ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE,
    ENEMY_ABSENT_CONFIRM_ROUNDS,
    ENEMY_HP_BGR_LOWER,
    ENEMY_NORMAL_HP_SLICES,
    KEY_SAVE_ENEMY_PRESENCE_FRAMES,
    _has_enemy_hp_run,
    _queue_enemy_presence_artifact,
    probe_enemy_presence_fast,
)

HP_BGR = np.array([102, 68, 255], dtype=np.uint8)
BAR_BGR = np.array([220, 220, 220], dtype=np.uint8)


class _ProbeHarness:
    def __init__(self, frame, overlay=False, save_frames=False, debug=False):
        self.frame = frame
        self.overlay = overlay
        self.debug = debug
        self.config = {KEY_SAVE_ENEMY_PRESENCE_FRAMES: save_frames}
        self.draw_calls = []
        self.clear_box_calls = 0
        self.debug_logs = []

    def _is_debug_overlay_enabled(self):
        return self.overlay

    def draw_boxes(self, feature_name=None, boxes=None, color="red", debug=True):
        self.draw_calls.append((feature_name, boxes or [], color, debug))

    def clear_box(self):
        self.clear_box_calls += 1

    def log_debug(self, message):
        self.debug_logs.append(message)


def _frame(width=1920, height=1080):
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestEnemyHealthProbe(unittest.TestCase):
    def test_accepts_minimum_residual_hp_bar_at_1080p_with_context(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:110] = HP_BGR
        roi[29:32, 80:170] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_rejects_too_short_residual_hp_bar_at_1080p(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:109] = HP_BGR
        roi[29:32, 80:170] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_long_hp_run_skips_secondary_context_at_1080p(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:140] = HP_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_short_hp_colored_vfx_without_context_is_rejected(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:130] = HP_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_short_hp_run_with_horizontal_ui_context_is_accepted(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:25, 100:121] = HP_BGR
        roi[29:32, 70:180] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 1920, 1080))

    def test_rejects_thin_pink_particle(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        roi[20:23, 100:120] = HP_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_horizontal_and_vertical_evidence_must_be_same_pixel_run(self):
        roi = np.zeros((100, 400, 3), dtype=np.uint8)
        # The sampled horizontal candidate is at y=9 and only one pixel thick
        # at x=105. A separate four-pixel vertical segment sits nearby in the
        # same column. These must not be combined into one synthetic hit box.
        roi[9, 100:110] = HP_BGR
        roi[12:16, 105] = HP_BGR
        roi[20:23, 80:170] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 1920, 1080))

    def test_scales_geometry_and_context_to_4k(self):
        roi = np.zeros((200, 800, 3), dtype=np.uint8)
        roi[40:50, 200:220] = HP_BGR
        roi[58:64, 160:300] = BAR_BGR
        self.assertTrue(_has_enemy_hp_run(roi, 3840, 2160))

    def test_rejects_4k_run_below_scaled_threshold(self):
        roi = np.zeros((200, 800, 3), dtype=np.uint8)
        roi[40:50, 200:219] = HP_BGR
        roi[58:64, 160:300] = BAR_BGR
        self.assertFalse(_has_enemy_hp_run(roi, 3840, 2160))

    def test_short_hp_context_can_cross_normal_slice_boundary(self):
        frame = _frame()
        # Slice 0 ends at y=286 at 1080p. Keep the short pink candidate wholly
        # inside slice 0 while placing only its supporting slot edge in slice 1.
        frame[279:284, 800:821] = HP_BGR
        frame[289:292, 780:900] = BAR_BGR
        task = _ProbeHarness(frame)

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertEqual(getattr(task, "_enemy_hp_last_slice", None), 0)

    def test_normal_enemy_returns_present_and_reuses_slice(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame)

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        cached = getattr(task, "_enemy_hp_last_slice", None)
        self.assertIsInstance(cached, int)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertEqual(getattr(task, "_enemy_hp_last_slice", None), cached)

    def test_boss_region_returns_present(self):
        frame = _frame()
        frame[59:72, 722:1090] = HP_BGR
        task = _ProbeHarness(frame)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
        self.assertIsNone(getattr(task, "_enemy_hp_last_slice", None))

    def test_absent_requires_two_complete_normal_rounds(self):
        task = _ProbeHarness(_frame())
        checks = ENEMY_NORMAL_HP_SLICES * ENEMY_ABSENT_CONFIRM_ROUNDS

        for _ in range(checks - 1):
            self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.ABSENT)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.ABSENT)

    def test_out_of_range_pink_is_ignored(self):
        frame = _frame()
        frame[900:910, 500:700] = ENEMY_HP_BGR_LOWER
        task = _ProbeHarness(frame)
        checks = ENEMY_NORMAL_HP_SLICES * ENEMY_ABSENT_CONFIRM_ROUNDS
        state = EnemyPresence.UNKNOWN
        for _ in range(checks):
            state = probe_enemy_presence_fast(task)
        self.assertEqual(state, EnemyPresence.ABSENT)

    def test_debug_overlay_draws_scanned_regions_and_exact_hit(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame, overlay=True)

        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)

        calls = {name: (boxes, color, debug) for name, boxes, color, debug in task.draw_calls}
        scan_boxes, scan_color, scan_debug = calls["enemy_presence_scan_regions"]
        hit_boxes, hit_color, hit_debug = calls["enemy_presence_hits"]

        self.assertEqual(scan_color, "blue")
        self.assertTrue(scan_debug)
        self.assertGreaterEqual(len(scan_boxes), 2)
        self.assertTrue(any("normal_slice_0:hit:present" in box.name for box in scan_boxes))

        self.assertEqual(hit_color, "green")
        self.assertTrue(hit_debug)
        self.assertEqual(len(hit_boxes), 1)
        hit = hit_boxes[0]
        self.assertEqual((hit.x, hit.y, hit.width, hit.height), (800, 250, 120, 5))
        self.assertIn("normal_slice_0", hit.name)

    def test_debug_overlay_clears_stale_hit_after_miss(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame, overlay=True)
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)

        task.frame[:] = 0
        task.draw_calls.clear()
        self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)

        self.assertEqual(task.clear_box_calls, 1)
        self.assertTrue(any(name == "enemy_presence_scan_regions" for name, _, _, _ in task.draw_calls))
        self.assertFalse(any(name == "enemy_presence_hits" for name, _, _, _ in task.draw_calls))

    def test_capture_option_saves_same_stem_png_and_inform_json(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame, overlay=False, save_frames=True, debug=False)

        with TemporaryDirectory() as directory:
            task._enemy_presence_debug_folder = directory
            task._enemy_presence_debug_sync_save = True

            self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
            task.frame[:] = 0
            self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.UNKNOWN)

            output_dir = Path(directory)
            pngs = sorted(output_dir.glob("*.png"))
            infos = sorted(output_dir.glob("*.inform.json"))
            self.assertEqual(len(pngs), 2)
            self.assertEqual(len(infos), 2)

            png_stems = [path.stem for path in pngs]
            info_stems = [path.name.removesuffix(".inform.json") for path in infos]
            self.assertEqual(png_stems, info_stems)

            first_info = json.loads(infos[0].read_text(encoding="utf-8"))
            self.assertEqual(first_info["image"], pngs[0].name)
            self.assertEqual(first_info["schema"], "enemy_presence_probe/v1")
            self.assertEqual(first_info["final_state"], "present")
            self.assertTrue(first_info["detected"])
            self.assertEqual(first_info["screen"], {"width": 1920, "height": 1080})

            regions = {region["label"]: region for region in first_info["scanned_regions"]}
            self.assertEqual(regions["boss"]["result"], "miss")
            self.assertEqual(regions["normal_slice_0"]["result"], "hit")
            self.assertEqual(
                regions["normal_slice_0"]["hit_box"],
                {"x": 800, "y": 250, "width": 120, "height": 5, "x2": 920, "y2": 255},
            )

            first_image = cv2.imread(str(pngs[0]))
            self.assertIsNotNone(first_image)
            self.assertTrue(np.array_equal(first_image[27, 480], np.array([0, 255, 255], dtype=np.uint8)))
            self.assertTrue(np.array_equal(first_image[250, 800], np.array([0, 255, 0], dtype=np.uint8)))

            second_info = json.loads(infos[1].read_text(encoding="utf-8"))
            self.assertEqual(second_info["final_state"], "unknown")
            self.assertFalse(second_info["detected"])
            self.assertTrue(all(region["result"] == "miss" for region in second_info["scanned_regions"]))

    def test_capture_queue_is_bounded_and_drops_without_blocking_when_full(self):
        task = _ProbeHarness(_frame(), save_frames=True)
        annotated = np.zeros((4, 4, 3), dtype=np.uint8)
        inform = {"schema": "test"}

        with TemporaryDirectory() as directory, patch("src.image.enemy_health_probe.threading.Thread") as thread:
            output_dir = Path(directory)
            for index in range(_ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE + 1):
                _queue_enemy_presence_artifact(
                    task,
                    output_dir,
                    f"frame_{index}",
                    annotated,
                    inform,
                )

            work_queue = task._enemy_presence_artifact_queue
            self.assertEqual(work_queue.maxsize, _ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE)
            self.assertEqual(work_queue.qsize(), _ENEMY_PRESENCE_ARTIFACT_QUEUE_MAXSIZE)
            thread.return_value.start.assert_called_once()
            self.assertEqual(len(task.debug_logs), 1)
            self.assertIn("队列已满", task.debug_logs[0])

    def test_capture_option_disabled_writes_no_artifacts_even_with_debug_overlay(self):
        frame = _frame()
        frame[250:255, 800:920] = HP_BGR
        task = _ProbeHarness(frame, overlay=True, save_frames=False, debug=True)

        with TemporaryDirectory() as directory:
            task._enemy_presence_debug_folder = directory
            task._enemy_presence_debug_sync_save = True
            self.assertEqual(probe_enemy_presence_fast(task), EnemyPresence.PRESENT)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
