import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import weakref
from concurrent.futures import CancelledError, wait
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.core.config_migration import apply_value_migrations
from src.core.NavConfig import NAV_MAP_ID_KEY
from src.core.PositionConfig import (
    CONFIG_USE_EXPERIMENTAL,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE,
    CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT,
    CONFIG_VISUAL_MAP_ROOT,
    DEFAULT_POSITION_CONFIG,
    DEFAULT_VISUAL_MAP_ROOT,
)
from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.layer_search_index import LayerSearchIndex
from src.localization.layered_minimap_locator import LayeredMinimapLocator, layer_calibration
from src.localization.localization_workers import (
    DecodeBudget,
    LocalizationWorkers,
    ResourcePending,
    _ResourceExecutor,
    ordered_results,
)
from src.localization.map_set_locator import MapSetLocator
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MapCandidate,
    MinimapGeometry,
    MinimapMapLocator,
    MinimapTracker,
    TrackResult,
    _has_minimap_texture,
    candidate_margin,
)
from src.localization.position_config_migration import (
    backup_retired_position_settings,
    migrate_item_position_overrides,
    migrate_visual_map_root,
)
from src.localization.position_service import WorldPoseService
from src.localization.visual_capture_coordination import VisualCaptureCoordinator
from src.localization.visual_pose_sampling import VisualPoseSampler
from src.localization.visual_position_source import VisualPositionSource
from src.localization.wgc_frame_timing import FrameTiming, timing_for_frame
from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask


