import threading
import unittest
from types import SimpleNamespace

import numpy as np

from scripts.analyze_minimap_timing import Trajectory, speed_bin
from src.localization.wgc_frame_timing import _FRAME_TIMINGS, install_wgc_frame_timing, map_frame_time, timing_for_frame


class TestPositionTimeInterpolation(unittest.TestCase):
    def setUp(self):
        self.sample = {"flow": 1, "scene": 228}
        self.left = {"time": 10.0, "flow": 1, "scene": 228, "x": 0.0, "z": 2.0}
        self.right = dict(self.left, time=10.2, x=2.0, z=4.0)

    def test_linear_bracket_and_velocity(self):
        position, velocity = Trajectory([self.right, self.left]).at(10.1, self.sample)
        np.testing.assert_allclose(position, [1.0, 3.0])
        np.testing.assert_allclose(velocity, [10.0, 10.0])

    def test_no_extrapolation_or_interpolation_over_gaps(self):
        trajectory = Trajectory([self.left, self.right])
        self.assertIsNone(trajectory.at(9.9, self.sample))
        self.assertIsNone(trajectory.at(10.3, self.sample))
        self.assertIsNone(Trajectory([self.left, dict(self.right, time=11.0)]).at(10.1, self.sample))

    def test_no_interpolation_across_reconnections_or_scenes(self):
        for right in (dict(self.right, flow=2), dict(self.right, scene=999)):
            self.assertIsNone(Trajectory([self.left, right]).at(10.1, self.sample))
        self.assertIsNone(Trajectory([self.left, self.right]).at(10.1, {"flow": 2, "scene": 228}))

    def test_speed_groups_are_explicit(self):
        self.assertEqual(speed_bin(0), "stationary")
        self.assertEqual(speed_bin(1.5), "slow_0.5_to_3_m_s")
        self.assertEqual(speed_bin(5), "normal_3_to_6_m_s")
        self.assertEqual(speed_bin(7), "fast_6_plus_m_s")


class TestFrameClockMapping(unittest.TestCase):
    def test_small_observed_wgc_frame_lead_keeps_the_actual_timestamp(self):
        result = map_frame_time(100_050_000, 100_000_000, 100_000_020, 10_000_000, 1_000_000_000_000)
        self.assertAlmostEqual(result.frame_time, 1000.004999)
        with self.assertRaises(ValueError):
            map_frame_time(100_300_000, 100_000_000, 100_000_020, 10_000_000, 1_000_000_000_000)

    def test_100ns_timestamp_maps_to_bracketed_wall_clock(self):
        result = map_frame_time(99_900_000, 100_000_000, 100_000_020, 10_000_000, 1_000_000_000_000)
        self.assertAlmostEqual(result.frame_time, 999.989999, places=8)
        self.assertAlmostEqual(result.clock_uncertainty_s, 0.0000011)
        self.assertEqual(result.system_relative_100ns, 99_900_000)

    def test_qpc_frequency_is_independent_of_timespan_units(self):
        result = map_frame_time(99_900_000, 30_000_000, 30_000_006, 3_000_000, 1_000_000_000_000)
        self.assertAlmostEqual(result.frame_time, 999.989999, places=8)

    def test_invalid_stale_and_future_timestamps_are_not_arrival_times(self):
        for ticks in (0, -1, 70_000_000, 110_000_000):
            with self.subTest(ticks=ticks), self.assertRaises(ValueError):
                map_frame_time(ticks, 100_000_000, 100_000_020, 10_000_000, 1_000_000_000_000)
        with self.assertRaises(ValueError):
            map_frame_time(99_900_000, 100, 99, 10_000_000, 1_000_000_000_000)


class TestWgcImageTimingPairing(unittest.TestCase):
    def setUp(self):
        class Capture:
            def __init__(self):
                self.lock = threading.RLock()
                self.ticks = 99_900_000
                self.fail = False

            def convert_dx_frame(self, frame):
                return np.zeros((3, 3), dtype=np.uint8)

            def _do_get_frame(self, frame_deadline=None):
                self.frame_deadline = frame_deadline
                if self.fail:
                    return None
                return self.convert_dx_frame(SimpleNamespace(SystemRelativeTime=self.ticks)).copy()

        self.cls = Capture
        self.observer = lambda ticks: map_frame_time(ticks, 100_000_000, 100_000_020, 10_000_000, 1_000_000_000_000)
        install_wgc_frame_timing(Capture, self.observer)
        self.capture = Capture()

    def test_metadata_matches_returned_image_after_crop_or_copy(self):
        image = self.capture._do_get_frame()
        self.assertEqual(timing_for_frame(self.capture, image).system_relative_100ns, self.capture.ticks)
        self.assertIsNone(timing_for_frame(self.capture, image.copy()))

    def test_probe_frame_deadline_is_forwarded_to_updated_capture_api(self):
        image = self.capture._do_get_frame(frame_deadline=123.5)
        self.assertEqual(self.capture.frame_deadline, 123.5)
        self.assertIsNotNone(timing_for_frame(self.capture, image))

    def test_framework_bgra_slice_keeps_timestamp_without_intermediate_view(self):
        image = self.capture._do_get_frame()
        view = image[:, :2]
        del image
        self.assertIsNotNone(timing_for_frame(self.capture, view))

    def test_timeout_clears_previous_metadata(self):
        image = self.capture._do_get_frame()
        self.capture.fail = True
        self.assertIsNone(self.capture._do_get_frame())
        self.assertIsNotNone(timing_for_frame(self.capture, image))
        self.assertIsNone(timing_for_frame(self.capture, None))

    def test_none_is_never_paired_with_a_collected_image(self):
        image = self.capture._do_get_frame()
        del image
        self.assertIsNone(timing_for_frame(self.capture, None))

    def test_missing_timestamp_does_not_fail_capture_or_reuse_previous_time(self):
        old = self.capture._do_get_frame()
        self.capture.ticks = 0
        new = self.capture._do_get_frame()
        self.assertIsNotNone(new)
        self.assertIsNone(timing_for_frame(self.capture, new))
        self.assertIsNotNone(timing_for_frame(self.capture, old))

    def test_install_is_idempotent(self):
        wrapper = self.cls._do_get_frame
        install_wgc_frame_timing(self.cls, self.observer)
        self.assertIs(self.cls._do_get_frame, wrapper)

    def test_retained_frame_uses_its_own_time_after_refresh(self):
        old = self.capture._do_get_frame()
        ticks = self.capture.ticks
        self.capture.ticks += 1000
        new = self.capture._do_get_frame()
        self.assertEqual(timing_for_frame(self.capture, old).system_relative_100ns, ticks)
        self.assertEqual(timing_for_frame(self.capture, new).system_relative_100ns, ticks + 1000)

    def test_registry_is_bounded_and_never_crosses_capture_owners(self):
        images = [self.capture._do_get_frame() for _ in range(40)]
        self.assertLessEqual(len(_FRAME_TIMINGS), 32)
        self.assertIsNone(timing_for_frame(self.capture, images[0]))
        self.assertIsNotNone(timing_for_frame(self.capture, images[-1]))
        self.assertIsNone(timing_for_frame(self.cls(), images[-1]))