class TestVisualPositionSource(unittest.TestCase):
    def test_unsampled_odometry_keeps_new_absolute_fix_without_adding_cached_chain_frames(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        source.sample(self.frame(), now=1)
        source.sample(self.frame(), now=1.2)
        before = (len(source.chain._nodes), len(source.keyframe_chain._nodes), source._keyframe_tick)
        with (
            patch.object(source.odometry, "sample", return_value={"sampled": False, "reason": "interval"}),
            patch.object(
                source.odometry, "last_ring_patch", side_effect=AssertionError("cached frame must not enter chain")
            ),
        ):
            state = source.sample(self.frame(504), now=1.3)
        self.assertTrue(state["position_trusted"])
        self.assertAlmostEqual(state["x"], 126, delta=0.3)
        self.assertEqual(before, (len(source.chain._nodes), len(source.keyframe_chain._nodes), source._keyframe_tick))

    def test_weak_too_soon_preserves_evidence_and_original_timestamp(self):
        source, _ = self._layered_source()
        self.addCleanup(source.close)
        anchored = source.sample(self.frame(504), now=1.4)
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        locate = source.locator.locate_near(self.frame(504), (126, -100), geometry, search_radius_m=12)
        rejected = TrackResult("lost", False, "insufficient_acquisition_evidence", locate, None, 0, True)
        before = (source.evidence.previous, source.evidence.tick, source._last_absolute_t, len(source.chain._nodes))
        with patch.object(source.tracker, "update", return_value=rejected):
            for timestamp in (1.42, 1.47, 1.53):
                retained = source.sample(self.frame(505), now=timestamp)
                self.assertTrue(retained["position_trusted"], retained)
                self.assertTrue(retained["visual_duplicate_frame"])
                self.assertFalse(retained["absolute_position_valid"])
                self.assertFalse(retained["odom_ok"])
                self.assertEqual(retained["frame_t"], anchored["frame_t"])
                self.assertEqual(retained["x"], anchored["x"])
                self.assertEqual(
                    before,
                    (source.evidence.previous, source.evidence.tick, source._last_absolute_t, len(source.chain._nodes)),
                )
            moving = source.sample(self.frame(508), now=1.6)
            self.assertTrue(moving["position_trusted"], moving)
            self.assertAlmostEqual(moving["x"], 127, delta=0.3)
            self.assertGreater(moving["visual_chain_error_budget_m"], anchored["visual_chain_error_budget_m"])
            # Skips cannot indefinitely refresh a chain or hide a genuine gap.
            lost = source.sample(self.frame(508), now=3.7)
            self.assertFalse(lost["position_trusted"])
            self.assertIsNone(source.estimate())

    def test_too_soon_does_not_hide_loading_or_floor_change(self):
        for failure in ("loading", "floor"):
            with self.subTest(failure=failure):
                source, _ = self._layered_source()
                self.addCleanup(source.close)
                source.sample(self.frame(504), now=1.4)
                geometry = MinimapGeometry.from_frame(self.width, self.height)
                locate = source.locator.locate_near(self.frame(504), (126, -100), geometry, search_radius_m=12)
                locate = None if failure == "loading" else replace(locate, best=replace(locate.best, tier_id="floor_1"))
                rejected = TrackResult("lost", False, failure, locate, None, 0, True)
                with patch.object(source.tracker, "update", return_value=rejected):
                    state = source.sample(self.frame(505), now=1.42)
                self.assertFalse(state["position_trusted"])

    def test_source_serializes_sample_state_and_rejects_after_close(self):
        source = VisualPositionSource(SimpleNamespace(), SimpleNamespace(), "map02")
        entered, second_started, second_entered, release = (threading.Event() for _ in range(4))
        self.addCleanup(release.set)
        calls = []

        def sample(frame, *, now=None):
            calls.append(now)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            else:
                second_entered.set()
            return {}

        source._sample = sample
        first = threading.Thread(target=lambda: source.sample(None, now=1))

        def next_sample():
            second_started.set()
            source.sample(None, now=2)

        second = threading.Thread(target=next_sample)
        first.start()
        self.assertTrue(entered.wait(3))
        second.start()
        self.assertTrue(second_started.wait(3))
        self.assertFalse(second_entered.wait(0.05))
        release.set()
        first.join(timeout=3)
        second.join(timeout=3)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(calls, [1, 2])
        source.close()
        state = source.sample(None, now=3)
        self.assertFalse(state["position_trusted"])
        self.assertEqual(state["trust_reason"], "visual_source_closed")
        self.assertEqual(calls, [1, 2])

    def setUp(self):
        self.width, self.height = 640, 360
        self.task = SimpleNamespace(width=self.width, height=self.height, active_time=lambda: 0)
        self.image = cv2.GaussianBlur(
            np.random.default_rng(21).integers(20, 230, (800, 900), dtype=np.uint8), (3, 3), 0
        )
        self.cal = MapCalibration(np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]]))
        self.locator = MinimapMapLocator(self.image, self.cal, scale_override=1)

    def frame(self, x=500, y=400):
        g = MinimapGeometry.from_frame(self.width, self.height)
        matrix = np.float32([[1, 0, g.center_x - x], [0, 1, g.center_y - y]])
        return cv2.cvtColor(cv2.warpAffine(self.image, matrix, (self.width, self.height)), cv2.COLOR_GRAY2BGR)

    def test_absolute_acquisition_and_chain_are_independent_of_ws_and_packets(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        self.assertFalse(source.sample(self.frame(), now=1)["position_trusted"])
        for i in range(1, 5):
            state = source.sample(self.frame(500 + i * 2), now=1 + i * 0.2)
            self.assertTrue(state["position_trusted"], state)
            self.assertAlmostEqual(state["x"], (500 + i * 2) / 4, delta=0.3)
            self.assertAlmostEqual(state["z"], -100, delta=0.3)
            self.assertIsNone(state["y"])
            self.assertFalse(state["sync_supported"])
        self.assertGreater(len(source.chain._nodes), 1)
        self.assertGreater(len(source.keyframe_chain._nodes), 0)

    def test_pending_duplicate_keeps_evidence_without_counting_as_confirmation(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        frame = self.frame()
        first = source.sample(frame, now=1)
        self.assertTrue(first["visual_confirmation_pending"])
        self.assertFalse(first["position_trusted"])
        with patch.object(source.tracker, "update", wraps=source.tracker.update) as update:
            for _ in range(3):
                duplicate = source.sample(frame, now=1)
                self.assertTrue(duplicate["visual_duplicate_frame"])
                self.assertTrue(duplicate["visual_confirmation_pending"])
                self.assertFalse(duplicate["position_trusted"])
                self.assertIsNone(duplicate["x"])
            update.assert_not_called()
            confirmed = source.sample(frame, now=1.02)
            update.assert_called_once()
        self.assertTrue(confirmed["position_trusted"])
        self.assertFalse(confirmed.get("visual_confirmation_pending", False))
        self.assertFalse(confirmed.get("visual_duplicate_frame", False))
        self.assertAlmostEqual(confirmed["x"], 125, delta=0.3)

    def test_pending_clock_rollback_and_loading_still_require_new_confirmation(self):
        for next_time, next_frame in ((0.9, self.frame()), (1.02, np.zeros((360, 640, 3), dtype=np.uint8))):
            with self.subTest(next_time=next_time):
                source = VisualPositionSource(self.task, self.locator, "map02")
                source.sample(self.frame(), now=1)
                state = source.sample(next_frame, now=next_time)
                self.assertFalse(state["position_trusted"])
                self.assertFalse(state.get("visual_duplicate_frame", False))
                if next_time > 1:
                    self.assertFalse(state["visual_confirmation_pending"])
                    self.assertFalse(source.sample(self.frame(), now=1.04)["position_trusted"])

    def test_trusted_duplicate_preserves_anchor_and_does_not_advance_tracking(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        frame = self.frame()
        source.sample(frame, now=1)
        trusted = source.sample(frame, now=1.02)
        self.assertTrue(trusted["position_trusted"])
        before = (source._sequence, source._last_absolute_t, len(source.chain._nodes), source._keyframe_tick)
        with (
            patch.object(source.tracker, "update", wraps=source.tracker.update) as update,
            patch.object(source.odometry, "sample", wraps=source.odometry.sample) as odometry,
        ):
            duplicate = source.sample(frame, now=1.02)
            update.assert_not_called()
            odometry.assert_not_called()
        self.assertEqual(duplicate, {**trusted, "visual_duplicate_frame": True})
        self.assertEqual(
            before, (source._sequence, source._last_absolute_t, len(source.chain._nodes), source._keyframe_tick)
        )
        self.assertFalse(source.sample(frame, now=1.01)["position_trusted"])

    def test_fast_confirmation_avoids_crossing_unchanged_one_second_gap_limit(self):
        # Deterministic 840 ms capture/matching/heading budget: another fixed
        # 200 ms wait would discard the first observation, not relax its gates.
        for fast in (False, True):
            with self.subTest(fast=fast):
                source = VisualPositionSource(self.task, self.locator, "map02")
                first = source.sample(self.frame(), now=1)
                first.update(frame_t=1.0, frame_time_source="wgc_system_relative_time")
                hub = RuntimeStateHub()
                publish_world_pose(hub, first, source="visual_map", now=1)
                first = hub.value(RuntimeTopic.WORLD_POSE, now=1)
                delay = GridNavigationMixin._grid_anchor_poll_delay(first, 0.2) if fast else 0.2
                self.assertEqual(source.tracker.pending_max_gap_s, 1.0)
                state = source.sample(self.frame(), now=1 + 0.84 + delay)
                self.assertEqual(state["position_trusted"], fast)

    def test_confirmation_metadata_survives_world_pose_publication_and_clears_on_accept(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        source.sample(self.frame(), now=1)
        hub = RuntimeStateHub()
        for stamp, pending, duplicate in ((1, True, True), (1.02, False, False)):
            state = source.sample(self.frame(), now=stamp)
            state.update(frame_t=stamp, frame_time_source="wgc_system_relative_time")
            publish_world_pose(hub, state, source="visual_map", now=stamp)
            pose = hub.value(RuntimeTopic.WORLD_POSE, now=stamp)
            self.assertEqual(pose["visual_confirmation_pending"], pending)
            self.assertEqual(pose["visual_duplicate_frame"], duplicate)
            self.assertEqual(pose["position_trusted"], not pending)
            self.assertEqual(GridNavigationMixin._grid_anchor_poll_delay(pose, 0.2), 1 / 60 if duplicate else 0.2)

    def test_subpixel_coordinates_and_scores_use_the_same_patch(self):
        frame = self.frame(500.45, 400.35)
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        for result in (
            self.locator.locate_global(frame, geometry),
            self.locator.locate_near(frame, (125, -100), geometry, search_radius_m=12),
        ):
            self.assertGreater(result.best.full_ncc, 0.98)
            self.assertAlmostEqual(result.best.map_x, 500.45, delta=0.15)
            self.assertAlmostEqual(result.best.map_y, 400.35, delta=0.15)

    def test_loading_drops_anchor_and_requires_reacquisition(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        source.sample(self.frame(), now=1)
        self.assertTrue(source.sample(self.frame(), now=1.2)["position_trusted"])
        lost = source.sample(np.zeros((360, 640, 3), dtype=np.uint8), now=1.4)
        self.assertFalse(lost["position_trusted"])
        self.assertIsNone(lost["x"])
        self.assertEqual(len(source.chain._nodes), 0)
        self.assertFalse(source.sample(self.frame(), now=1.6)["position_trusted"])
        self.assertTrue(source.sample(self.frame(), now=1.8)["position_trusted"])

    def test_rejected_acquisition_exposes_scores_without_publishing_position(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        candidate = MapCandidate(500, 400, 125, -100, 0.83, full_ncc=0.63)
        result = LocateResult("accepted", "test", candidate, (candidate,), 0.4, 1)
        with patch.object(self.locator, "locate_global", return_value=result):
            state = source.sample(self.frame(), now=1)
        self.assertFalse(state["position_trusted"])
        self.assertIsNone(state["x"])
        self.assertIsNone(source.estimate())
        self.assertEqual(state["trust_reason"], "insufficient_acquisition_evidence")
        self.assertFalse(state["visual_confirmation_pending"])
        self.assertEqual(state["visual_full_ncc"], 0.63)
        self.assertEqual(state["visual_match_elapsed_s"], 0.4)
        state = source.sample(np.zeros((360, 640, 3), dtype=np.uint8), now=1.2)
        self.assertNotIn("visual_full_ncc", state)

    def test_confirmed_absolute_fix_is_usable_before_odometry_baseline(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        self.assertFalse(source.sample(self.frame(), now=1)["position_trusted"])
        state = source.sample(self.frame(504), now=1.2)
        self.assertTrue(state["position_trusted"])
        self.assertTrue(state["absolute_position_valid"])
        self.assertFalse(state["odom_ok"])
        from src.runtime_state.world_pose import build_world_pose

        self.assertTrue(build_world_pose(state, source="visual_map")["absolute_position_valid"])
        lost = source.sample(np.zeros((360, 640, 3), dtype=np.uint8), now=1.4)
        self.assertFalse(build_world_pose(lost, source="visual_map")["absolute_position_valid"])

    def test_single_turn_sampling_keeps_anchor_while_blocking_turn_loses_it(self):
        for interval, trusted in ((1.2, True), (3.4, False)):
            with self.subTest(interval=interval):
                source = VisualPositionSource(self.task, self.locator, "map02")
                source.sample(self.frame(), now=1)
                initial = source.sample(self.frame(), now=1.2)
                state = source.sample(self.frame(508), now=1.2 + interval)
                self.assertEqual(state["position_trusted"], trusted)
                if trusted:
                    self.assertEqual(state["sync_seq"], initial["sync_seq"])
                    self.assertAlmostEqual(state["x"], 127, delta=0.3)

    def _layered_source(self):
        locator = LayeredMinimapLocator(self.locator, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, locator, "map02")
        source.sample(self.frame(), now=1)
        initial = source.sample(self.frame(), now=1.2)
        self.assertTrue(initial["position_trusted"])
        return source, initial

    def test_color_acquisition_and_unknown_floor_share_the_visual_chain_safely(self):
        pixels = cv2.merge((self.image, np.roll(self.image, 13, axis=0), np.roll(self.image, 19, axis=1)))
        base = MinimapMapLocator(pixels, self.cal, scale_override=1)
        layered = LayeredMinimapLocator(base, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, layered, "map02")
        self.addCleanup(source.close)
        geometry = MinimapGeometry.from_frame(self.width, self.height)

        def frame(x):
            transform = np.float32([[1, 0, geometry.center_x - x], [0, 1, geometry.center_y - 400]])
            return cv2.warpAffine(pixels, transform, (self.width, self.height))

        for now in (1.0, 1.2, 1.4):
            initial = source.sample(frame(500), now=now)
        self.assertTrue(initial["absolute_position_valid"], initial)
        locate = layered.locate_near(frame(502), (125.5, -100), geometry)
        candidate = replace(locate.best, ncc=0.50, full_ncc=0.50)
        self.assertTrue(candidate.color)
        accepted = replace(locate, best=candidate, candidates=(candidate,), spatial_margin=0.12)
        with patch.object(layered, "locate_near", return_value=accepted):
            anchored = source.sample(frame(502), now=1.6)
        self.assertTrue(anchored["absolute_position_valid"], anchored)
        pending = replace(
            accepted,
            best=replace(candidate, tier_id=None),
            status="rejected",
            reason="layer_scan_pending",
            spatial_margin=0,
        )
        with patch.object(layered, "locate_near", return_value=pending):
            continued = source.sample(frame(504), now=1.8)
        self.assertTrue(continued["position_trusted"], continued)
        self.assertFalse(continued["absolute_position_valid"])
        self.assertFalse(continued["tier_trusted"])
        self.assertEqual(source._last_absolute_t, 1.6)
        self.assertAlmostEqual(continued["x"], 126, delta=0.3)

    def test_rejected_prediction_competition_cannot_reset_visual_budget_or_anchor_age(self):
        for status, margin in (("rejected", 0.0), ("ambiguous", 0.10)):
            with self.subTest(status=status):
                self._check_prediction_does_not_reset_anchor(status, margin)

    def _check_prediction_does_not_reset_anchor(self, status, margin):
        source, _ = self._layered_source()
        self.addCleanup(source.close)
        initial = source.sample(self.frame(504), now=1.4)
        locate = source.locator.locate_near
        anchor_time = source._last_absolute_t
        anchor_budget = initial["visual_chain_error_budget_m"]
        wide_calls = []

        def pending_layers(*args, **kwargs):
            result = locate(*args, **kwargs)
            if kwargs["search_radius_m"] == source.tracker.search_radius_m:
                wide_calls.append(result)
                return replace(result, status=status, reason="competition_unverified", spatial_margin=margin)
            return result

        with patch.object(source.locator, "locate_near", side_effect=pending_layers):
            for index in range(7):
                now = 2.6 + index * 0.4
                state = source.sample(self.frame(506 + index * 2), now=now)
                self.assertTrue(state["position_trusted"], state)
                self.assertFalse(state["absolute_position_valid"], state)
                self.assertEqual(source._last_absolute_t, anchor_time)
                self.assertAlmostEqual(state["visual_absolute_anchor_age_s"], now - anchor_time)
                # Direct keyframe checks can shorten the path, but cannot reset
                # its inherited cost to a newly acquired absolute anchor's cost.
                self.assertGreater(state["visual_chain_error_budget_m"], anchor_budget)
        self.assertEqual(len(wide_calls), 7)

    def test_strong_map_and_independent_motion_confirm_short_control_gap(self):
        source, initial = self._layered_source()
        state = source.sample(self.frame(508), now=4.2)
        self.assertTrue(state["position_trusted"], state)
        self.assertEqual(state["visual_tracking_reason"], "visual_motion_confirmed")
        self.assertEqual(state["sync_seq"], initial["sync_seq"])
        self.assertAlmostEqual(state["x"], 127, delta=0.3)

    def test_motion_continuity_cannot_bypass_invalid_evidence(self):
        for failure in ("weak_motion", "disagreement", "new_floor", "unknown_floor", "long_gap", "loading"):
            with self.subTest(failure=failure):
                source, _ = self._layered_source()
                if failure == "weak_motion":
                    original = source.odometry.sample
                    source.odometry.sample = lambda *args, sample=original, **kwargs: {
                        **sample(*args, **kwargs),
                        "response": 0.2,
                    }
                elif failure == "disagreement":
                    source._origin += np.array([16, 0])  # 4m inconsistent with this frame.
                elif failure in ("new_floor", "unknown_floor"):
                    original = source.locator.locate_global

                    def locate(*args, original=original, failure=failure, **kwargs):
                        result = original(*args, **kwargs)
                        tier = "floor_1" if failure == "new_floor" else None
                        return replace(result, best=replace(result.best, tier_id=tier))

                    source.locator.locate_global = locate
                frame = self.frame(508) if failure != "loading" else np.zeros((360, 640, 3), dtype=np.uint8)
                state = source.sample(frame, now=6.2 if failure == "long_gap" else 4.2)
                self.assertFalse(state["position_trusted"], state)
                self.assertIsNone(state["x"])

    def test_motion_confirmation_cannot_acquire_a_first_fix(self):
        locator = LayeredMinimapLocator(self.locator, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, locator, "map02")
        state = source.sample(self.frame(), now=1)
        self.assertFalse(state["position_trusted"])
        state = source.sample(self.frame(508), now=4.2)
        self.assertFalse(state["position_trusted"])

    def test_short_ncc_rejection_continues_only_a_connected_same_layer_chain(self):
        layered = LayeredMinimapLocator(self.locator, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, layered, "map02")
        self.addCleanup(source.close)
        source.sample(self.frame(), now=1.0)
        anchored = source.sample(self.frame(504), now=1.2)
        self.assertTrue(anchored["position_trusted"], anchored)
        anchored = source.sample(self.frame(508), now=1.4)
        self.assertTrue(anchored["position_trusted"], anchored)
        self.assertGreater(len(source.chain._nodes), 0)
        self.assertTrue(source.chain._connected_to_absolute_anchor())

        geometry = MinimapGeometry.from_frame(self.width, self.height)
        trace = []
        for index, x in enumerate(range(512, 560, 4), start=1):
            timestamp = 1.4 + index * 0.2
            locate = layered.locate_near(self.frame(x), (x / 4, -100), geometry, search_radius_m=12)
            self.assertIsNotNone(locate)
            self.assertEqual(locate.best.tier_id, "__main__")
            rejected = TrackResult("lost", False, "insufficient_acquisition_evidence", locate, None, 0, True)
            with patch.object(source.tracker, "update", return_value=rejected):
                state = source.sample(self.frame(x), now=timestamp)
            self.assertTrue(state["position_trusted"], state)
            self.assertEqual(state["trust_reason"], "visual_anchor_chain")
            self.assertTrue(state["tier_trusted"])
            trace.append(abs(state["x"] - x / 4))
        self.assertLessEqual(max(trace), 0.5, trace)
        self.assertTrue(source.chain._connected_to_absolute_anchor())
        recovered = source.sample(self.frame(560), now=4.0)
        self.assertTrue(recovered["position_trusted"], recovered)
        self.assertTrue(recovered["absolute_position_valid"])
        self.assertLessEqual(abs(recovered["x"] - 140), 0.5)

    def test_teleport_to_unrelated_terrain_does_not_continue_old_chain(self):
        source, _ = self._layered_source()
        source.sample(self.frame(504), now=1.4)
        state = source.sample(self.frame(700, 600), now=1.6)
        self.assertFalse(state["position_trusted"], state)
        self.assertIsNone(state["x"])

    def test_long_continuous_visual_chain_and_strong_anchor_recovery(self):
        source, _ = self._layered_source()
        self.addCleanup(source.close)
        source.sample(self.frame(504), now=1.4)
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        locate = source.locator.locate_near(self.frame(504), (126, -100), geometry, search_radius_m=12)
        rejected = TrackResult("lost", False, "insufficient_acquisition_evidence", locate, None, 0, True)
        errors = []
        with patch.object(source.tracker, "update", return_value=rejected):
            for i in range(300):
                phase = i % 80
                x = 504 + min(phase, 80 - phase)
                state = source.sample(self.frame(x), now=1.6 + i * 0.2)
                self.assertTrue(state["position_trusted"], (i, state))
                self.assertFalse(state["absolute_position_valid"])
                errors.append(float(np.hypot(state["x"] - x / 4, state["z"] + 100)))
        self.assertGreater(state["visual_absolute_anchor_age_s"], 59)
        self.assertGreater(state["visual_chain_keyframe_checks"], 0)
        from src.runtime_state.world_pose import build_world_pose

        published = build_world_pose(state, source="visual_map")
        self.assertEqual(published["visual_chain_error_budget_m"], state["visual_chain_error_budget_m"])
        self.assertLess(max(errors), 0.5)
        first = source.sample(self.frame(x), now=61.6)
        self.assertTrue(first["position_trusted"], first)
        recovered = source.sample(self.frame(x), now=61.8)
        self.assertTrue(recovered["absolute_position_valid"], recovered)
        self.assertLess(float(np.hypot(recovered["x"] - x / 4, recovered["z"] + 100)), 0.3)
        self.sequence_metrics = {
            "frames_without_absolute_observation": 300,
            "max_error_m": max(errors),
            "last_absolute_age_s": state["visual_absolute_anchor_age_s"],
            "final_error_allowance_m": state["visual_chain_error_budget_m"],
            "recovery_s": 61.8 - 61.6,
            "recovery_frames": 2,
        }

    def test_budget_exhaustion_revokes_position_then_new_absolute_anchor_recovers(self):
        source, _ = self._layered_source()
        source.sample(self.frame(504), now=1.4)
        source.evidence.max_error_m = 0.001
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        locate = source.locator.locate_near(self.frame(506), (126.5, -100), geometry, search_radius_m=12)
        rejected = TrackResult("lost", False, "insufficient_acquisition_evidence", locate, None, 0, True)
        with patch.object(source.tracker, "update", return_value=rejected):
            state = source.sample(self.frame(506), now=1.6)
        self.assertFalse(state["position_trusted"])
        self.assertEqual(state["trust_reason"], "visual_chain_error_budget_exhausted")
        self.assertIsNone(source.estimate())
        self.assertIsNone(source.evidence.previous)
        source.evidence.max_error_m = 1.0
        source.sample(self.frame(508), now=1.8)
        recovered = source.sample(self.frame(508), now=2.0)
        self.assertTrue(recovered["position_trusted"], recovered)
        self.assertTrue(recovered["absolute_position_valid"])
        self.assertEqual(source.evidence.anchor_time, 2.0)

    def test_unknown_floor_keeps_chain_but_does_not_refresh_anchor_or_claim_tier(self):
        source, _ = self._layered_source()
        self.addCleanup(source.close)
        initial = source.sample(self.frame(), now=1.4)
        locate = source.locator.locate_near

        def unknown(*args, **kwargs):
            result = locate(*args, **kwargs)
            return replace(
                result,
                best=replace(result.best, tier_id=None),
                status="rejected",
                reason="layer_scan_pending",
                spatial_margin=0,
            )

        with (
            patch.object(source.locator, "locate_near", side_effect=unknown) as near,
            patch.object(source.locator, "locate_global", side_effect=AssertionError("must stay local")),
        ):
            for i in range(15):
                now, x = 1.6 + i * 0.2, 502 + i * 2
                state = source.sample(self.frame(x), now=now)
                self.assertTrue(state["position_trusted"], state)
                self.assertFalse(state["absolute_position_valid"])
                self.assertFalse(state["tier_trusted"])
                self.assertIsNone(state["tier_id"])
                self.assertAlmostEqual(state["x"], x / 4, delta=0.3)
                self.assertEqual(source._last_absolute_t, 1.4)
                self.assertGreater(state["visual_chain_error_budget_m"], initial["visual_chain_error_budget_m"])
            self.assertEqual(near.call_count, 15)
            self.assertTrue(all(call.kwargs["search_radius_m"] == 80 for call in near.call_args_list))
        # Even high spatial confidence with unresolved floor is not a new anchor.
        result = locate(self.frame(x), (x / 4, -100), MinimapGeometry.from_frame(self.width, self.height))
        result = replace(result, best=replace(result.best, tier_id=None))
        with patch.object(source.locator, "locate_near", return_value=result):
            for now in (4.6, 4.8):
                state = source.sample(self.frame(x), now=now)
                self.assertTrue(state["position_trusted"], state)
                self.assertFalse(state["absolute_position_valid"])
                self.assertEqual(source._last_absolute_t, 1.4)
        for now in (5.0, 5.2):
            recovered = source.sample(self.frame(x), now=now)
        self.assertTrue(recovered["absolute_position_valid"], recovered)
        self.assertTrue(recovered["tier_trusted"])

    def test_unknown_floor_does_not_hide_invalid_chain_or_real_transition(self):
        for failure in ("teleport", "gap", "budget", "loading", "floor", "map"):
            with self.subTest(failure=failure):
                source, _ = self._layered_source()
                self.addCleanup(source.close)
                source.sample(self.frame(), now=1.4)
                geometry = MinimapGeometry.from_frame(self.width, self.height)
                locate = source.locator.locate_near(self.frame(504), (126, -100), geometry)
                locate = replace(
                    locate,
                    best=replace(locate.best, tier_id=None),
                    status="rejected",
                    reason="layer_scan_pending",
                    spatial_margin=0,
                )
                if failure == "loading":
                    locate = None
                elif failure == "floor":
                    locate = replace(locate, best=replace(locate.best, tier_id="floor_1"))
                elif failure == "map":
                    locate = replace(locate, best=replace(locate.best, map_id="map03"))
                elif failure == "budget":
                    source.evidence.max_error_m = 0.001
                rejected = TrackResult("lost", False, "layer_scan_pending", locate, None, 0, True)
                frame = self.frame(700, 600) if failure == "teleport" else self.frame(504)
                with patch.object(source.tracker, "update", return_value=rejected):
                    state = source.sample(frame, now=4.0 if failure == "gap" else 1.6)
                self.assertFalse(state["position_trusted"], state)
                self.assertIsNone(source._origin)
                self.assertIsNone(source.evidence.previous)

    def test_unknown_floor_cannot_create_initial_absolute_anchor(self):
        locator = LayeredMinimapLocator(self.locator, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, locator, "map02")
        self.addCleanup(source.close)
        locate = locator.locate_global(self.frame(), MinimapGeometry.from_frame(self.width, self.height))
        unknown = replace(locate, best=replace(locate.best, tier_id=None))
        with (
            patch.object(locator, "locate_global", return_value=unknown),
            patch.object(locator, "locate_near", return_value=unknown),
        ):
            for now in (1.0, 1.2, 1.4):
                state = source.sample(self.frame(), now=now)
                self.assertFalse(state["position_trusted"])
                self.assertIsNone(source._last_absolute_t)

    def test_short_chain_cannot_continue_across_changed_floor(self):
        source, _ = self._layered_source()
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        locate = source.locator.locate_near(self.frame(508), (127, -100), geometry, search_radius_m=12)
        self.assertIsNotNone(locate)
        candidate = replace(locate.best, tier_id="floor_1")
        result = replace(locate, best=candidate)
        rejected = TrackResult("lost", False, "map competition", result, None, 0, True)
        with patch.object(source.tracker, "update", return_value=rejected):
            state = source.sample(self.frame(508), now=1.4)
        self.assertFalse(state["position_trusted"], state)

    def test_confirmed_floor_transition_starts_a_new_chain(self):
        source, _ = self._layered_source()
        self.addCleanup(source.close)
        source.sample(self.frame(), now=1.4)
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        result = source.locator.locate_near(self.frame(504), (126, -100), geometry)
        candidate = replace(result.best, tier_id="floor_1")
        result = replace(result, best=candidate)
        confirmed = TrackResult("convinced", True, "temporal_promotion", result, candidate, 2, True)
        with patch.object(source.tracker, "update", return_value=confirmed):
            state = source.sample(self.frame(504), now=1.6)
            self.assertFalse(state["position_trusted"])
            self.assertIsNone(source.evidence.previous)
            recovered = source.sample(self.frame(504), now=1.8)
        self.assertTrue(recovered["absolute_position_valid"], recovered)
        self.assertEqual(recovered["tier_id"], "floor_1")
        self.assertEqual(source._last_absolute_t, 1.8)

    def test_clock_rollback_resets_absolute_state(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        source.sample(self.frame(), now=1)
        source.sample(self.frame(), now=1.2)
        self.assertFalse(source.sample(self.frame(), now=0.5)["position_trusted"])

    def test_frame_derived_icon_mask_retains_position_and_cached_geometry(self):
        frame = self.frame()
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        before = self.locator._prepare_template(frame, geometry)
        overlaid = frame.copy()
        x, y = round(geometry.center_x), round(geometry.center_y)
        cv2.rectangle(overlaid, (x + 6, y - 12), (x + 16, y + 12), (255, 255, 255), -1)
        after = self.locator._prepare_template(overlaid, geometry)
        self.assertIsNotNone(after)
        self.assertLess(after.mask.sum(), before.mask.sum())
        self.assertIs(before.mask.flags.writeable, False)
        result = self.locator.locate_global(overlaid, geometry)
        self.assertGreater(result.confidence, 0.85)
        self.assertAlmostEqual(result.best.world_x, 125, delta=0.3)
        self.assertAlmostEqual(result.best.world_z, -100, delta=0.3)

    def test_mostly_occluded_map_fails_closed(self):
        frame = self.frame()
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        x, y, radius = round(geometry.center_x), round(geometry.center_y), round(geometry.outer_radius)
        cv2.rectangle(frame, (x - radius, y - radius), (x + radius, y + radius // 2), (255, 255, 255), -1)
        self.assertIsNone(self.locator._prepare_template(frame, geometry))

    def test_small_icon_coverage_is_masked_without_an_activation_cliff(self):
        frame = self.frame()
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        original = self.locator._prepare_template(frame, geometry)
        x, y = round(geometry.center_x), round(geometry.center_y)
        cv2.circle(frame, (x + 12, y), 1, (255, 255, 255), -1)
        prepared = self.locator._prepare_template(frame, geometry)
        self.assertIsNotNone(prepared)
        self.assertGreater(prepared.mask.sum(), 0.90 * original.mask.sum())
        self.assertLess(prepared.mask.sum(), original.mask.sum())
        result = self.locator.locate_global(frame, geometry)
        self.assertGreater(result.best.full_ncc, 0.98)
        self.assertAlmostEqual(result.best.world_x, 125, delta=0.3)

    def test_sustained_movement_between_slower_frames_can_acquire(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        self.assertFalse(source.sample(self.frame(400), now=1)["position_trusted"])
        # 7 m/s, 0.9s apart: more than the former fixed 5m consistency radius.
        state = source.sample(self.frame(425), now=1.9)
        self.assertTrue(state["position_trusted"], state)


class TestSparseMinimapPosition(unittest.TestCase):
    """Real low-texture HUD + independently calibrated map crop; no game required."""

    def setUp(self):
        root = Path(__file__).parent / "fixtures/minimap"
        self.metadata = json.loads((root / "low_texture_mapping.json").read_text(encoding="utf-8"))
        self.frame = np.zeros((1080, 1920, 3), np.uint8)
        self.frame[:280, :340] = cv2.imread(str(root / "low_texture_hud.png"))
        self.geometry = MinimapGeometry.from_frame(1920, 1080)
        self.base = MinimapMapLocator.from_files(root / "low_texture_map.png", root / "low_texture_mapping.json")
        self.locator = MapSetLocator(SimpleNamespace(), ["map02"])
        self.locator._cache["map02"] = LayeredMinimapLocator(self.base, "map02", root, {"layers": []})
        self.source = VisualPositionSource(
            SimpleNamespace(width=1920, height=1080, active_time=lambda: 0), self.locator, ""
        )
        self.addCleanup(self.source.close)

    def test_sparse_real_hud_acquires_through_map_selection_and_visual_source(self):
        template = self.base._prepare_template(self.frame, self.geometry)
        self.assertIsNotNone(template)
        # Record the actual regression: the former six-sector gate rejects it.
        half = template.ring.shape[0] // 2
        center = (
            half + self.geometry.center_x - round(self.geometry.center_x),
            half + self.geometry.center_y - round(self.geometry.center_y),
        )
        self.assertFalse(_has_minimap_texture(template.ring, center, self.geometry.outer_radius))
        first = self.source.sample(self.frame, now=1)
        self.assertFalse(first["position_trusted"])
        self.assertIsNone(first["x"])
        fixed = self.source.sample(self.frame, now=1.2)
        self.assertTrue(fixed["position_trusted"], fixed)
        self.assertEqual((fixed["map_id"], fixed["tier_id"]), ("map02", "__main__"))
        self.assertAlmostEqual(fixed["x"], self.metadata["expected_world"][0], delta=0.3)
        self.assertAlmostEqual(fixed["z"], self.metadata["expected_world"][1], delta=0.3)
        self.assertGreater(fixed["visual_full_ncc"], self.source.tracker.initial_min_full_ncc)
        self.assertGreaterEqual(fixed["visual_margin"], 0.08)

    def test_sparse_hud_scales_with_resolution(self):
        for width in (1280, 1920, 2560, 3840):
            with self.subTest(width=width):
                frame = cv2.resize(self.frame, (width, width * 9 // 16), interpolation=cv2.INTER_AREA)
                self.assertIsNotNone(
                    self.base._prepare_template(frame, MinimapGeometry.from_frame(*frame.shape[1::-1]))
                )

    def test_sparse_terrain_without_hud_rim_is_rejected_before_map_search(self):
        frame = self.frame.copy()
        yy, xx = np.mgrid[:280, :340]
        distance = np.hypot(xx - self.geometry.center_x, yy - self.geometry.center_y)
        frame[:280, :340][distance > self.geometry.outer_radius * 0.9] = 0
        with patch.object(self.base, "locate_global", side_effect=AssertionError("invalid HUD must not search")):
            state = self.source.sample(frame, now=1)
        self.assertFalse(state["position_trusted"])
        self.assertIn("minimap_insufficient_texture: stage=raw", state["trust_reason"])
        self.assertIn("sectors=2/8", state["trust_reason"])
        self.assertIn("rim_sectors=0/8", state["trust_reason"])

    def test_loading_clears_anchor_and_requires_new_confirmation(self):
        g = self.geometry
        cx, cy = round(g.center_x), round(g.center_y)
        variants = [np.full_like(self.frame, level) for level in (0, 127, 255)]
        logo = variants[0].copy()
        cv2.putText(logo, "EF", (cx - 65, cy - 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 3)
        variants.append(logo)
        # Even the real rim/player marker cannot substitute for terrain.
        rim_only = self.frame.copy()
        yy, xx = np.mgrid[:280, :340]
        distance = np.hypot(xx - g.center_x, yy - g.center_y)
        rim_only[:280, :340][(distance > g.outer_radius * 0.2) & (distance < g.outer_radius * 0.9)] = 90
        variants.append(rim_only)
        for index, loading in enumerate(variants):
            with self.subTest(variant=index):
                self.source.reset()
                self.source.sample(self.frame, now=1)
                self.assertTrue(self.source.sample(self.frame, now=1.2)["position_trusted"])
                with patch.object(self.base, "_coarse_map", side_effect=AssertionError("loading must not search")):
                    for now in (1.4, 1.6, 1.8):
                        state = self.source.sample(loading, now=now)
                        self.assertFalse(state["position_trusted"])
                        self.assertFalse(state["anchor_set"])
                        self.assertIsNone(state["x"])
                        self.assertIn("minimap_insufficient_texture:", state["trust_reason"])
                self.assertFalse(self.source.sample(self.frame, now=2)["position_trusted"])
                self.assertTrue(self.source.sample(self.frame, now=2.2)["position_trusted"])

    def test_distributed_loading_spinner_cannot_acquire_from_texture_alone(self):
        loading = np.zeros_like(self.frame)
        center = (round(self.geometry.center_x), round(self.geometry.center_y))
        cv2.ellipse(loading, center, (50, 50), 0, 20, 320, (180, 180, 180), 5)
        # This decoration passes the original distributed-texture gate. The
        # unchanged map-evidence gates must still reject it, even after a fix.
        self.assertIsNotNone(self.base._prepare_template(loading, self.geometry))
        self.source.sample(self.frame, now=1)
        self.assertTrue(self.source.sample(self.frame, now=1.2)["position_trusted"])
        for i in range(5):
            state = self.source.sample(loading, now=1.4 + i * 0.2)
            self.assertFalse(state["position_trusted"], state)
            self.assertIsNone(state["x"])
            self.assertFalse(state["anchor_set"])

    def test_recorded_loading_huds_clear_tracking(self):
        root = Path(__file__).parent / "fixtures/minimap"
        for index in (1, 2):
            with self.subTest(recording=index):
                self.source.reset()
                self.source.sample(self.frame, now=1)
                self.assertTrue(self.source.sample(self.frame, now=1.2)["position_trusted"])
                loading = np.zeros((1440, 2560, 3), np.uint8)
                loading[:370, :450] = cv2.imread(str(root / f"loading_hud_{index}.png"))
                for now in (1.4, 1.6, 1.8):
                    state = self.source.sample(loading, now=now)
                    self.assertFalse(state["position_trusted"], state)
                    self.assertIsNone(state["x"])
                    self.assertFalse(state["anchor_set"])
                self.assertFalse(self.source.sample(self.frame, now=2)["position_trusted"])
                self.assertTrue(self.source.sample(self.frame, now=2.2)["position_trusted"])


class TestFactoryOcclusionRejection(unittest.TestCase):
    def test_factory_frame_below_original_ncc_gates(self):
        root = Path(__file__).parent / "fixtures/minimap"
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[:280, :340] = cv2.imread(str(root / "factory_occlusion_hud.png"))
        base = MinimapMapLocator.from_files(root / "factory_occlusion_map.png", root / "factory_occlusion_mapping.json")
        locator = MapSetLocator(SimpleNamespace(), ["map02"])
        locator._cache["map02"] = LayeredMinimapLocator(base, "map02", root, {"layers": []})
        source = VisualPositionSource(SimpleNamespace(width=1920, height=1080, active_time=lambda: 0), locator, "")
        self.addCleanup(source.close)
        for now in (1, 1.2, 1.4, 1.6):
            state = source.sample(frame, now=now)
            self.assertFalse(state["position_trusted"], state)
            self.assertIsNone(state["x"])
            self.assertFalse(state["visual_confirmation_pending"])
            self.assertLess(state["visual_confidence"], source.tracker.initial_min_ncc)
            self.assertLess(state["visual_full_ncc"], source.tracker.initial_min_full_ncc)
            self.assertEqual(state["visual_tracking_status"], "lost")


class TestDenseIconMinimapPosition(unittest.TestCase):
    def test_bright_terrain_with_outlined_icons_acquires_with_color_thresholds(self):
        root = Path(__file__).parent / "fixtures/minimap"
        metadata = json.loads((root / "dense_icons_mapping.json").read_text(encoding="utf-8"))
        frame = np.zeros((1080, 1920, 3), np.uint8)
        frame[:280, :340] = cv2.imread(str(root / "dense_icons_hud.png"))
        base = MinimapMapLocator.from_files(root / "dense_icons_map.png", root / "dense_icons_mapping.json")
        locator = MapSetLocator(SimpleNamespace(), ["map02"])
        locator._cache["map02"] = LayeredMinimapLocator(base, "map02", root, {"layers": []})
        source = VisualPositionSource(SimpleNamespace(width=1920, height=1080, active_time=lambda: 0), locator, "")
        self.addCleanup(source.close)
        first = source.sample(frame, now=1)
        self.assertFalse(first["position_trusted"])
        state = source.sample(frame, now=1.2)
        self.assertTrue(state["position_trusted"], state)
        self.assertEqual((state["map_id"], state["tier_id"]), ("map02", "__main__"))
        self.assertGreaterEqual(state["visual_confidence"], source.tracker.initial_min_ncc)
        self.assertGreaterEqual(state["visual_full_ncc"], source.tracker.initial_min_full_ncc)
        self.assertGreaterEqual(state["visual_margin"], 0.08)
        self.assertAlmostEqual(state["x"], metadata["expected_world"][0], delta=0.3)
        self.assertAlmostEqual(state["z"], metadata["expected_world"][1], delta=0.3)
        # Coverage guards also apply when connected antialiasing consumes too
        # much bright terrain and the mask falls back to bright seeds alone.
        geometry = MinimapGeometry.from_frame(1920, 1080)
        template = base._prepare_template(frame, geometry)
        original = next(iter(base._mask_cache.values()))[0]
        half = template.ring.shape[0] // 2
        self.assertGreaterEqual(template.mask.sum(), 0.55 * original.sum())
        for rows in (slice(None, half), slice(half, None)):
            for columns in (slice(None, half), slice(half, None)):
                self.assertGreaterEqual(template.mask[rows, columns].sum(), 0.35 * original[rows, columns].sum())


class TestMapAssetsAndSourceService(unittest.TestCase):
    def test_bundle_paths_reject_paths_outside_resource_root(self):
        from src.localization.calibrated_map_store import resolve_bundle_path

        for name in ("../outside.webp", "/outside.webp", "C:/outside.webp", "maps\\outside.webp", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                resolve_bundle_path(self.root, name)

    def test_bundle_verifier_can_run_as_a_script_without_pythonpath(self):
        import os

        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        script = Path(__file__).resolve().parents[1] / "scripts/nav/verify_localization.py"
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(script), "--help"],
            cwd=self.root,
            env=environment,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertIn(b"--root", result.stdout)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write_map("map02")

        # Windows cannot remove fixtures while a background decoder has them open.
        def workers():
            pool = LocalizationWorkers()
            self.addCleanup(lambda: pool.matches.shutdown(wait=True, cancel_futures=True))
            self.addCleanup(lambda: pool.resources.shutdown(wait=True, cancel_futures=True))
            self.addCleanup(pool.close)
            return pool

        factory = patch("src.localization.localization_workers.LocalizationWorkers", side_effect=workers)
        factory.start()
        self.addCleanup(factory.stop)

    def write_map(self, name):
        directory = self.root / name / "4"
        directory.mkdir(parents=True)
        cv2.imwrite(str(directory / f"{name}_4.png"), np.zeros((20, 30), dtype=np.uint8))
        (directory / f"{name}_4_mapping.json").write_text(
            json.dumps(
                {
                    "matrix": [[0.25, 0, 0], [0, -0.25, 0]],
                    "image_size": {"width": 30, "height": 20},
                }
            ),
            encoding="utf-8",
        )

    def service(self):
        service = object.__new__(WorldPoseService)
        service._executor = SimpleNamespace(method=None)
        service.config = {**DEFAULT_POSITION_CONFIG, CONFIG_VISUAL_MAP_ROOT: str(self.root)}
        service._nav_config = lambda: {NAV_MAP_ID_KEY: "map02"}
        service.active_time = lambda: 10.0
        service.log_warning = Mock()
        service.log_info = Mock()
        service.log_debug = Mock()
        service.info_set = Mock()
        service._init_source_state()
        service._init_runtime_state_mixin(hub=RuntimeStateHub())
        service._read_arrow = lambda frame: (90.0, 1.0)
        return service

    def test_position_ui_updates_without_logging_every_correction_or_motion_change(self):
        service = self.service()
        state = {"x": 1, "z": 2, "map_id": "map02", "anchor_set": True, "position_trusted": True}
        for i in range(20):
            state.update(x=i, rest=bool(i % 2), visual_anchor_chain_correction_m=i / 100)
            service._report_position_status(state, now=i * 0.5)
        self.assertEqual(service.info_set.call_count, 20)
        self.assertTrue(all(c.kwargs == {"log": False} for c in service.info_set.call_args_list))
        service.log_info.assert_called_once()
        lost = {"x": None, "z": None, "anchor_set": False, "position_trusted": False}
        service._report_position_status(lost, now=9.6)
        service._report_position_status(state, now=9.7)
        self.assertEqual(service.log_info.call_count, 3)

    def test_failure_stage_and_measurements_reach_throttled_status_log(self):
        service = self.service()
        frame = np.zeros((1080, 1920, 3), np.uint8)
        source = VisualPositionSource(SimpleNamespace(), MapSetLocator(SimpleNamespace(), ["map02"]), "")
        self.addCleanup(source.close)
        state = source.sample(frame, now=1)
        service._report_position_status(state, now=1)
        message = service.log_info.call_args.args[0]
        self.assertIn("minimap_insufficient_texture: stage=raw sectors=0/8", message)
        self.assertIn("rim_sectors=0/8", message)
        state.update(trust_reason="minimap_insufficient_texture: sectors=1/8")
        service._report_position_status(state, now=1.2)
        self.assertEqual(service.log_info.call_count, 1)
        state.update(
            trust_reason="visual_map_resources_pending: maps=map02",
            odom_reason="visual_map_resources_pending: maps=map02",
        )
        service._report_position_status(state, now=1.4)
        self.assertEqual(service.log_info.call_count, 2)
        self.assertIn("maps=map02", service.log_info.call_args.args[0])

    def test_only_one_map_can_be_selected_automatically(self):
        self.assertEqual(CalibratedMapStore(self.root).load()[0], "map02")
        self.write_map("map01")
        map_id, locator = CalibratedMapStore(self.root).load()
        self.assertEqual(map_id, "")
        self.assertEqual(locator.map_ids, ("map01", "map02"))
        store = CalibratedMapStore(self.root)
        with self.assertRaisesRegex(ValueError, "invalid_visual_map_id"):
            store.load("../map02")

    def test_manifest_selects_webp_and_mixed_bundle_passes_verification(self):
        from scripts.nav.verify_localization import verify

        bundle = self.root / "localization"
        maps = bundle / "maps"
        layers = bundle / "layers"
        maps.mkdir(parents=True)
        layers.mkdir()
        pixels = np.arange(24, dtype=np.uint8).reshape(4, 6) * 11
        color = cv2.merge((pixels, np.flipud(pixels), np.fliplr(pixels)))
        alpha = np.tile(np.array([0, 32, 64, 128, 192, 255], dtype=np.uint8), (4, 1))
        webp_params = [cv2.IMWRITE_WEBP_QUALITY, 90]
        mapping = {
            "matrix": [[0.25, 0, 0], [0, -0.25, 0]],
            "inverse_matrix": [[4, 0, 0], [0, -4, 0]],
            "image_size": {"width": 6, "height": 4},
        }
        base_maps = []
        for map_id, suffix in (("map01", ".png"), ("map02", ".webp")):
            directory = maps / map_id / "4"
            directory.mkdir(parents=True)
            image = directory / f"{map_id}_4{suffix}"
            if suffix == ".webp":
                self.assertTrue(cv2.imwrite(str(image), color, webp_params))
                decoded = cv2.imread(str(image), cv2.IMREAD_UNCHANGED)
                self.assertTrue(np.any(decoded[:, :, 0] != decoded[:, :, 1]))
            else:
                self.assertTrue(cv2.imwrite(str(image), pixels))
            mapping_path = directory / f"{map_id}_4_mapping.json"
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            base_maps.append(
                {
                    "map_id": map_id,
                    "zoom": "4",
                    "image": image.relative_to(bundle).as_posix(),
                    "mapping": mapping_path.relative_to(bundle).as_posix(),
                    "width": 6,
                    "height": 4,
                }
            )
        layer = layers / "tier.webp"
        self.assertTrue(cv2.imwrite(str(layer), np.dstack((color, alpha)), webp_params))
        np.testing.assert_array_equal(cv2.imread(str(layer), cv2.IMREAD_UNCHANGED)[:, :, 3], alpha)
        catalog = {
            "schema_version": 1,
            "layers": [
                {
                    "id": "tier",
                    "map_name": "map02",
                    "map_category": "layered",
                    "path": layer.name,
                    "filename": layer.name,
                    "width": 6,
                    "height": 4,
                    "bounds": {"west": 0, "east": 1.5, "south": -1, "north": 0},
                }
            ],
        }
        (layers / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
        files = [
            {
                "path": path.relative_to(bundle).as_posix(),
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in bundle.rglob("*")
            if path.is_file()
        ]
        manifest = {
            "schema_version": 1,
            "kind": "okef-visual-localization",
            "maps_root": "maps",
            "layers_root": "layers",
            "layer_catalog": "layers/catalog.json",
            "base_maps": base_maps,
            "files": files,
            "coverage": {"layer_count": 1, "independent_layer_count": 0, "missing_logic_ids": []},
        }
        manifest_path = bundle / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        # A leftover PNG at the old path must not take precedence over the manifest.
        self.assertTrue(cv2.imwrite(str(maps / "map02/4/map02_4.png"), np.zeros((6, 8), dtype=np.uint8)))

        store = CalibratedMapStore(maps)
        self.assertEqual(store.available_maps(), ["map01", "map02"])
        _, locator = store.load("map02")
        self.assertEqual(locator.base.map_image.shape[:2], pixels.shape)
        self.assertEqual(locator._load(locator.entries[0]).map_image.shape[:2], pixels.shape)
        _, legacy_png = store.load("map01")
        self.assertEqual(legacy_png.base.map_image.shape[:2], pixels.shape)
        self.assertEqual(verify(bundle)["files"], 6)
        encoded = layer.read_bytes()
        layer.write_bytes(encoded[:-1] + bytes([encoded[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify(bundle)
        layer.write_bytes(encoded)

        webp = maps / "map02/4/map02_4.webp"
        encoded = webp.read_bytes()
        webp.unlink()
        self.assertEqual(store.available_maps(), ["map01"])
        webp.write_bytes(encoded)
        manifest_path.unlink()
        (maps / "map02/4/map02_4.png").unlink()
        self.assertEqual(store.available_maps(), ["map01", "map02"])
        self.assertEqual(store.load("map02")[1].base.map_image.shape[:2], pixels.shape)

    def test_sampler_scope_reuses_owner_and_closes_after_exceptions(self):
        service = self.service()
        service._executor.method = SimpleNamespace(get_frame_for_probe=Mock())
        service._executor.exit_event = threading.Event()
        service._executor.paused = False
        owner = SimpleNamespace(paused=False, enabled=True)
        with patch("src.localization.position_service.VisualPoseSampler") as factory:
            factory.return_value.stopped.is_set.return_value = False
            with self.assertRaisesRegex(RuntimeError, "navigation failed"), service.visual_sampling(owner):
                enabled = factory.call_args.args[2]
                self.assertTrue(enabled())
                owner.paused = True
                self.assertFalse(enabled())
                owner.paused = False
                service._executor.paused = True
                self.assertFalse(enabled())
                service._executor.paused = False
                service.config[CONFIG_USE_EXPERIMENTAL] = True
                self.assertFalse(enabled())
                service.config[CONFIG_USE_EXPERIMENTAL] = False
                self.assertTrue(enabled())
                with service.visual_sampling(owner):
                    factory.assert_called_once()
                raise RuntimeError("navigation failed")
            factory.return_value.close.assert_called_once()
            self.assertIsNone(service._visual_sampler)
            service.config[CONFIG_USE_EXPERIMENTAL] = True
            with service.visual_sampling(owner):
                factory.assert_called_once()

    def test_stopping_sampler_keeps_ownership_until_worker_exits(self):
        service = self.service()
        service._executor.method = SimpleNamespace(get_frame_for_probe=Mock())
        owner = SimpleNamespace(paused=False, enabled=True)
        with patch("src.localization.position_service.VisualPoseSampler") as factory:
            sampler = factory.return_value
            sampler.close.return_value = False
            sampler.thread.is_alive.return_value = True
            with service.visual_sampling(owner):
                pass
            self.assertIs(service._visual_sampler, sampler)
            sampler.stopped.is_set.return_value = True
            from ok.task.exceptions import CaptureException

            with self.assertRaisesRegex(CaptureException, "still stopping"), service.visual_sampling(owner):
                self.fail("navigation must not run without continuous observations")
            factory.assert_called_once()
            sampler.thread.is_alive.return_value = False
            sampler.close.return_value = True
            with service.visual_sampling(owner):
                self.assertEqual(factory.call_count, 2)
            self.assertIsNone(service._visual_sampler)

    def test_next_navigation_waits_for_capture_handoff_and_owns_replacement(self):
        service = self.service()
        entered, release, observed = threading.Event(), threading.Event(), threading.Event()

        def capture_frame(_timeout):
            entered.set()
            release.wait(3)
            return np.zeros((20, 20, 3), np.uint8)

        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=capture_frame)
        original = capture.get_frame
        service._executor.method = capture
        service._executor.exit_event = threading.Event()
        service._executor.paused = False
        service._sample_continuous_visual = lambda *_: observed.set()
        owner = SimpleNamespace(paused=False, enabled=True)
        old = VisualPoseSampler(capture, Mock(), lambda: True, interval=0.01, close_timeout=0.02)
        service._visual_sampler = old
        old.start()
        timer = threading.Timer(0.05, release.set)
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(old.close())
            old.close_timeout = 1.0
            timer.start()

            def create(*args):
                self.assertTrue(old.finished.is_set())
                self.assertIs(capture.get_frame, original)
                return VisualPoseSampler(*args, interval=0.01)

            with (
                patch("src.localization.position_service.VisualPoseSampler", side_effect=create),
                patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
                service.visual_sampling(owner),
            ):
                replacement = service._visual_sampler
                self.assertIsNot(replacement, old)
                self.assertTrue(observed.wait(2))
            self.assertIsNone(service._visual_sampler)
            self.assertFalse(replacement.thread.is_alive())
            self.assertIs(capture.get_frame, original)
        finally:
            release.set()
            if timer.ident is not None:
                timer.join(2)
            old.close()

    def test_sampler_start_failure_releases_service_ownership(self):
        service = self.service()
        service._executor.method = SimpleNamespace(get_frame_for_probe=Mock())
        with patch("src.localization.position_service.VisualPoseSampler") as factory:
            factory.return_value.start.side_effect = RuntimeError("start failed")
            factory.return_value.close.return_value = True
            with self.assertRaisesRegex(RuntimeError, "start failed"), service.visual_sampling(Mock()):
                self.fail("failed startup must not enter navigation")
            factory.return_value.close.assert_called_once()
            self.assertIsNone(service._visual_sampler)

    def test_capture_mode_switch_stops_probes_after_inflight_capture_finishes(self):
        service = self.service()
        entered, release, disabled = threading.Event(), threading.Event(), threading.Event()
        probe = Mock(side_effect=lambda _: (entered.set(), release.wait(3), np.zeros((20, 20, 3), np.uint8))[-1])
        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=probe)
        service._executor.method = capture
        service._executor.exit_event = threading.Event()
        service._executor.paused = False
        service._sample_continuous_visual = Mock()
        owner = SimpleNamespace(paused=False, enabled=True)

        def create(capture, observe, enabled):
            disabled_checks = 0

            def check():
                nonlocal disabled_checks
                result = enabled()
                if not result:
                    disabled_checks += 1
                    if disabled_checks >= 2:
                        disabled.set()
                return result

            return VisualPoseSampler(capture, observe, check, interval=0.01)

        with (
            patch("src.localization.position_service.VisualPoseSampler", side_effect=create),
            patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
            service.visual_sampling(owner),
        ):
            try:
                self.assertTrue(entered.wait(2))
                service.config[CONFIG_USE_EXPERIMENTAL] = True
            finally:
                release.set()
            self.assertTrue(disabled.wait(2))
        probe.assert_called_once()
        service._sample_continuous_visual.assert_not_called()
        self.assertIsNone(service._visual_sampler)

    def test_continuous_observation_failure_resets_partial_source_state(self):
        service = self.service()
        service._visual_sample_lock = threading.RLock()
        service._visual_source = Mock()
        service._sampled_visual_state = {"position_trusted": True}
        service._sample_visual_pose = Mock(side_effect=RuntimeError("observer failed"))
        service.experimental_capture_enabled = lambda: False
        with (
            patch("src.localization.position_service.timing_for_frame", return_value=object()),
            self.assertRaisesRegex(RuntimeError, "observer failed"),
        ):
            service._sample_continuous_visual(np.zeros((20, 20, 3), np.uint8), threading.Event())
        service._visual_source.reset.assert_called_once()
        self.assertIsNone(service._sampled_visual_state)

    def test_continuous_sampler_coalesces_retained_frames_without_restamping(self):
        service = self.service()
        service._visual_source = SimpleNamespace(odometry=SimpleNamespace(_sample_min_dt=0.15))
        service._visual_sampler = object()
        service._sampled_visual_state = {
            "frame_t": 99.8,
            "frame_time_source": "wgc_system_relative_time",
            "position_trusted": True,
            "x": 12,
            "z": 34,
            "absolute_position_valid": False,
        }
        service._locate_visual_frame = Mock(side_effect=AssertionError("must reuse newer measured frame"))
        for stamp in (99.7, 99.8, 99.9):
            timing = SimpleNamespace(frame_time=stamp, source="wgc_system_relative_time")
            with (
                patch("src.localization.position_service.timing_for_frame", return_value=timing),
                patch("src.localization.position_service.time.time", return_value=100),
            ):
                state = service._sample_visual_pose(np.zeros((360, 640, 3), np.uint8))
            self.assertEqual(state["frame_t"], 99.8)
            self.assertEqual(state["x"], 12)
            self.assertTrue(state["visual_duplicate_frame"])
        service._locate_visual_frame.assert_not_called()
        # A newer observed failure takes precedence over a retained good frame.
        service._sampled_visual_state.update(position_trusted=False, x=None, z=None)
        timing = SimpleNamespace(frame_time=99.7, source="wgc_system_relative_time")
        with (
            patch("src.localization.position_service.timing_for_frame", return_value=timing),
            patch("src.localization.position_service.time.time", return_value=100),
        ):
            state = service._sample_visual_pose(np.zeros((360, 640, 3), np.uint8))
        self.assertFalse(state["position_trusted"])
        self.assertIsNone(state["x"])

    def test_continuous_sampler_cannot_keep_stale_coordinates_fresh(self):
        service = self.service()
        service._visual_source = Mock()
        service._visual_source.unavailable.return_value = {"position_trusted": False, "x": None}
        service._visual_sampler = object()
        service._sampled_visual_state = {
            "frame_t": 97,
            "frame_time_source": "wgc_system_relative_time",
            "position_trusted": True,
            "x": 12,
        }
        service._locate_visual_frame = Mock(return_value={"position_trusted": False, "x": None})
        timing = SimpleNamespace(frame_time=97.1, source="wgc_system_relative_time", clock_uncertainty_s=0)
        with (
            patch("src.localization.position_service.timing_for_frame", return_value=timing),
            patch("src.localization.position_service.time.time", return_value=100),
        ):
            state = service._sample_visual_pose(np.zeros((360, 640, 3), np.uint8))
        self.assertFalse(state["position_trusted"])
        service._locate_visual_frame.assert_called_once()
        service._visual_source.unavailable.assert_called_once_with("visual_result_too_old")

    def test_service_has_no_ws_mixin_or_runtime_import(self):
        self.assertFalse(any("Ws" in cls.__name__ for cls in WorldPoseService.__mro__))
        root = Path(__file__).resolve().parents[1] / "src/localization"
        for name in ("position_service.py", "capture_pose_mixin.py", "visual_position_source.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("ws_position", node.module or "")
                    self.assertNotIn("minimap_position_mixin", node.module or "")

    def test_background_single_map_preserves_affine_calibration(self):
        path = self.root / "map02/4/map02_4_mapping.json"
        values = json.loads(path.read_text(encoding="utf-8"))
        values["matrix"] = [[0.25, 0.01, 11], [0.02, -0.25, 12]]
        path.write_text(json.dumps(values), encoding="utf-8")
        mid, locator = CalibratedMapStore(self.root).load("map02", background=True)
        locator.prewarm()
        self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
        self.assertTrue(locator._warm_base_resources())
        self.assertEqual(mid, "map02")
        np.testing.assert_array_equal(locator.calibration.matrix, values["matrix"])

    def test_switch_to_capture_clears_visual_state_and_retains_packet_mode(self):
        service = self.service()
        service.start_minimap_position(wait_stable=False)
        visual = service._visual_source
        self.assertIsNotNone(visual)
        service.config[CONFIG_USE_EXPERIMENTAL] = True
        service.ensure_capture_backend = Mock()
        service._capture_world_pose = lambda **kwargs: {
            "x": 1.0,
            "y": 2.0,
            "z": 3.0,
            "position_trusted": True,
            "sync_seq": 1,
        }
        state = service.sample_world_pose(now=10)
        self.assertEqual(state["x"], 1)
        self.assertIsNone(service._visual_source)
        self.assertFalse(state.get("sync_needed", False))

    def test_missing_maps_publish_untrusted_state_without_ws_fallback(self):
        service = self.service()
        service.config[CONFIG_VISUAL_MAP_ROOT] = str(self.root / "missing")
        state = service.sample_world_pose(now=10)
        self.assertFalse(state["position_trusted"])
        self.assertIsNone(state["x"])
        self.assertTrue(service.minimap_position_ready)

    def test_keyframe_settings_reach_source_and_changes_reinitialize_it(self):
        service = self.service()
        service.config.update(
            {
                CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME: 3,
                CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_EDGES: 2,
                CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_MIN_RESPONSE: 0.7,
                CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT: 17,
            }
        )
        self.assertTrue(service.start_minimap_position(wait_stable=False))
        source = service._visual_source
        self.assertEqual(source.keyframe_interval, 3)
        self.assertEqual(source.keyframe_options["max_edges"], 2)
        self.assertEqual(source.keyframe_options["min_response"], 0.7)
        self.assertEqual(source.keyframe_weight, 17)
        service.config[CONFIG_VISUAL_ANCHOR_CHAIN_KEYFRAME_WEIGHT] = 19
        service.start_minimap_position(wait_stable=False)
        self.assertIsNot(source, service._visual_source)
        self.assertEqual(service._visual_source.keyframe_weight, 19)

    def test_retired_settings_are_backed_up_before_verification(self):
        source = self.root / "Position Config.json"
        old = {"WS等待稳定秒数": 7, CONFIG_USE_EXPERIMENTAL: True, CONFIG_VISUAL_MAP_ROOT: "custom"}
        source.write_text(json.dumps(old), encoding="utf-8")
        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            backup_retired_position_settings()
            source.write_text("{}", encoding="utf-8")
            backup_retired_position_settings()
        backup = self.root / "position_source_migration_backup/v1/Position Config.json"
        self.assertEqual(json.loads(backup.read_text()), old)

    def test_missing_legacy_resource_root_migrates_once_with_backup(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets/localization/maps", CONFIG_USE_EXPERIMENTAL: True, "other": 17}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *parts: str(self.root.joinpath(*parts)),
            ),
            patch("src.localization.position_config_migration.Path.is_dir", return_value=False),
        ):
            self.assertTrue(migrate_visual_map_root())
            self.assertFalse(migrate_visual_map_root())
        expected = {**original, CONFIG_VISUAL_MAP_ROOT: DEFAULT_VISUAL_MAP_ROOT}
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), expected)
        backup = self.root / "position_source_migration_backup/v2/Position Config.json"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

    def test_existing_legacy_resources_remain_selected(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets\\localization\\maps\\"}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *parts: str(self.root.joinpath(*parts)),
            ),
            patch(
                "src.localization.calibrated_map_store.CalibratedMapStore", return_value=CalibratedMapStore(self.root)
            ),
        ):
            self.assertFalse(migrate_visual_map_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
        self.assertFalse((self.root / "position_source_migration_backup/v2").exists())

    def test_auto_discovery_ignores_invalid_directory_names_and_corrupt_mapping(self):
        self.write_map("invalid name")
        self.write_map("broken")
        (self.root / "broken/4/broken_4_mapping.json").write_text("{}", encoding="utf-8")
        self.assertEqual(CalibratedMapStore(self.root).available_maps(), ["map02"])

    def test_empty_existing_legacy_map_directory_migrates_to_packaged_maps(self):
        legacy = self.root / "empty"
        legacy.mkdir()
        source = self.root / "Position Config.json"
        source.write_text(json.dumps({CONFIG_VISUAL_MAP_ROOT: "assets/localization/maps"}), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *p: str(self.root.joinpath(*p)),
            ),
            patch("src.localization.calibrated_map_store.CalibratedMapStore", return_value=CalibratedMapStore(legacy)),
        ):
            self.assertTrue(migrate_visual_map_root())
        self.assertEqual(
            json.loads(source.read_text(encoding="utf-8"))[CONFIG_VISUAL_MAP_ROOT], DEFAULT_VISUAL_MAP_ROOT
        )

    def test_resource_root_migration_preserves_custom_new_and_missing_values(self):
        source = self.root / "Position Config.json"
        for values in (
            {},
            {CONFIG_VISUAL_MAP_ROOT: "custom/maps"},
            {CONFIG_VISUAL_MAP_ROOT: DEFAULT_VISUAL_MAP_ROOT},
            {CONFIG_VISUAL_MAP_ROOT: None},
        ):
            with self.subTest(values=values):
                source.write_text(json.dumps(values), encoding="utf-8")
                with patch(
                    "src.localization.position_config_migration.config_path",
                    side_effect=lambda *parts: str(self.root.joinpath(*parts)),
                ):
                    self.assertFalse(migrate_visual_map_root())
                self.assertEqual(json.loads(source.read_text(encoding="utf-8")), values)

    def test_resource_root_migration_does_not_create_missing_config(self):
        source = self.root / "Position Config.json"
        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            self.assertFalse(migrate_visual_map_root())
        self.assertFalse(source.exists())

    def test_account_override_migration_is_idempotent_and_preserves_other_values(self):
        data = {
            "accounts": {
                "a": {"ItemNavigatorTask": {"定位实现": "旧版兼容", "标记按键": "g"}},
                "b": {"ItemNavigatorTask": {"定位实现": "统一定位"}},
            }
        }
        migrated = migrate_item_position_overrides(data)
        self.assertEqual(migrated["accounts"]["a"]["ItemNavigatorTask"], {"定位实现": "统一定位", "标记按键": "g"})
        self.assertEqual(migrate_item_position_overrides(migrated), migrated)

    def test_actual_framework_value_migration_accepts_two_arguments(self):
        for old in ({"定位实现": "旧版兼容", "按键": "g"}, {"定位实现": "统一定位"}, {}):
            migrated, _ = apply_value_migrations(old.copy(), ItemNavigatorTask.config_value_migrations)
            if "定位实现" in old:
                self.assertEqual(migrated["定位实现"], "统一定位")
            else:
                self.assertEqual(migrated, {})
            if "按键" in old:
                self.assertEqual(migrated["按键"], "g")
            self.assertEqual(
                apply_value_migrations(migrated.copy(), ItemNavigatorTask.config_value_migrations), (migrated, False)
            )

    def test_bad_mapping_and_catalog_fail_closed(self):
        mapping = self.root / "map02/4/map02_4_mapping.json"
        mapping.write_text("{}", encoding="utf-8")
        store = CalibratedMapStore(self.root)
        with self.assertRaises(ValueError):
            store.load("map02")

    def test_resource_failure_during_lazy_search_is_untrusted(self):
        service = self.service()
        service.start_minimap_position(wait_stable=False)
        service._visual_source.sample = Mock(side_effect=ValueError("broken lazy layer"))
        result = service.sample_world_pose(frame=np.zeros((360, 640, 3), np.uint8), now=10)
        self.assertFalse(result["position_trusted"])
        self.assertIsNone(result["x"])


class TestLocalizationWorkerBudget(unittest.TestCase):
    def test_failed_future_does_not_retain_decoded_image_through_traceback(self):
        pool = _ResourceExecutor(budget=DecodeBudget(lambda: 0))
        references = []

        def decode_inner():
            image = np.zeros((32, 32), np.uint8)
            references.append(weakref.ref(image))
            raise ValueError("invalid decoded image")

        def decode():
            try:
                decode_inner()
            except ValueError as exc:
                raise ValueError("invalid decoded image wrapper") from exc

        try:
            future = pool.submit(decode)
            with self.assertRaisesRegex(ValueError, "invalid decoded image"):
                future.result(timeout=3)
            self.assertIsNone(references[0]())
            self.assertIsInstance(future.exception(), ValueError)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_graceful_shutdown_drains_results_without_cancelling_them(self):
        pool = _ResourceExecutor(budget=DecodeBudget(lambda: 0))
        futures = [pool.submit(lambda value=i: value) for i in range(5)]
        pool.shutdown(wait=True)
        self.assertEqual([future.result(timeout=1) for future in futures], list(range(5)))
        self.assertEqual(pool._budget.reserved_bytes, 0)

    def test_task_timeout_error_is_preserved_after_other_results_are_drained(self):
        pool = _ResourceExecutor(2, budget=None)
        try:
            failed = pool.submit(Mock(side_effect=TimeoutError("decoder timeout")))
            completed = pool.submit(lambda: 1)
            with self.assertRaisesRegex(TimeoutError, "decoder timeout"):
                ordered_results([failed, completed], threading.Event())
            self.assertTrue(completed.done())
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_failed_memory_probe_falls_back_to_one_decoder(self):
        budget = DecodeBudget(Mock(side_effect=OSError("unavailable")))
        self.assertTrue(budget.acquire(100))
        self.assertFalse(budget.acquire(100))
        budget.release(100)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_idle_workers_release_completed_results_and_bound_arguments(self):
        budget = DecodeBudget(lambda: 0)
        pool = _ResourceExecutor(budget=budget)
        released = threading.Event()
        original = budget.release

        def record_release(size):
            original(size)
            released.set()

        class Decoder:
            def run(self, pixels):
                return pixels

        budget.release = record_release
        owner = Decoder()
        pixels = np.zeros((32, 32), np.uint8)
        owner_ref, pixels_ref = weakref.ref(owner), weakref.ref(pixels)
        try:
            future = pool.submit(owner.run, pixels)
            future.result(timeout=3)
            self.assertTrue(released.wait(3))
            del owner, pixels, future
            self.assertIsNone(owner_ref())
            self.assertIsNone(pixels_ref())
            self.assertTrue(any(thread.is_alive() for thread in pool._threads))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_memory_admission_allows_one_two_or_four_decoders(self):
        for available, concurrency in ((0, 1), (250, 2), (1000, 4)):
            with self.subTest(available=available):
                budget = DecodeBudget(lambda amount=available: amount, reserve_bytes=0)
                pool = _ResourceExecutor(budget=budget)
                release = threading.Event()
                entered = [threading.Event() for _ in range(4)]

                def decode(i, entered=entered, release=release):
                    entered[i].set()
                    self.assertTrue(release.wait(3))
                    return i

                try:
                    futures = [pool.submit(decode, i, memory_bytes=100) for i in range(4)]
                    for event in entered[:concurrency]:
                        self.assertTrue(event.wait(3))
                    for event in entered[concurrency:]:
                        self.assertFalse(event.wait(0.05))
                    release.set()
                    self.assertEqual([f.result(timeout=3) for f in futures], list(range(4)))
                finally:
                    release.set()
                    pool.shutdown(wait=True, cancel_futures=True)
                self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))
                self.assertFalse(any(thread.is_alive() for thread in pool._threads))

    def test_memory_drop_stops_new_work_and_recovery_expands_again(self):
        memory = [1000]
        budget = DecodeBudget(lambda: memory[0], reserve_bytes=0)
        pool = _ResourceExecutor(budget=budget)
        entered = [threading.Event() for _ in range(8)]
        release = [threading.Event() for _ in range(8)]

        def decode(i):
            entered[i].set()
            self.assertTrue(release[i].wait(3))

        try:
            first = [pool.submit(decode, i, memory_bytes=100) for i in range(4)]
            self.assertTrue(all(event.wait(3) for event in entered[:4]))
            memory[0] = 150
            next_job = pool.submit(decode, 4, memory_bytes=100)
            for event in release[:3]:
                event.set()
            for future in first[:3]:
                future.result(timeout=3)
            self.assertFalse(entered[4].wait(0.1))
            release[3].set()
            self.assertTrue(entered[4].wait(3))
            memory[0] = 1000
            more = [pool.submit(decode, i, memory_bytes=100) for i in range(5, 8)]
            self.assertTrue(all(event.wait(3) for event in entered[5:]))
            for event in release:
                event.set()
            for future in [*first, next_job, *more]:
                future.result(timeout=3)
        finally:
            for event in release:
                event.set()
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertEqual(budget.reserved_bytes, 0)

    def test_retiring_and_new_sources_share_one_decode_budget(self):
        budget = DecodeBudget(lambda: 0)
        first, second = _ResourceExecutor(budget=budget), _ResourceExecutor(budget=budget)
        entered, release, next_entered = threading.Event(), threading.Event(), threading.Event()
        try:
            old = first.submit(lambda: (entered.set(), release.wait(3)))
            self.assertTrue(entered.wait(3))
            first.shutdown(wait=False, cancel_futures=True)
            new = second.submit(next_entered.set)
            self.assertFalse(next_entered.wait(0.1))
            release.set()
            with self.assertRaises(CancelledError):
                old.result(timeout=3)
            new.result(timeout=3)
        finally:
            release.set()
            first.shutdown(wait=True, cancel_futures=True)
            second.shutdown(wait=True, cancel_futures=True)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_close_cancels_queue_discards_late_image_and_releases_memory(self):
        pool = _ResourceExecutor(budget=DecodeBudget(lambda: 0))
        entered, release = threading.Event(), threading.Event()
        references = []

        def decode():
            pixels = np.zeros((32, 32), np.uint8)
            references.append(weakref.ref(pixels))
            entered.set()
            self.assertTrue(release.wait(3))
            return pixels

        try:
            running = pool.submit(decode)
            self.assertTrue(entered.wait(3))
            queued = pool.submit(Mock(side_effect=AssertionError("cancelled work ran")))
            pool.shutdown(wait=False, cancel_futures=True)
            self.assertTrue(queued.cancelled())
            self.assertFalse(wait([queued], timeout=0.1).not_done)
            with self.assertRaises(RuntimeError):
                pool.submit(lambda: None)
            release.set()
            with self.assertRaises(CancelledError):
                running.result(timeout=3)
        finally:
            release.set()
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertIsNone(references[0]())
        self.assertEqual(pool._budget.reserved_bytes, 0)

    def test_decode_failure_releases_reservation_and_next_job_can_run(self):
        budget = DecodeBudget(lambda: 0)
        pool = _ResourceExecutor(budget=budget)
        try:
            failed = pool.submit(Mock(side_effect=ValueError("bad pixels")))
            succeeded = pool.submit(lambda: 42)
            with self.assertRaisesRegex(ValueError, "bad pixels"):
                failed.result(timeout=3)
            self.assertEqual(succeeded.result(timeout=3), 42)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_parallel_rank_uses_four_read_only_jobs_and_matches_serial_results(self):
        pixels = np.random.default_rng(31).integers(0, 255, (64, 64, 3), np.uint8)
        pixels.setflags(write=False)
        index = LayerSearchIndex(tuple((str(i), pixels) for i in range(8)))
        template = SimpleNamespace(flat=pixels[16:32, 16:32])
        expected = index.rank(template, 0.125)
        barrier = threading.Barrier(4)
        original = index._rank_images

        def rank(images, thumbnail):
            self.assertFalse(thumbnail.flags.writeable)
            barrier.wait(timeout=3)
            return original(images, thumbnail)

        pool = _ResourceExecutor(4, budget=None)
        try:
            with patch.object(LayerSearchIndex, "_rank_images", side_effect=rank):
                self.assertEqual(index.rank(template, 0.125, executor=pool), expected)
            threads = tuple(pool._threads)
            for _ in range(4):
                self.assertEqual(index.rank(template, 0.125, executor=pool), expected)
            self.assertEqual(tuple(pool._threads), threads)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def test_failed_ranking_drains_the_other_jobs_before_return(self):
        pool = _ResourceExecutor(2, budget=None)
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def collect():
            try:
                ordered_results(
                    [
                        pool.submit(Mock(side_effect=ValueError("failed rank"))),
                        pool.submit(lambda: (entered.set(), release.wait(3))),
                    ]
                )
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                returned.set()

        thread = threading.Thread(target=collect)
        try:
            thread.start()
            self.assertTrue(entered.wait(3))
            self.assertFalse(returned.wait(0.05))
            release.set()
            self.assertTrue(returned.wait(3))
            self.assertEqual(errors, ["failed rank"])
        finally:
            release.set()
            thread.join(timeout=3)
            pool.shutdown(wait=True, cancel_futures=True)


class TestAutomaticMapCompetition(unittest.TestCase):
    def test_decoding_retains_only_tier_hint_and_validates_the_current_frame(self):
        locator = self.locator()
        floor = SimpleNamespace(
            rank_layers=Mock(return_value=[(1.0, "floor")]),
            locate_proposed_layer=Mock(side_effect=[ResourcePending, self.result(0.99)]),
        )
        locator._cache.update(map02=floor)
        locator._compare_base_maps = Mock(return_value=[("map02", self.result(0.5))])
        self.assertIsNone(locator._locate_cold_layers("old", None, 0))
        result = locator._locate_cold_layers("current", None, 0)
        floor.rank_layers.assert_called_once_with("old", None)
        floor.locate_proposed_layer.assert_called_with("current", None, "floor")
        self.assertEqual(result.best.ncc, 0.99)
        self.assertIsNone(locator._cold_proposal)

    def test_within_map_ambiguity_prioritizes_visual_map_order_without_accepting_it(self):
        locator = self.locator()
        maps = {
            mid: SimpleNamespace(entries=[{}], rank_layers=Mock(side_effect=ResourcePending))
            for mid in ("map01", "map02")
        }
        locator._cache.update(maps)
        locator._frame_gate._prepare_template = Mock(return_value=object())
        locator._warm_base_resources = Mock(return_value=True)
        locator._compare_base_maps = Mock(
            return_value=[("map01", self.result(0.6, 0.01)), ("map02", self.result(0.75, 0.01))]
        )
        self.assertIsNone(locator.locate_global(None, None))
        self.assertEqual(locator._cold_map_order, ("map02", "map01"))
        self.assertIsNone(locator._active)
        maps["map02"].rank_layers.assert_called_once()
        maps["map01"].rank_layers.assert_not_called()
        # Exhausting one map's proposal budget must give the next map a turn.
        maps["map02"].rank_layers.side_effect = None
        maps["map02"].rank_layers.return_value = []
        locator._locate_cold_layers(None, None, 0)
        self.assertEqual(locator._cold_map_cursor, 1)
        locator._locate_cold_layers(None, None, 0)
        maps["map01"].rank_layers.assert_called_once()

    def test_index_proposals_cannot_bypass_other_map_competition_or_confirmation(self):
        for competing_score, accepted in ((0.98, False), (0.5, True)):
            with self.subTest(competing_score=competing_score):
                locator = self.locator()
                floor = SimpleNamespace(
                    entries=[{}],
                    rank_layers=Mock(return_value=[(1.0, "last_floor")]),
                    locate_proposed_layer=Mock(return_value=self.result(0.99)),
                )
                locator._cache.update(map01=SimpleNamespace(), map02=floor)
                locator._cold_layers = True
                locator._frame_gate._prepare_template = Mock(return_value=object())
                locator._warm_base_resources = Mock(return_value=True)
                locator._active_result = Mock(return_value=None)
                locator._compare_base_maps = Mock(
                    return_value=[("map01", self.result(competing_score)), ("map02", self.result(0.6))]
                )
                tracker = MinimapTracker(locator)
                first = tracker.update(None, None, now=1)
                self.assertFalse(first.accepted)
                second = tracker.update(None, None, now=1.2)
                self.assertEqual(second.accepted, accepted)
                if accepted:
                    self.assertEqual(second.position.map_id, "map02")
                    self.assertEqual(second.position.ncc, 0.99)

    def test_pending_index_does_not_repeat_base_search_or_decode_unselected_maps(self):
        locator = self.locator()
        first = SimpleNamespace(rank_layers=Mock(side_effect=ResourcePending))
        second = SimpleNamespace(rank_layers=Mock(return_value=[(0.9, "floor")]))
        locator._cache.update(map01=first, map02=second)
        locator._cold_layers = True
        locator._frame_gate._prepare_template = Mock(return_value=object())
        locator._warm_base_resources = Mock(return_value=True)
        locator._compare_base_maps = Mock(side_effect=AssertionError("warm-up must not redo base searches"))
        for _ in range(3):
            self.assertIsNone(locator.locate_global(None, None))
            self.assertEqual(locator.last_failure_reason, "layer_index_pending")
        self.assertEqual(first.rank_layers.call_count, 3)
        second.rank_layers.assert_not_called()

    def test_exhausted_index_falls_back_to_round_robin_full_scan(self):
        locator = self.locator()
        weak = self.result(0.6, margin=0.01)
        floor = SimpleNamespace(
            entries=[{}],
            base=object(),
            rank_layers=Mock(return_value=[(0.99, "floor")]),
            locate_proposed_layer=Mock(return_value=None),
            locate_global=Mock(return_value=weak),
        )
        locator._cache.update(map01=floor)
        locator._cold_layers = True
        locator._frame_gate._prepare_template = Mock(return_value=object())
        locator._warm_base_resources = Mock(return_value=True)
        locator._compare_base_maps = Mock(return_value=[("map01", weak)])
        self.assertIsNone(locator.locate_global(None, None))
        locator.locate_global(None, None)
        floor.locate_global.assert_called_once_with(None, None, base_result=weak, use_index=False)

    def test_chain_recovery_uses_anchored_map_even_when_active_hint_changes(self):
        locator = self.locator()
        locator._cache.update(
            {
                mid: SimpleNamespace(locate_near=Mock(return_value=self.result()), locate_global=Mock())
                for mid in ("map01", "map02")
            }
        )
        anchored = locator._load("map01")
        other = locator._load("map02")
        locator._active = "map02"
        tracker = MinimapTracker(locator)
        with patch.object(locator, "locate_global", side_effect=AssertionError("cross-map scan")):
            result = tracker.update(None, None, now=10, recovery_world=(10, 10), recovery_map_id="map01")
        self.assertEqual(result.locate.best.map_id, "map01")
        anchored.locate_near.assert_called_once_with(None, (10, 10), None, search_radius_m=80)
        other.locate_near.assert_not_called()
        other.locate_global.assert_not_called()
        # Missing recovery resources cannot silently search another map.
        missing = tracker.update(None, None, now=10.2, recovery_world=(10, 10), recovery_map_id="missing")
        self.assertIsNone(missing.locate)
        self.assertFalse(missing.accepted)

    def test_unselected_map_pending_layers_prevent_acceptance(self):
        locator = self.locator()
        ready = self.result(0.99, margin=0.9)
        pending = replace(self.result(0.6), reason="layer_scan_pending", status="rejected")
        combined = locator._combine([("map01", ready), ("map02", pending)], 0, None)
        self.assertEqual(combined.status, "rejected")
        self.assertEqual(combined.reason, "layer_scan_pending")

    def test_blocked_resource_io_does_not_hold_interpreter_exit(self):
        code = (
            "import threading; from src.localization.localization_workers import LocalizationWorkers; "
            "pool=LocalizationWorkers(); entered=threading.Event(); blocked=threading.Event(); "
            "pool.resources.submit(lambda: (entered.set(),blocked.wait())); "
            "assert entered.wait(3); pool.close()"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_bad_background_map_does_not_starve_another_valid_map(self):
        pool = self.workers()
        valid = SimpleNamespace(locate_global=Mock(return_value=self.result()))

        def load(mid):
            if mid == "map01":
                raise ValueError("corrupt image")
            return mid, valid

        locator = MapSetLocator(SimpleNamespace(load=load), ["map01", "map02"], workers=pool)
        locator.prewarm()
        self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
        self.assertTrue(locator._warm_base_resources())
        self.assertEqual([mid for mid, _ in locator._compare_base_maps(np.zeros((3, 3, 3), np.uint8), None)], ["map02"])

    def workers(self):
        pool = LocalizationWorkers()
        self.addCleanup(lambda: pool.matches.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(lambda: pool.resources.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(pool.close)
        return pool

    def test_background_decode_never_publishes_partial_map_set(self):
        entered, release = threading.Event(), threading.Event()
        pool = self.workers()
        self.addCleanup(release.set)

        def load(mid):
            entered.set()
            self.assertTrue(release.wait(3))
            return mid, SimpleNamespace()

        store = SimpleNamespace(load=Mock(side_effect=load))
        locator = MapSetLocator(store, ["map01", "map02"], workers=pool)
        locator.prewarm()
        self.assertTrue(entered.wait(3))
        self.assertFalse(locator._warm_base_resources())
        self.assertEqual(locator._cache, {})
        release.set()
        self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
        self.assertEqual(locator._cache, {})  # Workers never publish caches.
        self.assertTrue(locator._warm_base_resources())
        self.assertEqual(list(locator._cache), ["map01", "map02"])
        self.assertEqual(store.load.call_count, 2)

    def test_parallel_comparison_overlaps_and_preserves_map_order_and_snapshot(self):
        pool = self.workers()
        barrier = threading.Barrier(2)
        frame = np.full((10, 10, 3), 7, np.uint8)

        def search(snapshot, geometry):
            self.assertFalse(np.shares_memory(snapshot, frame))
            self.assertFalse(snapshot.flags.writeable)
            self.assertTrue(np.all(snapshot == 7))
            barrier.wait(timeout=3)
            return self.result()

        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=pool)
        locator._cache.update({mid: SimpleNamespace(locate_global=search) for mid in locator.map_ids})
        results = locator._compare_base_maps(frame, None)
        self.assertEqual([mid for mid, _ in results], ["map01", "map02"])
        combined = locator._combine(results, 0, None)
        self.assertEqual(combined.status, "ambiguous")
        self.assertEqual(combined.margin, 0)

    def test_failed_parallel_search_drains_other_map_before_returning(self):
        pool = self.workers()
        failed, release, finished, returned = (threading.Event() for _ in range(4))
        self.addCleanup(release.set)

        def fail(*args):
            failed.set()
            raise ValueError("bad map")

        def slow(*args):
            self.assertTrue(release.wait(3))
            finished.set()
            return self.result()

        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=pool)
        locator._cache.update(map01=SimpleNamespace(locate_global=fail), map02=SimpleNamespace(locate_global=slow))
        errors = []

        def compare():
            try:
                locator._compare_base_maps(np.zeros((3, 3, 3), np.uint8), None)
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                returned.set()

        thread = threading.Thread(target=compare)
        thread.start()
        self.assertTrue(failed.wait(3))
        self.assertFalse(returned.is_set())
        release.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, ["bad map"])

    def test_closed_locator_does_not_adopt_late_resource(self):
        pool = self.workers()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def load(mid):
            entered.set()
            self.assertTrue(release.wait(3))
            return mid, SimpleNamespace()

        locator = MapSetLocator(SimpleNamespace(load=load), ["map01"], workers=pool)
        locator.prewarm()
        self.assertTrue(entered.wait(3))
        future = locator._pending_maps["map01"]
        locator.close()
        release.set()
        with self.assertRaises(CancelledError):
            future.result(timeout=3)
        self.assertFalse(locator._warm_base_resources())
        self.assertEqual(locator._cache, {})

    def test_broken_background_map_retries_after_backoff(self):
        pool = self.workers()
        store = SimpleNamespace(load=Mock(side_effect=[ValueError("invalid image"), ("map01", SimpleNamespace())]))
        locator = MapSetLocator(store, ["map01"], workers=pool)
        with patch("src.localization.map_set_locator.time.monotonic", return_value=10):
            locator.prewarm()
            self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
            self.assertFalse(locator._warm_base_resources())
            self.assertFalse(locator._warm_base_resources())
            self.assertEqual(store.load.call_count, 1)
            self.assertEqual(locator._resource_errors, {"map01": "invalid image"})
        with patch("src.localization.map_set_locator.time.monotonic", return_value=16):
            locator.prewarm()
            self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
            self.assertTrue(locator._warm_base_resources())
            self.assertEqual(locator._resource_errors, {})
        self.assertEqual(store.load.call_count, 2)

    def test_full_terrain_separates_a_trimmed_side_lobe(self):
        best = MapCandidate(0, 0, 0, 0, 0.99, full_ncc=0.90)
        side = MapCandidate(20, 0, 5, 0, 0.97, full_ncc=0.77)
        self.assertAlmostEqual(candidate_margin([best, side]), 0.13)
        competitor = MapCandidate(200, 0, 50, 0, 0.98, full_ncc=0.89)
        self.assertAlmostEqual(candidate_margin([best, side, competitor]), 0.01)

    def result(self, score=0.99, margin=None):
        candidate = MapCandidate(40, -40, 10, 10, score, full_ncc=score, tier_id="__main__")
        return LocateResult("accepted", "test", candidate, (candidate,), 0.001, 1, spatial_margin=margin)

    def locator(self):
        return MapSetLocator(SimpleNamespace(), ["map01", "map02"])

    def test_same_xz_in_different_maps_cannot_merge_to_one_match(self):
        locator = self.locator()
        result = locator._combine([("map01", self.result()), ("map02", self.result())], 0, None)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.margin, 0)
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(locator._active)

    def test_hidden_within_map_competition_is_preserved(self):
        result = self.locator()._combine([("map01", self.result(margin=0.01))], 0, None)
        self.assertAlmostEqual(result.margin, 0.01)
        self.assertEqual(result.status, "ambiguous")

    def test_base_comparison_only_expands_the_strong_parent(self):
        locator = self.locator()
        locator._frame_gate._prepare_template = Mock(return_value=object())
        maps = {
            mid: SimpleNamespace(
                base=SimpleNamespace(locate_global=Mock(return_value=self.result(score))),
                locate_global=Mock(return_value=self.result(score)),
            )
            for mid, score in [("map01", 0.6), ("map02", 0.99)]
        }
        locator._cache.update(maps)
        locator._load = lambda mid: maps[mid]
        result = locator.locate_global(None, None)
        self.assertEqual(result.best.map_id, "map02")
        maps["map01"].locate_global.assert_not_called()
        maps["map02"].locate_global.assert_called_once()


class TestVisualLayerCandidates(unittest.TestCase):
    def test_four_parallel_index_decoders_preserve_catalog_order_and_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = self.index_fixture(root)
            entries = [{**entry, "id": str(i)} for i in range(8)]
            expected = LayerSearchIndex.build(root, entries, color=True)
            budget = DecodeBudget(lambda: 1024**3)
            pool = _ResourceExecutor(budget=budget)
            barrier = threading.Barrier(4)
            original = LayerSearchIndex._decode_entry

            def decode(*args):
                barrier.wait(timeout=3)
                return original(*args)

            try:
                with patch.object(LayerSearchIndex, "_decode_entry", side_effect=decode):
                    actual = LayerSearchIndex.build(root, entries, color=True, resources=pool)
                self.assertEqual([name for name, _ in actual.images], [name for name, _ in expected.images])
                for (_, pixels), (_, reference) in zip(actual.images, expected.images, strict=True):
                    np.testing.assert_array_equal(pixels, reference)
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
            self.assertEqual(budget.reserved_bytes, 0)

    def test_parallel_index_matches_serial_and_can_progress_with_one_decode_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = self.index_fixture(root)
            entries = [{**entry, "id": str(i)} for i in range(9)]
            expected = LayerSearchIndex.build(root, entries, color=True)
            pool = LocalizationWorkers()
            pool.resources._budget = DecodeBudget(lambda: 0)
            try:
                job = pool.indexes.submit(
                    LayerSearchIndex.load_or_build,
                    root,
                    entries,
                    color=True,
                    cache_dir=root / "cache",
                    resources=pool.resources,
                    stop=pool.stop,
                )
                actual = job.result(timeout=3)
                self.assertEqual([name for name, _ in actual.images], [name for name, _ in expected.images])
                for (_, image), (_, reference) in zip(actual.images, expected.images, strict=True):
                    np.testing.assert_array_equal(image, reference)
                    self.assertFalse(image.flags.writeable)
            finally:
                pool.close()
                pool.indexes.shutdown(wait=True, cancel_futures=True)
                pool.resources.shutdown(wait=True, cancel_futures=True)

    def test_close_abandons_index_coordinator_without_waiting_for_blocked_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = self.index_fixture(root)
            entries = [{**entry, "id": str(i)} for i in range(8)]
            pool = LocalizationWorkers()
            pool.resources._budget = DecodeBudget(lambda: 0)
            entered, release = threading.Event(), threading.Event()
            original = LayerSearchIndex._decode_entry

            def blocked(*args):
                entered.set()
                self.assertTrue(release.wait(3))
                return original(*args)

            try:
                with patch.object(LayerSearchIndex, "_decode_entry", side_effect=blocked) as decode:
                    job = pool.indexes.submit(
                        LayerSearchIndex.load_or_build,
                        root,
                        entries,
                        color=True,
                        cache_dir=root / "cache",
                        resources=pool.resources,
                        stop=pool.stop,
                    )
                    self.assertTrue(entered.wait(3))
                    pool.close()
                    with self.assertRaises(CancelledError):
                        job.result(timeout=1)
                    self.assertFalse((root / "cache").exists())
                    self.assertEqual(decode.call_count, 1)
                    release.set()
                    pool.resources.shutdown(wait=True, cancel_futures=True)
            finally:
                release.set()
                pool.close()
                pool.indexes.shutdown(wait=True, cancel_futures=True)
                pool.resources.shutdown(wait=True, cancel_futures=True)
            self.assertEqual(pool.resources._budget.reserved_bytes, 0)

    def test_close_releases_layer_cache_and_cancels_queued_resources(self):
        from concurrent.futures import Future

        locator = self.locator()
        pixels = np.zeros((32, 32), np.uint8)
        reference = weakref.ref(pixels)
        locator._cache["floor"] = SimpleNamespace(map_image=pixels)
        locator._cache_bytes = pixels.nbytes
        del pixels
        floor, index = Future(), Future()
        locator._pending_layers["floor"] = floor
        locator._pending_index = index
        locator.close()
        self.assertTrue(floor.cancelled())
        self.assertTrue(index.cancelled())
        self.assertIsNone(reference())
        self.assertEqual(locator._cache_bytes, 0)
        self.assertEqual(locator._pending_layers, {})

    def index_fixture(self, root):
        entry = self.locator().entries[0]
        pixels = np.random.default_rng(24).integers(0, 255, (80, 80, 3), dtype=np.uint8)
        cv2.imwrite(str(root / entry["path"]), pixels)
        return entry

    def test_persisted_index_skips_full_image_decode_and_preserves_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [self.index_fixture(root)]
            initial = LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
            with patch.object(LayerSearchIndex, "build", side_effect=AssertionError("must reuse derived pixels")):
                loaded = LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
            np.testing.assert_array_equal(initial.images[0][1], loaded.images[0][1])
            self.assertFalse(loaded.images[0][1].flags.writeable)

    def test_persisted_index_invalidates_changed_source_bounds_and_color(self):
        for change in ("source", "bounds", "color"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entry = self.index_fixture(root)
                LayerSearchIndex.load_or_build(root, [entry], color=True, cache_dir=root / "cache")
                if change == "source":
                    cv2.imwrite(str(root / entry["path"]), np.zeros((80, 80, 3), np.uint8))
                if change == "bounds":
                    entry["bounds"] = {**entry["bounds"], "east": 40}
                with patch.object(LayerSearchIndex, "build", wraps=LayerSearchIndex.build) as build:
                    LayerSearchIndex.load_or_build(root, [entry], color=change != "color", cache_dir=root / "cache")
                build.assert_called_once()

    def test_damaged_index_or_invalid_pixel_shape_rebuilds(self):
        for damage in ("truncated", "shape", "npy"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                entries = [self.index_fixture(root)]
                LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
                path = next((root / "cache").glob("*.npz"))
                if damage == "truncated":
                    path.write_bytes(b"PK invalid zip")
                elif damage == "npy":
                    with path.open("wb") as handle:
                        np.save(handle, np.zeros((1, 1)))
                else:
                    np.savez(path, signature=LayerSearchIndex._signature(root, entries, True), image_0=np.zeros((1, 1)))
                with patch.object(LayerSearchIndex, "build", wraps=LayerSearchIndex.build) as build:
                    loaded = LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
                build.assert_called_once()
                self.assertEqual(loaded.images[0][1].shape, (10, 10, 3))

    def test_failed_cache_publication_keeps_usable_index_without_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [self.index_fixture(root)]
            with patch("src.localization.layer_search_index.os.replace", side_effect=PermissionError):
                loaded = LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
            self.assertEqual(len(loaded.images), 1)
            self.assertEqual(list((root / "cache").iterdir()), [])

    def test_source_change_during_index_build_does_not_publish_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [self.index_fixture(root)]
            with patch.object(LayerSearchIndex, "_signature", side_effect=["before", "after"]):
                loaded = LayerSearchIndex.load_or_build(root, entries, color=True, cache_dir=root / "cache")
            self.assertEqual(len(loaded.images), 1)
            self.assertFalse((root / "cache").exists())

    def test_overlap_loading_skips_repeat_full_search_then_matches_current_frame(self):
        from concurrent.futures import Future

        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        pending = Future()
        locator._pending_layers["floor1"] = pending
        for _ in range(3):
            with self.assertRaises(ResourcePending):
                locator.locate_proposed_layer("old", None, "floor1")
        floor.locate_global.assert_not_called()
        pending.set_result(floor)
        result = locator.locate_proposed_layer("current", None, "floor1")
        floor.locate_global.assert_called_once_with("current", None)
        self.assertEqual(result.best.tier_id, "floor1")

    def test_single_map_index_advances_after_full_competition_rejects_a_proposal(self):
        locator = self.locator(0.5, 0.99)
        locator.rank_layers = Mock(return_value=[(1.0, "wrong"), (0.9, "floor1")])
        locator.locate_proposed_layer = Mock(side_effect=[self.result(0.5), self.result(0.99, tier="floor1")])
        self.assertEqual(locator.locate_global(None, None).best.ncc, 0.5)
        result = locator.locate_global(None, None)
        self.assertEqual(result.best.tier_id, "floor1")
        self.assertEqual([call.args[2] for call in locator.locate_proposed_layer.call_args_list], ["wrong", "floor1"])

    def test_floor_index_ranks_pixels_instead_of_catalog_order(self):
        rng = np.random.default_rng(77)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = []
            for i in range(16):
                pixels = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
                name = f"floor{i}.png"
                cv2.imwrite(str(root / name), pixels)
                entries.append(
                    {
                        "id": str(i),
                        "path": name,
                        "width": 64,
                        "height": 64,
                        "bounds": {"west": 0, "east": 128, "south": 0, "north": 128},
                    }
                )
            index = LayerSearchIndex.build(root, entries, color=True)
            template = SimpleNamespace(flat=pixels[20:36, 25:41])
            ranks = index.rank(template, 0.125)
            self.assertEqual(ranks[0][1], "15")
            self.assertGreater(ranks[0][0], 0.99)
            self.assertFalse(index.images[0][1].flags.writeable)
            changed = SimpleNamespace(flat=index.images[0][1][20:36, 25:41])
            self.assertEqual(index.rank(changed, 0.125)[0][1], "0")

    def test_closed_resource_pool_cannot_adopt_a_late_floor_index(self):
        from concurrent.futures import Future

        locator = self.locator()
        locator.base._prepare_template = Mock(return_value=object())
        future = Future()
        workers = SimpleNamespace(
            closed=False,
            resources=object(),
            stop=threading.Event(),
            indexes=SimpleNamespace(submit=Mock(return_value=future)),
        )
        locator._resource_workers = workers
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        for _ in range(2):
            with self.assertRaises(ResourcePending):
                locator.rank_layers(frame, None)
        workers.indexes.submit.assert_called_once()
        future.set_result(LayerSearchIndex(()))
        workers.closed = True
        with self.assertRaises(ResourcePending):
            locator.rank_layers(frame, None)
        self.assertIsNone(locator._search_index)

    def test_index_hint_keeps_overlap_and_distant_floor_competitors(self):
        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        alternative = replace(self.result(0.98).best, world_x=1000, world_z=1000)
        best = self.result(0.99)
        floor.locate_global.return_value = replace(best, candidates=(best.best, alternative))
        self.assertIsNone(locator.locate_proposed_layer(None, None, "floor1"))
        # A good full-floor result is still ambiguous against an overlapping floor.
        floor.locate_global.return_value = best
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        locator._cache.update(floor1=floor, floor2=floor)
        result = locator.locate_proposed_layer(None, None, "floor1")
        self.assertIsNone(result.best.tier_id)
        self.assertEqual(result.layer_margin, 0)

    def test_index_hint_waits_for_all_overlapping_resources(self):
        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        locator.entries.append({**locator.entries[0], "id": "floor2"})

        def load(entry):
            if entry["id"] == "floor1":
                return floor
            raise ResourcePending()

        locator._load.side_effect = load
        result = locator.locate_proposed_layer(None, None, "floor1")
        self.assertEqual(result.reason, "layer_scan_pending")
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.margin, 0)

    def test_recovery_checks_only_layers_intersecting_80_meter_circle(self):
        locator = self.locator(0.6, 0.5)
        template = locator.entries[0]
        locator.entries = [
            {**template, "id": "edge", "bounds": {"west": 80, "east": 100, "south": -10, "north": 10}},
            {**template, "id": "outside", "bounds": {"west": 81, "east": 100, "south": -10, "north": 10}},
            {**template, "id": "diagonal", "bounds": {"west": 60, "east": 100, "south": 60, "north": 100}},
        ]
        result = locator.locate_near(None, (0, 0), None, search_radius_m=80)
        self.assertNotEqual(result.reason, "layer_scan_pending")
        self.assertEqual([call.args[0]["id"] for call in locator._load.call_args_list], ["edge"])
        locator.base.locate_global.assert_not_called()
        floor = locator._load.return_value
        floor.locate_global.assert_not_called()
        floor.locate_near.assert_called_once_with(None, (0, 0), None, search_radius_m=80)

    def test_no_nearby_layers_needs_no_layer_resources_even_with_weak_base_match(self):
        locator = self.locator(0.6, 0.99)
        result = locator.locate_near(None, (101, 10), None, search_radius_m=80)
        self.assertEqual(result.best.tier_id, "__main__")
        self.assertNotEqual(result.reason, "layer_scan_pending")
        locator._load.assert_not_called()
        locator.base.locate_global.assert_not_called()
        # Unknown catalog coverage is not proof that the area has no floors.
        locator.tier_coverage_complete = False
        result = locator.locate_near(None, (101, 10), None, search_radius_m=80)
        self.assertIsNone(result.best.tier_id)

    def test_failed_sync_layer_backs_off_without_starving_later_layers(self):
        locator = self.locator(0.99, 0.5)
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        floor = locator._load.return_value
        floor.map_image = np.zeros((30, 30), np.uint8)
        locator._load = LayeredMinimapLocator._load.__get__(locator)

        def decode(entry):
            if entry["id"] == "floor1":
                raise ValueError("bad floor")
            return floor

        locator._decode = Mock(side_effect=decode)
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=10):
            _, complete, pending = locator._layer_candidates(None, None, (10, 10), 80, locator.entries)
        self.assertFalse(complete)
        self.assertTrue(pending)
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=11):
            candidates, complete, pending = locator._layer_candidates(None, None, (10, 10), 80, locator.entries)
        self.assertFalse(complete)  # A broken competitor cannot prove a unique tier.
        self.assertFalse(pending)
        self.assertEqual([item.tier_id for item in candidates], ["floor2"])
        self.assertEqual([call.args[0]["id"] for call in locator._decode.call_args_list], ["floor1", "floor2"])
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=15):
            locator._layer_candidates(None, None, (10, 10), 80, locator.entries)
        self.assertEqual(
            [call.args[0]["id"] for call in locator._decode.call_args_list], ["floor1", "floor2", "floor1"]
        )

    def test_failed_background_layer_is_not_resubmitted_during_backoff(self):
        locator = self.locator()
        pool = LocalizationWorkers()
        self.addCleanup(lambda: pool.resources.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(pool.close)
        locator._resource_workers = pool
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator._decode = Mock(side_effect=ValueError("bad floor"))
        future = pool.resources.submit(locator._decode, locator.entries[0])
        with self.assertRaises(ValueError):
            future.result(timeout=3)
        locator._pending_layers["floor1"] = future
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=10):
            for _ in range(3):
                with self.assertRaises(ValueError):
                    locator._load(locator.entries[0])
        self.assertEqual(locator._decode.call_count, 1)
        self.assertEqual(locator._pending_layers, {})

    def test_pending_background_layer_cannot_prove_uniqueness(self):
        locator = self.locator(0.99, 0.5)
        floor = locator._load.return_value
        floor.map_image = np.zeros((30, 30), np.uint8)
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        entered, release = threading.Event(), threading.Event()
        pool = LocalizationWorkers()
        self.addCleanup(lambda: pool.resources.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(lambda: pool.matches.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(pool.close)
        self.addCleanup(release.set)
        locator._resource_workers = pool

        def decode(entry):
            entered.set()
            self.assertTrue(release.wait(3))
            return floor

        locator._decode = decode
        first = locator.locate_global(None, None)
        self.assertEqual(first.reason, "layer_scan_pending")
        self.assertEqual(first.status, "rejected")
        self.assertEqual(first.margin, 0)
        self.assertTrue(entered.wait(3))
        release.set()
        self.assertFalse(wait(tuple(locator._pending_layers.values()), timeout=3).not_done)
        self.assertEqual(locator._cache, {})
        second = locator.locate_global(None, None)
        self.assertEqual(second.status, "accepted")
        self.assertEqual(second.best.tier_id, "__main__")
        self.assertIn("floor1", locator._cache)

    def test_closed_resource_pool_does_not_run_sync_layer_decode(self):
        locator = self.locator()
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator._resource_workers = SimpleNamespace(closed=True)
        locator._decode = Mock()
        with self.assertRaises(ResourcePending):
            locator._load(locator.entries[0])
        locator._decode.assert_not_called()

    def test_weak_map_scan_does_not_cancel_its_own_slow_layer(self):
        locator = self.locator(0.6, 0.99)
        floor = locator._load.return_value
        floor.map_image = np.zeros((30, 30), np.uint8)
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        entered, release = threading.Event(), threading.Event()
        pool = LocalizationWorkers()
        self.addCleanup(lambda: pool.resources.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(lambda: pool.matches.shutdown(wait=True, cancel_futures=True))
        self.addCleanup(pool.close)
        self.addCleanup(release.set)
        locator._resource_workers = pool

        def decode(entry):
            entered.set()
            self.assertTrue(release.wait(3))
            return floor

        locator._decode = Mock(side_effect=decode)
        locator.locate_global(None, None)
        self.assertTrue(entered.wait(3))
        locator.locate_global(None, None)
        self.assertEqual(locator._scan_index, 0)
        self.assertEqual(list(locator._pending_layers), ["floor1"])
        release.set()
        self.assertFalse(wait(tuple(locator._pending_layers.values()), timeout=3).not_done)
        locator.locate_global(None, None)
        self.assertEqual(locator._scan_index, 1)
        locator._decode.assert_called_once()

    def result(self, score, *, tier=None):
        candidate = MapCandidate(40, -40, 10, 10, score, full_ncc=score, tier_id=tier)
        return LocateResult("accepted", "test", candidate, (candidate,), 0.001, 1)

    def locator(self, base_score=0.95, layer_score=0.95):
        base = MinimapMapLocator(
            np.zeros((30, 30), dtype=np.uint8),
            MapCalibration(np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]])),
            scale_override=1,
        )
        base.locate_global = Mock(return_value=self.result(base_score))
        base.locate_near = Mock(return_value=self.result(base_score))
        entry = {
            "id": "floor1",
            "map_name": "map02",
            "map_category": "layered",
            "path": "floor.png",
            "width": 80,
            "height": 80,
            "bounds": {"west": 0, "east": 20, "south": 0, "north": 20},
        }
        locator = LayeredMinimapLocator(base, "map02", ".", {"layers": [entry]})
        locator._load = Mock(
            return_value=SimpleNamespace(
                locate_global=Mock(return_value=self.result(layer_score)),
                locate_near=Mock(return_value=self.result(layer_score)),
            )
        )
        return locator

    def test_equal_world_coordinates_on_two_tiers_remain_ambiguous(self):
        locator = self.locator()
        result = locator.locate_global(None, None)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.status, "accepted")
        self.assertIsNone(result.best.tier_id)
        self.assertEqual(result.layer_margin, 0)

    def test_old_search_hint_is_revalidated_without_restarting_global_floor_scan(self):
        locator = self.locator(0.99, 0.5)
        locator._recent = (0, (10, 10))
        result = locator.locate_global(None, None)
        self.assertEqual(result.status, "accepted")
        locator.base.locate_near.assert_called_once()
        locator.base.locate_global.assert_not_called()

    def test_weak_old_hint_does_not_prevent_global_relocalization(self):
        locator = self.locator(0.99, 0.5)
        locator._recent = (0, (10, 10))
        locator.base.locate_near.return_value = self.result(0.6)
        result = locator.locate_global(None, None)
        self.assertEqual(result.status, "accepted")
        locator.base.locate_global.assert_called_once()

    def test_stronger_layer_candidate_uses_shared_world_coordinate_frame(self):
        locator = self.locator(0.6, 0.99)
        proposal = locator.locate_global(None, None)
        self.assertIsNone(proposal.best.tier_id)
        result = locator.locate_global(None, None)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.tier_id, "floor1")
        self.assertEqual((result.best.map_x, result.best.map_y), (40, -40))

    def test_missing_layer_cannot_prove_main_tier(self):
        locator = self.locator()
        locator._load.side_effect = ValueError("missing image")
        result = locator.locate_global(None, None)
        self.assertIsNone(result.best.tier_id)

    def test_cold_layers_decode_once_per_sample_and_cannot_publish_early(self):
        locator = self.locator(0.99, 0.5)
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        cold = []
        floor = locator._load.return_value

        def load(entry):
            if entry["id"] not in locator._cache:
                cold.append(entry["id"])
                locator._cache[entry["id"]] = floor
            return floor

        locator._load.side_effect = load
        first = locator.locate_global(None, None)
        self.assertEqual(cold, ["floor1"])
        self.assertEqual(first.reason, "layer_scan_pending")
        self.assertEqual(first.margin, 0)
        second = locator.locate_global(None, None)
        self.assertEqual(cold, ["floor1", "floor2"])
        self.assertEqual(second.status, "accepted")
        self.assertEqual(second.best.tier_id, "__main__")

    def test_display_candidate_limit_does_not_hide_spatial_competition(self):
        locator = self.locator(0.95, 0.94)
        locator.base.top_k = 1
        far = MapCandidate(60, -60, 15, 15, 0.94, full_ncc=0.94)
        base = self.result(0.95)
        locator.base.locate_global.return_value = LocateResult(
            "ambiguous", "test", base.best, (base.best, far), 0.001, 1
        )
        locator.base.locate_near.return_value = locator.base.locate_global.return_value
        result = locator.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "ambiguous")
        self.assertAlmostEqual(result.margin, 0.01)

    def test_pixel_center_resampling_preserves_editor_bounds(self):
        bounds = {"west": -768, "east": 128, "south": -768, "north": 0}
        calibration, (fx, fy) = layer_calibration(bounds, 4200, 3600)
        u, v = 2425.78125, 2205.46875
        x, z = calibration.map_to_world((u + 0.5) * fx - 0.5, (v + 0.5) * fy - 0.5)
        self.assertAlmostEqual(x, -250.5)
        self.assertAlmostEqual(z, -470.5)

    def test_active_overlapping_layers_are_not_evicted_during_warmup(self):
        with tempfile.TemporaryDirectory() as directory:
            cv2.imwrite(str(Path(directory) / "floor.png"), np.zeros((80, 80), np.uint8))
            template = self.locator().entries[0]
            entries = [{**template, "id": f"floor{i}"} for i in range(3)]
            locator = LayeredMinimapLocator(self.locator().base, "map02", directory, {"layers": entries})
            locator._cache_limit_bytes = 1
            locator._pinned_ids = {"floor0", "floor1"}
            locator._load(entries[0])
            locator._load(entries[1])
            self.assertEqual(set(locator._cache), {"floor0", "floor1"})
            # Once the search moves, the previous working set is reclaimable.
            locator._pinned_ids = {"floor2"}
            locator._load(entries[2])
            self.assertEqual(set(locator._cache), {"floor2"})

    def test_different_tier_cannot_refresh_trusted_pose_by_distance_alone(self):
        locator = SimpleNamespace(
            locate_global=Mock(return_value=self.result(0.99, tier="floor1")),
            locate_near=Mock(return_value=self.result(0.99, tier="floor2")),
        )
        tracker = MinimapTracker(locator)
        tracker._set_convinced(self.result(0.99, tier="floor1").best, 1)
        result = tracker.update(None, None, now=1.2)
        self.assertFalse(result.accepted)
        self.assertEqual(result.pending_hits, 1)

    def test_navigation_never_guesses_a_tier_for_visual_pose(self):
        layered = SimpleNamespace(grid=lambda map_id, tier: object())
        tier, _note = GridNavigationMixin._grid_start_tier(
            object(),
            layered,
            "map02",
            None,
            (10, 10),
            label="start",
            known_tier="",
            pose={"tier_evidence_required": True, "tier_trusted": False, "tier_id": None},
        )
        self.assertIsNone(tier)
        tier, _ = GridNavigationMixin._grid_start_tier(
            object(),
            layered,
            "map02",
            None,
            (10, 10),
            label="start",
            known_tier="",
            pose={"tier_evidence_required": True, "tier_trusted": True, "tier_id": "floor1"},
        )
        self.assertEqual(tier, "floor1")


class TestVisualPoseSampler(unittest.TestCase):
    def test_stalled_capture_has_bounded_exit_and_deferred_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        original = Mock()

        def stuck(_timeout):
            entered.set()
            release.wait(3)
            return np.zeros((20, 20, 3), np.uint8)

        capture = SimpleNamespace(get_frame=original, get_frame_for_probe=stuck)
        observe = Mock()
        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01, close_timeout=0.02)
        sampler.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(sampler.close())
            self.assertTrue(sampler.thread.is_alive())
            self.assertIsNot(capture.get_frame, original)
            from ok.task.exceptions import CaptureException

            with self.assertRaises(CaptureException):
                capture.get_frame(busy_wait=0.1)
            self.assertFalse(sampler.finished.is_set())
        finally:
            release.set()
            sampler.thread.join(3)
        self.assertIs(capture.get_frame, original)
        observe.assert_not_called()

    def test_foreground_wait_has_deadline_when_probe_does_not_return(self):
        from ok.task.exceptions import CaptureException

        entered, release = threading.Event(), threading.Event()
        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=lambda _: (entered.set(), release.wait(3)))
        requests = VisualCaptureCoordinator(capture, foreground_timeout=0.02)
        requests.install()
        worker = threading.Thread(target=lambda: requests.get_frame_for_probe(0.08))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(CaptureException, "stalled"):
                capture.get_frame(busy_wait=0.1)
        finally:
            release.set()
            worker.join(3)
            requests.close()

    def test_delayed_probe_does_not_send_real_executor_into_busy_retry(self):
        from ok.device.capture_methods.windows_graphics import WindowsGraphicsCaptureMethod
        from ok.task.TaskExecutor import TaskExecutor

        entered, release, navigating = (threading.Event() for _ in range(3))
        frame = np.zeros((20, 20, 4), np.uint8)
        capture = object.__new__(WindowsGraphicsCaptureMethod)
        capture.exit_event = threading.Event()
        capture.get_frame_lock = threading.RLock()
        capture._size = (0, 0)

        def delayed_capture(frame_deadline=None):
            if frame_deadline is not None:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test did not release probe")
            return frame

        capture._do_get_frame = delayed_capture
        original = capture.get_frame
        executor = SimpleNamespace(
            method=capture,
            exit_event=capture.exit_event,
            reset_scene=Mock(),
            check_enabled=Mock(),
            can_capture=lambda: True,
            blur_overlay_processor=None,
            sleep=Mock(side_effect=AssertionError("probe caused foreground retry sleep")),
        )
        results, errors = [], []

        def navigate():
            navigating.set()
            try:
                results.append(TaskExecutor.next_frame(executor))
            except Exception as exc:
                errors.append(exc)

        sampler = VisualPoseSampler(capture, Mock(), lambda: True, interval=0.01)
        sampler.start()
        worker = threading.Thread(target=navigate)
        try:
            self.assertTrue(entered.wait(2))
            worker.start()
            self.assertTrue(navigating.wait(2))
            # Exceed TaskExecutor's real 0.1 s lock timeout while the probe
            # holds the actual WGC request lock, as slow compositing can do.
            worker.join(0.2)
            self.assertTrue(worker.is_alive())
            sampler.stopped.set()
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(3)
            sampler.close()
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        np.testing.assert_array_equal(results[0], frame[:, :, :3])
        executor.sleep.assert_not_called()
        self.assertEqual(capture.get_frame, original)
        self.assertNotIn("get_frame", vars(capture))

    def test_foreground_capture_blocks_new_probes_and_restores_override(self):
        entered, release = threading.Event(), threading.Event()

        def foreground(*args, **kwargs):
            entered.set()
            release.wait(3)

        capture = SimpleNamespace(get_frame=foreground, get_frame_for_probe=Mock())
        requests = VisualCaptureCoordinator(capture)
        requests.install()
        worker = threading.Thread(target=capture.get_frame)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            from ok.task.exceptions import CaptureBusyException

            with self.assertRaises(CaptureBusyException):
                requests.get_frame_for_probe(0.08)
            capture.get_frame_for_probe.assert_not_called()
        finally:
            release.set()
            worker.join(3)
            requests.close()
        self.assertIs(capture.get_frame, foreground)

    def test_failed_probe_releases_foreground_and_failed_start_restores_capture(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(side_effect=RuntimeError("capture failed"))
        )
        original = capture.get_frame
        requests = VisualCaptureCoordinator(capture)
        requests.install()
        try:
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                requests.get_frame_for_probe(0.08)
            self.assertFalse(requests._probe_active)
            capture.get_frame(busy_wait=0.1)
            original.assert_called_once_with(busy_wait=0.1)
        finally:
            requests.close()
        sampler = VisualPoseSampler(capture, Mock(), lambda: True)
        with (
            patch.object(sampler.thread, "start", side_effect=RuntimeError("start failed")),
            self.assertRaisesRegex(RuntimeError, "start failed"),
        ):
            sampler.start()
        self.assertIs(capture.get_frame, original)

    def test_observation_error_is_logged_and_sampling_continues(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 3), np.uint8))
        )
        continued = threading.Event()
        calls = []

        def observe(frame, stopped):
            calls.append(frame)
            if len(calls) == 1:
                raise ValueError("observer state failed")
            continued.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with (
            patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
            patch("src.localization.visual_pose_sampling.logger.exception") as log,
        ):
            sampler.start()
            try:
                self.assertTrue(continued.wait(2))
            finally:
                sampler.close()
        self.assertGreaterEqual(len(calls), 2)
        log.assert_called_once()

    def test_capture_error_is_logged_and_sampling_continues(self):
        frame = np.zeros((20, 20, 3), np.uint8)
        calls = 0
        continued = threading.Event()

        def get_frame(_timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("capture backend failed")
            continued.set()
            return frame

        capture = SimpleNamespace(get_frame=Mock(), get_frame_for_probe=get_frame)
        sampler = VisualPoseSampler(capture, Mock(), lambda: True, interval=0.01)
        with (
            patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()),
            patch("src.localization.visual_pose_sampling.logger.exception") as log,
        ):
            sampler.start()
            try:
                self.assertTrue(continued.wait(2))
            finally:
                sampler.close()
        log.assert_called_once()

    def test_probe_timing_survives_rgb_slice_into_observation(self):
        frame = np.zeros((20, 20, 4), np.uint8)
        observed = threading.Event()
        timing = FrameTiming(100.0, 1, 100.0, 0.0001)
        capture = SimpleNamespace(
            lock=threading.RLock(),
            get_frame=Mock(),
            get_frame_for_probe=Mock(return_value=frame),
            _localization_timed_image=weakref.ref(frame),
            _localization_frame_timing=timing,
        )

        def observe(sliced, stopped):
            self.assertEqual(sliced.shape, (20, 20, 3))
            self.assertIs(timing_for_frame(capture, sliced), timing)
            observed.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        sampler.start()
        try:
            self.assertTrue(observed.wait(2))
        finally:
            sampler.close()

    def test_sampling_progresses_during_blocked_control_and_stops_on_close(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 4), np.uint8))
        )
        observed = threading.Event()
        calls = []

        def observe(frame, stopped):
            self.assertEqual(frame.shape, (20, 20, 3))
            calls.append(frame)
            if len(calls) >= 3:
                observed.set()

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()):
            sampler.start()
            try:
                # Control performs no world_pose calls during this wait.
                self.assertTrue(observed.wait(2))
            finally:
                sampler.close()
        self.assertFalse(sampler.thread.is_alive())
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(call.args == (0.01,) for call in capture.get_frame_for_probe.call_args_list))

    def test_paused_or_untimed_frames_do_not_create_observations(self):
        capture = SimpleNamespace(
            get_frame=Mock(), get_frame_for_probe=Mock(return_value=np.zeros((20, 20, 3), np.uint8))
        )
        observe = Mock()
        checked = threading.Event()

        def disabled():
            checked.set()
            return False

        sampler = VisualPoseSampler(capture, observe, disabled, interval=0.01)
        sampler.start()
        try:
            self.assertTrue(checked.wait(2))
        finally:
            sampler.close()
        capture.get_frame_for_probe.assert_not_called()
        # A frame without a WGC presentation clock cannot be fed as a new sample.
        done = threading.Event()

        def no_timing(*args):
            done.set()
            return None

        sampler = VisualPoseSampler(capture, observe, lambda: True, interval=0.01)
        with patch("src.localization.visual_pose_sampling.timing_for_frame", side_effect=no_timing):
            sampler.start()
            try:
                self.assertTrue(done.wait(2))
            finally:
                sampler.close()
        observe.assert_not_called()

    def test_cancelled_inflight_capture_cannot_publish_after_scope_exit(self):
        entered, release = threading.Event(), threading.Event()

        def frame(timeout):
            entered.set()
            self.assertTrue(release.wait(2))
            return np.zeros((20, 20, 3), np.uint8)

        observe = Mock()
        sampler = VisualPoseSampler(
            SimpleNamespace(get_frame=Mock(), get_frame_for_probe=frame), observe, lambda: True, interval=0.01
        )
        with patch("src.localization.visual_pose_sampling.timing_for_frame", return_value=object()):
            sampler.start()
            self.assertTrue(entered.wait(2))
            sampler.stopped.set()
            release.set()
            sampler.close()
        observe.assert_not_called()
        self.assertFalse(sampler.thread.is_alive())
