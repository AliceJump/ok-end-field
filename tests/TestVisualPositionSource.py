import ast
import json
import subprocess
import sys
import threading
import time
import unittest
import weakref
from concurrent.futures import CancelledError, Future, wait
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.core.config_migration import apply_value_migrations
from src.core.GridNavConfig import CONFIG_GRID_DIR, DEFAULT_GRID_NAV_CONFIG
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
from src.localization.layered_minimap_locator import LayeredMinimapLocator
from src.localization.localization_workers import (
    DecodeBudget,
    LocalizationWorkers,
    ResourcePending,
    _ResourceExecutor,
    ordered_results,
)
from src.localization.map_set_locator import MapSetLocator, _SearchOutcome
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
from src.localization.native_tile_image import UnpreparedTileOverview
from src.localization.position_config_migration import (
    backup_retired_position_settings,
    migrate_grid_navigation_root,
    migrate_item_position_overrides,
    migrate_visual_map_root,
)
from src.localization.position_service import WorldPoseService
from src.localization.visual_pose_sampling import VisualPoseSampler
from src.localization.visual_position_source import VisualPositionSource
from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask
from tests.localization_bundle_fixture import LocalizationBundleFixture


class TestVisualPositionSource(unittest.TestCase):
    def test_color_locator_accepts_grayscale_frame_with_identical_template(self):
        locator = MinimapMapLocator(cv2.cvtColor(self.image, cv2.COLOR_GRAY2BGR), self.cal, scale_override=1)
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        frame = self.frame()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        expected = locator._prepare_template(frame, geometry)
        actual = locator._prepare_template(gray, geometry)
        self.assertIsNotNone(actual)
        np.testing.assert_array_equal(actual.flat, expected.flat)
        np.testing.assert_array_equal(actual.mask, expected.mask)

    def test_accepted_weak_match_still_publishes_when_chain_correction_is_disabled(self):
        source = VisualPositionSource(self.task, self.locator, "map02", chain_enabled=False)
        self.addCleanup(source.close)
        source.sample(self.frame(), now=1)
        self.assertTrue(source.sample(self.frame(), now=1.2)["position_trusted"])
        geometry = MinimapGeometry.from_frame(self.width, self.height)
        match = source.tracker.update(self.frame(504), geometry, now=1.4)
        self.assertTrue(match.accepted)
        weak = replace(match.locate.best, ncc=0.5, full_ncc=0.5)
        match = replace(match, locate=replace(match.locate, best=weak), position=weak)
        with patch.object(source.tracker, "update", return_value=match):
            state = source.sample(self.frame(504), now=1.4)
        self.assertTrue(state["position_trusted"], state)
        self.assertIsNone(source.chain)
        self.assertEqual(source._last_absolute_t, 1.2)
        with patch.object(source.tracker, "update", return_value=match):
            expired = source.sample(self.frame(504), now=1.2 + source.tracker.stale_after_s + 0.1)
        self.assertFalse(expired["position_trusted"])
        self.assertEqual(expired["trust_reason"], "visual_absolute_anchor_stale")
        self.assertIsNone(source.tracker._convinced)

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

    def test_direct_source_normalizes_non_1280_frame_before_geometry(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        rejection = SimpleNamespace(status="lost", accepted=False, reason="test_rejection", locate=None, pending_hits=0)
        with (
            patch.object(source.locator, "requires_visual_normalization", return_value=True, create=True),
            patch.object(source.tracker, "update", return_value=rejection) as update,
        ):
            source.sample(np.zeros((1080, 1920, 3), np.uint8), now=1)
        self.assertEqual(update.call_args.args[0].shape, (720, 1280, 3))
        self.assertEqual(update.call_args.args[1], MinimapGeometry.from_frame(1280, 720))

    def test_delayed_snapshot_waits_for_a_current_odometry_frame(self):
        source = VisualPositionSource(self.task, self.locator, "map02", chain_enabled=False)
        self.addCleanup(source.close)
        source._initialize_shape((360, 640))
        source._origin = np.zeros(2)
        source._last_absolute_t = 4.0
        candidate = MapCandidate(40, -40, 10, 10, 0.99, full_ncc=0.99)
        locate = LocateResult("accepted", "test", candidate, (candidate,), 0.001, 1, observed_at=4.0)
        match = TrackResult("convinced", True, "stale_async", locate, locate.best, 0, False)
        with (
            patch.object(source.tracker, "update", return_value=match),
            patch.object(source.odometry, "sample", return_value={"sampled": False, "reason": "interval"}),
            patch.object(
                source,
                "_update_absolute_anchor",
                return_value=(np.zeros(2), np.asarray([10.0, 10.0]), True),
            ),
            patch.object(source, "_correct_visual_chains", return_value=(np.zeros(2), None)),
        ):
            state = source.sample(self.frame(), now=5.0)
        self.assertFalse(state["position_trusted"])
        self.assertEqual(state["trust_reason"], "visual_snapshot_awaiting_current_frame")
        self.assertEqual(source._last_absolute_t, 4.0)

    def test_anchor_init_seeds_the_first_visual_chain_anchor(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        self.addCleanup(source.close)
        self.assertFalse(source.sample(self.frame(), now=1.0)["position_trusted"])
        state = source.sample(self.frame(504), now=1.2)
        self.assertTrue(state["position_trusted"], state)
        self.assertEqual(source.odometry.last_result()["reason"], "anchor_init")
        self.assertEqual(len(source.chain._nodes), 1)
        self.assertEqual(len(source.keyframe_chain._nodes), 1)
        self.assertIsNotNone(source.evidence.previous)
        self.assertEqual(source._evidence_result.reason, "absolute_anchor")

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

    def test_too_soon_preserves_fresh_absolute_fix_without_chain_evidence(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        self.addCleanup(source.close)
        source.sample(self.frame(), now=1)
        with patch.object(source.odometry, "sample", return_value={"sampled": False, "reason": "too_soon"}):
            anchored = source.sample(self.frame(), now=1.2)
            self.assertTrue(anchored["absolute_position_valid"])
            self.assertIsNone(source._evidence_result)
            geometry = MinimapGeometry.from_frame(self.width, self.height)
            match = source.tracker.update(self.frame(), geometry, now=1.3)
            weak = replace(match.position, ncc=0.5, full_ncc=0.5)
            match = replace(match, locate=replace(match.locate, best=weak), position=weak)
            with patch.object(source.tracker, "update", return_value=match):
                retained = source.sample(self.frame(), now=1.3)
                self.assertTrue(retained["position_trusted"])
                self.assertTrue(retained["visual_duplicate_frame"])
                self.assertEqual(retained["frame_t"], 1.2)
                self.assertEqual(source._last_absolute_t, 1.2)
                self.assertIsNone(source._evidence_result)
                expired = source.sample(self.frame(), now=1.2 + source.tracker.stale_after_s + 0.1)
                self.assertFalse(expired["position_trusted"])

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

    def _source_with_navigation_topology(self):
        locator = LayeredMinimapLocator(self.locator, "map02", ".", {"layers": []})
        source = VisualPositionSource(self.task, locator, "map02")
        self.addCleanup(source.close)
        scope = SimpleNamespace(
            allows_tier=lambda map_id, tier: True,
            allows_candidate=lambda map_id, tier, x, z: True,
        )
        topology = SimpleNamespace(scope=Mock(return_value=scope))
        source.set_navigation_topology(topology)
        return source, topology

    def test_navigation_topology_requires_a_published_absolute_floor_anchor(self):
        source, topology = self._source_with_navigation_topology()
        first = source.sample(self.frame(), now=1)
        self.assertFalse(first["position_trusted"])
        topology.scope.assert_not_called()
        anchored = source.sample(self.frame(), now=1.2)
        self.assertTrue(anchored["absolute_position_valid"])
        topology.scope.assert_not_called()
        source.sample(self.frame(504), now=1.4)
        topology.scope.assert_called_once()
        args = topology.scope.call_args.args
        self.assertEqual(args[:2], ("map02", "__main__"))
        self.assertAlmostEqual(args[2][0], anchored["x"], delta=0.01)
        self.assertAlmostEqual(args[2][1], anchored["z"], delta=0.01)

    def test_stale_navigation_anchor_does_not_constrain_reacquisition(self):
        source, topology = self._source_with_navigation_topology()
        source.sample(self.frame(), now=1)
        source.sample(self.frame(), now=1.2)
        with patch.object(source.locator, "set_navigation_scope", wraps=source.locator.set_navigation_scope) as setter:
            source.sample(self.frame(504), now=1.2 + source.tracker.stale_after_s + 0.1)
        topology.scope.assert_not_called()
        self.assertIsNone(setter.call_args.args[0])

    def test_navigation_anchor_clears_after_missing_or_invalid_minimap(self):
        for missing in (None, np.zeros((360, 640, 3), dtype=np.uint8)):
            with self.subTest(missing=missing is None):
                source, topology = self._source_with_navigation_topology()
                source.sample(self.frame(), now=1)
                source.sample(self.frame(), now=1.2)
                lost = source.sample(missing, now=1.4)
                self.assertFalse(lost["position_trusted"])
                topology.scope.reset_mock()
                source.sample(self.frame(), now=1.6)
                topology.scope.assert_not_called()

    def test_navigation_anchor_clears_after_clock_rollback_or_explicit_reset(self):
        for rollback in (False, True):
            with self.subTest(rollback=rollback):
                source, topology = self._source_with_navigation_topology()
                source.sample(self.frame(), now=1)
                source.sample(self.frame(), now=1.2)
                if not rollback:
                    source.reset()
                source.sample(self.frame(), now=0.5 if rollback else 1.4)
                topology.scope.assert_not_called()

    def test_discontinuous_odometry_cannot_constrain_a_strong_new_visual_match(self):
        source, topology = self._source_with_navigation_topology()
        source.sample(self.frame(), now=1)
        source.sample(self.frame(), now=1.2)
        measured = {
            "sampled": True,
            "ok": False,
            "reanchored": True,
            "benign_reanchor": False,
            "reason": "motion_discontinuity",
            "dmap_px": (0, 0),
        }
        with (
            patch.object(source.odometry, "sample", return_value=measured),
            patch.object(source.tracker, "update", wraps=source.tracker.update) as update,
        ):
            state = source.sample(self.frame(), now=1.4)
        self.assertIsNone(update.call_args.kwargs["recovery_world"])
        topology.scope.assert_not_called()
        self.assertFalse(state["position_trusted"], state)
        self.assertTrue(source.sample(self.frame(), now=1.6)["absolute_position_valid"])

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
        self.assertAlmostEqual(continued["x"], 126, delta=0.75)

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
                reason = "minimap_insufficient_texture" if failure == "loading" else f"visual_{failure}_lost"
                rejected = TrackResult("lost", False, reason, locate, None, 0, True)
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


def _recorded_map_crop(name):
    root = Path(__file__).parent / "fixtures/minimap"
    metadata = json.loads((root / f"{name}_mapping.json").read_text(encoding="utf-8"))
    left, top, width, height = (int(value) for value in metadata["source_crop"])
    resource_root = Path(__file__).resolve().parents[1] / "assets/nav-grid-data/navigation/localization/maps"
    store = CalibratedMapStore(resource_root, require_prepared=False)
    map_id, layered = store.load("map02", background=False)
    if map_id != "map02":
        layered.close()
        raise AssertionError(f"Expected map02 resource, got {map_id!r}")
    try:
        base = layered.base
        pixels = np.ascontiguousarray(base.map_image[top : top + height, left : left + width])
        matrix = base.calibration.matrix.copy()
    finally:
        layered.close()
    if pixels.shape != (height, width, 3):
        raise AssertionError(f"Map02 source crop {metadata['source_crop']} is outside the published raster")
    matrix[:, 2] += matrix[:, :2] @ np.array([left, top], dtype=np.float64)
    inverse = np.linalg.inv(np.vstack((matrix, [0.0, 0.0, 1.0])))[:2]
    calibration = MapCalibration(
        matrix=matrix,
        inverse_matrix=inverse,
        image_width=width,
        image_height=height,
        map_name=f"map02:{name}",
    )
    return pixels, calibration


def _recorded_minimap(test, name):
    root = Path(__file__).parent / "fixtures/minimap"
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:280, :340] = cv2.imdecode(np.frombuffer((root / f"{name}_hud.png").read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    pixels, calibration = _recorded_map_crop(name)
    base = MinimapMapLocator(pixels, calibration)
    locator = MapSetLocator(SimpleNamespace(), ["map02"])
    locator._cache["map02"] = LayeredMinimapLocator(base, "map02", root, {"layers": []})
    source = VisualPositionSource(SimpleNamespace(width=1920, height=1080, active_time=lambda: 0), locator, "")
    test.addCleanup(source.close)
    return frame, base, locator, source


class TestSparseMinimapPosition(unittest.TestCase):
    """Real low-texture HUD + independently calibrated map crop; no game required."""

    def setUp(self):
        root = Path(__file__).parent / "fixtures/minimap"
        self.metadata = json.loads((root / "low_texture_mapping.json").read_text(encoding="utf-8"))
        self.frame, self.base, self.locator, self.source = _recorded_minimap(self, "low_texture")
        self.geometry = MinimapGeometry.from_frame(1920, 1080)

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
        self.source.reset()
        self.source.sample(self.frame, now=1)
        self.assertTrue(self.source.sample(self.frame, now=1.2)["position_trusted"])
        loading = np.zeros((1440, 2560, 3), np.uint8)
        loading[:370, :450] = cv2.imread(str(root / "loading_hud_1.png"))
        for now in (1.4, 1.6, 1.8):
            state = self.source.sample(loading, now=now)
            self.assertFalse(state["position_trusted"], state)
            self.assertIsNone(state["x"])
            self.assertFalse(state["anchor_set"])
        self.assertFalse(self.source.sample(self.frame, now=2)["position_trusted"])
        self.assertTrue(self.source.sample(self.frame, now=2.2)["position_trusted"])


class TestFactoryOcclusionRejection(unittest.TestCase):
    def test_factory_frame_below_original_ncc_gates(self):
        frame, _base, _locator, source = _recorded_minimap(self, "factory_occlusion")
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
        frame, base, _locator, source = _recorded_minimap(self, "dense_icons")
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


class TestMapAssetsAndSourceService(LocalizationBundleFixture, unittest.TestCase):
    bundle_map_id = "map02"

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
        LocalizationBundleFixture.setUp(self)

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
        base = {
            **self.base,
            "map_id": name,
            "image": f"maps/{name}/4/tiles.json",
            "mapping": f"maps/{name}/4/mapping.json",
        }
        self.write_json(base["mapping"], json.loads((self.root / self.base["mapping"]).read_text()))
        self.write_index(
            base["image"], self.pixels[:, :, :3], {"id": name, "map_id": name, "zoom": "4", "mapping": base["mapping"]}
        )
        self.manifest["base_maps"].append(base)
        self.write_manifest()

    def service(self):
        service = object.__new__(WorldPoseService)
        service._executor = SimpleNamespace(method=None)
        service.config = {**DEFAULT_POSITION_CONFIG, CONFIG_VISUAL_MAP_ROOT: str(self.root / "maps")}
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

    def test_navigation_topology_context_clears_source_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                service = self.service()
                source = Mock(map_id="map02")
                service._visual_source = source
                topology = object()
                provider = Mock(return_value=topology)
                try:
                    with service.navigation_topology(provider):
                        service._locate_visual_frame(np.zeros((10, 10, 3), np.uint8), 1, None)
                        provider.assert_called_once_with("map02")
                        source.set_navigation_topology.assert_called_once_with(topology)
                        if fail:
                            raise InterruptedError("navigation cancelled")
                except InterruptedError:
                    self.assertTrue(fail)
                self.assertIsNone(service._navigation_topology_provider)
                self.assertIsNone(source.set_navigation_topology.call_args.args[0])

    def test_nested_navigation_topology_restores_previous_provider(self):
        service = self.service()
        source = Mock(map_id="map02")
        service._visual_source = source
        outer, inner = Mock(return_value=object()), Mock(return_value=object())
        with service.navigation_topology(outer):
            with service.navigation_topology(inner):
                service._locate_visual_frame(np.zeros((10, 10, 3), np.uint8), 1, None)
                source.set_navigation_topology.assert_called_once_with(inner.return_value)
            self.assertIs(service._navigation_topology_provider, outer)
            service._locate_visual_frame(np.zeros((10, 10, 3), np.uint8), 1.2, None)
            self.assertIs(source.set_navigation_topology.call_args.args[0], outer.return_value)
        self.assertIsNone(service._navigation_topology_provider)
        self.assertIsNone(source.set_navigation_topology.call_args.args[0])

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
        self.assertEqual(CalibratedMapStore(self.root / "maps", require_prepared=False).load()[0], "map02")
        self.write_map("map01")
        map_id, locator = CalibratedMapStore(self.root / "maps", require_prepared=False).load()
        self.assertEqual(map_id, "")
        self.assertEqual(locator.map_ids, ("map01", "map02"))
        store = CalibratedMapStore(self.root / "maps", require_prepared=False)
        with self.assertRaisesRegex(ValueError, "invalid_visual_map_id"):
            store.load("../map02")

    def test_sampler_scope_reuses_owner_and_closes_after_exceptions(self):
        service = self.service()
        service._executor.method = SimpleNamespace(get_frame_for_probe=Mock())
        service._executor.exit_event = threading.Event()
        service._executor.paused = False
        owner = SimpleNamespace(paused=False, enabled=True)
        with patch("src.localization.position_service.VisualPoseSampler") as factory:
            factory.return_value.capture = service._executor.method
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
        service._visual_input_shape = (360, 640)
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

    def test_unprepared_tile_search_width_is_reported_and_clears_tracking(self):
        service = self.service()
        service._visual_source = Mock()
        service._visual_source.sample.side_effect = UnpreparedTileOverview()
        frame = np.zeros((768, 1366, 3), np.uint8)
        state = service._locate_visual_frame(frame, 100, None)
        service._visual_source.reset.assert_called_once()
        service._visual_source.unavailable.assert_called_once_with("visual_search_width_unprepared:1280")
        self.assertIs(state, service._visual_source.unavailable.return_value)

    def test_visual_service_normalizes_only_localization_pixels_and_keeps_frame_time(self):
        service = self.service()
        service._visual_source = Mock()
        native = np.random.default_rng(12).integers(0, 255, (900, 1600, 3), dtype=np.uint8)
        original = native.copy()
        service._locate_visual_frame(native, 100.25, None)
        supplied = service._visual_source.sample.call_args.args[0]
        self.assertEqual(supplied.shape, (720, 1280, 3))
        np.testing.assert_array_equal(supplied, cv2.resize(native, (1280, 720), interpolation=cv2.INTER_AREA))
        np.testing.assert_array_equal(native, original)
        self.assertEqual(service._visual_source.sample.call_args.kwargs, {"now": 100.25})
        service._visual_source.reset.assert_not_called()

    def test_visual_service_preserves_1280_frame_identity_and_native_resolution_reset(self):
        service = self.service()
        service._visual_source = Mock()
        native = np.zeros((720, 1280, 3), np.uint8)
        service._locate_visual_frame(native, 100, None)
        self.assertIs(service._visual_source.sample.call_args.args[0], native)
        service._locate_visual_frame(np.zeros((1080, 1920, 3), np.uint8), 100.2, None)
        service._visual_source.reset.assert_called_once()
        self.assertEqual(service._visual_source.sample.call_args.args[0].shape, (720, 1280, 3))

    def test_visual_normalization_keeps_wgc_time_and_native_arrow_input(self):
        service = self.service()
        service._visual_source = Mock()
        service._visual_source.odometry = None
        service._visual_source.sample.return_value = {"position_trusted": True, "x": 1, "z": 2}
        service._read_arrow = Mock(return_value=(90, 0.9))
        frame = np.zeros((1440, 2560, 3), np.uint8)
        timing = SimpleNamespace(frame_time=100, source="wgc_system_relative_time", clock_uncertainty_s=0.001)
        with (
            patch("src.localization.position_service.timing_for_frame", return_value=timing),
            patch("src.localization.position_service.time.time", return_value=100.1),
        ):
            state = service._sample_visual_pose(frame)
        self.assertEqual(state["frame_t"], 100)
        self.assertEqual(state["frame_time_source"], timing.source)
        self.assertEqual(service._visual_source.sample.call_args.args[0].shape, (720, 1280, 3))
        self.assertIs(service._read_arrow.call_args.args[0], frame)

    def test_empty_visual_frame_fails_closed_without_division_by_zero(self):
        service = self.service()
        service._visual_source = Mock()
        service._locate_visual_frame(np.zeros((0, 0, 3), np.uint8), 100, None)
        service._visual_source.sample.assert_not_called()
        service._visual_source.reset.assert_called_once()
        service._visual_source.unavailable.assert_called_once_with("visual_resource_error:ValueError")

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
        path = self.root / self.base["mapping"]
        values = json.loads(path.read_text(encoding="utf-8"))
        values["matrix"] = [[0.25, 0.01, 11], [0.02, -0.25, 12]]
        self.write_json(self.base["mapping"], values)
        self.write_manifest()
        mid, locator = CalibratedMapStore(self.root / "maps", require_prepared=False).load("map02", background=True)
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
        backup = self.root / "position_source_migration_backup/v3/Position Config.json"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

    def test_resource_read_error_does_not_replace_the_persisted_directory(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets/localization/maps", "other": 7}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *p: str(self.root.joinpath(*p)),
            ),
            patch.object(
                CalibratedMapStore,
                "has_valid_base_metadata",
                side_effect=PermissionError("temporarily unavailable"),
            ),
        ):
            self.assertFalse(migrate_visual_map_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
        self.assertFalse((self.root / "position_source_migration_backup/v3").exists())

    def test_existing_v3_default_resources_remain_selected(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets\\localization\\maps\\"}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *parts: str(self.root.joinpath(*parts)),
            ),
            patch(
                "src.localization.calibrated_map_store.CalibratedMapStore",
                return_value=CalibratedMapStore(self.root / "maps", require_prepared=False),
            ),
        ):
            self.assertFalse(migrate_visual_map_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
        self.assertFalse((self.root / "position_source_migration_backup/v3").exists())

    def test_previous_packaged_visual_root_migrates_to_navigation(self):
        source = self.root / "Position Config.json"
        original = {
            CONFIG_VISUAL_MAP_ROOT: "assets/nav-grid-data/localization/maps",
            CONFIG_USE_EXPERIMENTAL: True,
        }
        source.write_text(json.dumps(original), encoding="utf-8")
        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            self.assertTrue(migrate_visual_map_root())
            self.assertFalse(migrate_visual_map_root())
        expected = {**original, CONFIG_VISUAL_MAP_ROOT: DEFAULT_VISUAL_MAP_ROOT}
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), expected)
        backup = self.root / "position_source_migration_backup/v3/Position Config.json"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)

    def test_grid_root_migrates_without_touching_custom_directories(self):
        source = self.root / "Nav Config.json"
        original = {CONFIG_GRID_DIR: "assets/nav-grid-data", "other": 9}
        source.write_text(json.dumps(original), encoding="utf-8")
        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            self.assertTrue(migrate_grid_navigation_root())
            self.assertFalse(migrate_grid_navigation_root())
        expected = {
            **original,
            CONFIG_GRID_DIR: DEFAULT_GRID_NAV_CONFIG[CONFIG_GRID_DIR],
        }
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), expected)
        self.assertEqual(
            json.loads((self.root / "nav_data_layout_migration/v1/Nav Config.json").read_text(encoding="utf-8")),
            original,
        )

        custom = {CONFIG_GRID_DIR: "D:/custom-navigation"}
        source.write_text(json.dumps(custom), encoding="utf-8")
        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            self.assertFalse(migrate_grid_navigation_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), custom)
        self.assertEqual(
            json.loads((self.root / "nav_data_layout_migration/v1/Nav Config.json").read_text(encoding="utf-8")),
            original,
        )

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
            patch(
                "src.localization.calibrated_map_store.CalibratedMapStore",
                return_value=CalibratedMapStore(legacy, require_prepared=False),
            ),
        ):
            self.assertTrue(migrate_visual_map_root())
        self.assertEqual(
            json.loads(source.read_text(encoding="utf-8"))[CONFIG_VISUAL_MAP_ROOT], DEFAULT_VISUAL_MAP_ROOT
        )

    def test_visual_map_migration_keeps_config_when_backup_fails(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets/nav-grid-data/localization/maps", "other": 7}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *parts: str(self.root.joinpath(*parts)),
            ),
            patch(
                "src.localization.position_config_migration.shutil.copy2",
                side_effect=PermissionError("backup denied"),
            ),
        ):
            self.assertFalse(migrate_visual_map_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)
        self.assertFalse((self.root / "position_source_migration_backup/v3/Position Config.json").exists())
        self.assertEqual(list(self.root.glob("position_source_migration_backup/v3/*.tmp")), [])

    def test_visual_map_migration_keeps_config_when_prepared_is_temporarily_unreadable(self):
        source = self.root / "Position Config.json"
        original = {CONFIG_VISUAL_MAP_ROOT: "assets/localization/maps"}
        source.write_text(json.dumps(original), encoding="utf-8")
        with (
            patch(
                "src.localization.position_config_migration.config_path",
                side_effect=lambda *parts: str(self.root.joinpath(*parts)),
            ),
            patch.object(CalibratedMapStore, "has_valid_base_metadata", return_value=True),
            patch.object(
                CalibratedMapStore,
                "has_prepared",
                side_effect=PermissionError("denied"),
            ) as has_prepared,
        ):
            self.assertFalse(migrate_visual_map_root())
        has_prepared.assert_called_once_with(propagate_os=True)
        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), original)

    def test_visual_map_migration_does_not_trust_manifest_without_base_mapping(self):
        (self.root / self.base["mapping"]).unlink()
        store = CalibratedMapStore(self.root / "maps", require_prepared=False)
        with self.assertRaises((FileNotFoundError, ValueError)):
            store.has_valid_base_metadata()

    def test_base_metadata_normalizes_invalid_mapping_fields(self):
        self.write_json(self.base["mapping"], {})
        self.write_manifest()
        store = CalibratedMapStore(self.root / "maps", require_prepared=False)
        with self.assertRaisesRegex(ValueError, "Invalid visual base map calibration"):
            store.has_valid_base_metadata()
        with self.assertRaises(ValueError):
            store.load("map02")

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

    def test_failed_grid_migration_write_preserves_original_and_can_retry(self):
        source = self.root / "Nav Config.json"
        original = json.dumps({CONFIG_GRID_DIR: "assets/nav-grid-data", "other": "retained"})
        source.write_text(original, encoding="utf-8")

        def fail_after_partial_write(path, values):
            Path(path).write_text("{", encoding="utf-8")
            raise OSError("disk full")

        with patch(
            "src.localization.position_config_migration.config_path",
            side_effect=lambda *parts: str(self.root.joinpath(*parts)),
        ):
            with patch(
                "src.localization.position_config_migration.write_json_file", side_effect=fail_after_partial_write
            ):
                self.assertFalse(migrate_grid_navigation_root())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertTrue(migrate_grid_navigation_root())
        self.assertEqual(json.loads(source.read_text(encoding="utf-8"))["other"], "retained")

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
        mapping = self.root / self.base["mapping"]
        mapping.write_text("{}", encoding="utf-8")
        store = CalibratedMapStore(self.root / "maps", require_prepared=False)
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

    def test_proposal_peak_positions_keep_original_rank_scores_in_serial_and_parallel(self):
        pixels = np.random.default_rng(2033).integers(0, 255, (64, 64, 3), np.uint8)
        pixels.setflags(write=False)
        index = LayerSearchIndex((("floor", pixels),))
        template = SimpleNamespace(flat=pixels[12:28, 20:36])
        expected = index.rank(template, 0.125)
        with_positions = index.rank(template, 0.125, with_positions=True)
        self.assertEqual([(score, tier) for score, tier, _ in with_positions], expected)
        self.assertEqual(with_positions[0][2], (27.5, 19.5))
        pool = _ResourceExecutor(2, budget=None)
        try:
            self.assertEqual(index.rank(template, 0.125, executor=pool, with_positions=True), with_positions)
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
    def layers(self, **values):
        base = MinimapMapLocator(np.zeros((1, 1), np.uint8), MapSetLocator(SimpleNamespace(), []).calibration)
        supplied = values.pop("base", None)
        if supplied is not None:
            base.locate_global = supplied.locate_global
        result = LayeredMinimapLocator(base, "map02", ".", {"layers": []})
        for name, value in values.items():
            setattr(result, name, value)
        return result

    def test_every_map_floor_competes_before_any_hint_is_accepted(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers(), async_search=True)
        order = []
        for mid, score, coarse in (("map01", 0.90, 0.99), ("map02", 0.95, 0.10)):

            def match(*args, mid=mid, score=score, **kwargs):
                order.append(mid)
                return self.result(score)

            locator._cache[mid] = self.layers(
                base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.4))),
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(coarse, "floor")]),
                locate_proposed_layer=Mock(side_effect=match),
            )
        result = locator._search_global_ready(np.zeros((3, 3, 3), np.uint8), None, 0)
        self.assertEqual(order, ["map01", "map02"])
        self.assertEqual(result.best.map_id, "map02")
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(locator._active)

    def test_a_floor_missing_from_the_rank_shortlist_still_competes(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=self.workers(), async_search=True)

        def match_floor(frame, geometry, tier, **kw):
            result = self.result(0.90 if tier == "first" else 0.95)
            candidate = replace(result.best, world_x=0 if tier == "first" else 100, tier_id=tier)
            return replace(result, best=candidate, candidates=(candidate,))

        match = Mock(side_effect=match_floor)
        locator._cache["map01"] = self.layers(
            base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.4))),
            entries=[{"id": "first"}, {"id": "omitted"}],
            rank_layers=Mock(return_value=[(0.99, "first")]),
            locate_proposed_layer=match,
        )
        result = locator._search_global_ready(np.zeros((3, 3, 3), np.uint8), None, 0)
        self.assertEqual([c.args[2] for c in match.call_args_list], ["first", "omitted"])
        self.assertIsNone(locator._active)
        self.assertEqual(result.status, "ambiguous")

    def test_scope_excluded_main_cannot_survive_empty_floor_validation(self):
        for allowed, floor_result in ((False, None), (True, None), (False, self.result(0.95))):
            with self.subTest(main_allowed=allowed, floor_matched=floor_result is not None):
                locator = MapSetLocator(SimpleNamespace(), ["map01"])
                scope = SimpleNamespace(
                    allows_tier=lambda mid, tier, allowed=allowed: tier != "__main__" or allowed,
                    allows_candidate=lambda *args: True,
                )
                locator._cache["map01"] = self.layers(
                    entries=[{"id": "floor"}],
                    rank_layers=Mock(return_value=[(1.0, "floor")]),
                    locate_proposed_layer=Mock(return_value=floor_result),
                )
                locator.set_navigation_scope(scope)
                result = locator._search_snapshot_proposals(None, None, 0, [("map01", self.result(0.99))])
                if not allowed and floor_result is None:
                    self.assertEqual(result.status, "rejected")
                    self.assertIsNone(locator._active)
                else:
                    self.assertIsNotNone(result)
                    self.assertEqual(result.confidence, 0.99 if allowed else 0.95)

    def test_local_entries_do_not_touch_locators_during_background_search(self):
        locator = self.locator()
        locator._active = "map01"
        locator._search_future = Future()
        locator._load = Mock(side_effect=AssertionError("concurrent cache access"))
        for entry in (
            lambda: locator.locate_near(None, (0, 0), None),
            lambda: locator.locate_near(None, (0, 0), None, use_global=True),
            lambda: locator.locate_on_map(None, "map01", (0, 0), None),
        ):
            self.assertIsNone(entry())
            self.assertEqual(locator.last_failure_reason, "visual_global_search_pending")
        locator._search_future.set_result(None)

    def test_snapshot_orders_floor_proposals_across_maps_and_reuses_all_main_competitors(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers(), async_search=True)
        order = []
        for mid, coarse, score in [("map01", 0.56, 0.8), ("map02", 0.78, 0.99)]:

            def match(*args, mid=mid, score=score, **kwargs):
                order.append(mid)
                return self.result(score)

            locator._cache[mid] = self.layers(
                base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.6))),
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(coarse, "floor")]),
                locate_proposed_layer=Mock(side_effect=match),
            )
        frame = np.zeros((3, 3, 3), np.uint8)
        result = locator._search_global_ready(frame, None, 0)
        self.assertEqual(order, ["map02", "map01"])
        self.assertEqual(result.status, "accepted")
        for item in locator._cache.values():
            item.base.locate_global.assert_called_once()
            item.rank_layers.assert_called_once_with(
                frame,
                None,
                wait_for_resources=True,
                fresh_scores=True,
                required_count=1,
                entries=item.entries,
            )
            self.assertTrue(item.locate_proposed_layer.call_args.kwargs["complete_competition"])

    def test_all_declared_floors_are_checked_beyond_the_old_shortlist_limit(self):
        locator = self.locator()
        weak = self.result(0.6, 0.01)
        for mid in locator.map_ids:
            locator._cache[mid] = self.layers(
                base=SimpleNamespace(locate_global=Mock(return_value=weak)),
                entries=[{"id": str(i)} for i in range(10)],
                rank_layers=Mock(return_value=[(0.8 - i * 0.01, str(i)) for i in range(8)]),
                locate_proposed_layer=Mock(return_value=None),
            )
        result = locator._search_global_ready(np.zeros((3, 3, 3), np.uint8), None, 0)
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(locator._active)
        for item in locator._cache.values():
            self.assertEqual(
                {call.args[2] for call in item.locate_proposed_layer.call_args_list}, {str(i) for i in range(10)}
            )
            item.base.locate_global.assert_called_once()

    def test_snapshot_keeps_other_map_competitors_and_does_not_publish_proposal_scores(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers(), async_search=True)
        locator._cache.update(
            map01=SimpleNamespace(locate_global=Mock(return_value=self.result(0.98, 0.01)), entries=[]),
            map02=self.layers(
                base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.6, 0.01))),
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(100.0, "floor")]),
                locate_proposed_layer=Mock(return_value=self.result(0.99)),
            ),
        )
        result = locator._search_global_ready(np.zeros((3, 3, 3), np.uint8), None, 0)
        self.assertEqual(result.status, "ambiguous")
        self.assertLess(result.margin, 0.02)
        self.assertEqual(result.confidence, 0.99)
        self.assertIsNone(locator._active)

    def test_snapshot_background_miss_restores_raw_candidates_on_the_same_frame(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=self.workers(), async_search=True)
        floor = self.layers(
            base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.6, 0.01))),
            entries=[{"id": "wrong"}, {"id": "right"}],
            rank_layers=Mock(return_value=[(0.9, "wrong"), (0.8, "right")]),
            locate_proposed_layer=Mock(side_effect=[None, self.result(0.99)]),
        )
        locator._cache["map01"] = floor
        frame = np.zeros((3, 3, 3), np.uint8)
        result = locator._search_global_ready(frame, None, 0)
        self.assertEqual(result.status, "accepted")
        self.assertEqual([c.args[2] for c in floor.locate_proposed_layer.call_args_list], ["wrong", "right"])
        floor.base.locate_global.assert_called_once()

    def test_snapshot_later_floor_cannot_erase_an_earlier_ambiguous_competitor(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers(), async_search=True)
        locator._cache.update(
            map01=SimpleNamespace(locate_global=Mock(return_value=self.result(0.98)), entries=[]),
            map02=self.layers(
                base=SimpleNamespace(locate_global=Mock(return_value=self.result(0.97))),
                entries=[{"id": "first"}, {"id": "second"}],
                rank_layers=Mock(return_value=[(1.0, "first"), (0.9, "second")]),
                locate_proposed_layer=Mock(side_effect=[self.result(0.99), self.result(0.8)]),
            ),
        )
        result = locator._search_global_ready(np.zeros((3, 3, 3), np.uint8), None, 0)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.confidence, 0.99)
        self.assertLess(result.margin, 0.02)
        self.assertIsNone(locator._active)

    def test_immutable_job_reuses_base_competitors_but_the_next_job_recomputes_them(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers(), async_search=True)
        other = SimpleNamespace(locate_global=Mock(side_effect=[self.result(0.5), self.result(0.4)]))
        base = SimpleNamespace(locate_global=Mock(return_value=self.result(0.75, 0.01)))
        floor = self.layers(
            base=base,
            entries=[{"id": "floor"}],
            rank_layers=Mock(return_value=[(1.0, "floor")]),
            locate_proposed_layer=Mock(return_value=self.result(0.99)),
        )
        locator._cache.update(map01=other, map02=floor)
        for i in range(2):
            frame = np.full((3, 3, 3), i, np.uint8)
            frame.setflags(write=False)
            locator.reset_tracking()
            result = locator._search_global_ready(frame, None, 0)
            self.assertEqual(result.status, "accepted")
            self.assertEqual(result.best.map_id, "map02")
            self.assertEqual(other.locate_global.call_count, i + 1)
            self.assertEqual(base.locate_global.call_count, i + 1)
        self.assertNotEqual(
            other.locate_global.call_args_list[0].args[0][0, 0, 0],
            other.locate_global.call_args_list[1].args[0][0, 0, 0],
        )

    def test_reused_same_frame_competitor_still_blocks_ambiguous_floor(self):
        locator = self.locator()
        locator._cache.update(
            map01=SimpleNamespace(),
            map02=self.layers(
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(1.0, "floor")]),
                locate_proposed_layer=Mock(return_value=self.result(0.99)),
            ),
        )
        locator._compare_base_maps = Mock(side_effect=AssertionError("duplicate same-frame search"))
        result = locator._search_snapshot_proposals(None, None, 0, [("map01", self.result(0.98))])
        self.assertEqual(result.status, "ambiguous")
        self.assertIsNone(locator._active)

    def test_large_map_is_submitted_first_but_all_results_keep_original_order(self):
        pool = LocalizationWorkers(match_workers=1)
        self.addCleanup(pool.close)
        self.addCleanup(lambda: pool.matches.shutdown(wait=True))
        locator = MapSetLocator(SimpleNamespace(), ["small", "medium", "large"], workers=pool)
        executed = []

        def base(mid, edge):
            def search(*_):
                executed.append(mid)
                return self.result()

            return SimpleNamespace(map_image=SimpleNamespace(shape=(edge, edge, 3)), locate_global=search)

        locator._cache.update(small=base("small", 10), medium=base("medium", 20), large=base("large", 30))
        results = locator._compare_base_maps(np.zeros((3, 3, 3), np.uint8), None)
        self.assertEqual(executed, ["large", "medium", "small"])
        self.assertEqual([mid for mid, _ in results], ["small", "medium", "large"])
        self.assertEqual(locator._combine(results, 0, None).status, "ambiguous")

    def test_explicit_reset_discards_floor_order_and_background_hints(self):
        locator = self.locator()
        reset = Mock()
        layered = SimpleNamespace(_recent=(1, (10, 10)), reset_proposals=reset)
        locator._cache["map02"] = layered
        locator._active = "map02"
        locator.reset_tracking()
        reset.assert_called_once()
        self.assertIsNone(layered._recent)
        self.assertIsNone(locator._active)

    def async_locator(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=self.workers(), async_search=True)
        self.addCleanup(locator.close)
        locator._warm_base_resources = Mock(return_value=True)
        locator._prepare_base_search = Mock(return_value=True)
        locator._frame_gate = SimpleNamespace(_prepare_template=Mock(return_value=True), last_failure_reason="invalid")
        locator._cache["map01"] = SimpleNamespace(close=Mock())
        return locator

    def test_fresh_frame_submits_complete_competition_in_background(self):
        locator = self.async_locator()
        old = np.zeros((4, 4, 3), np.uint8)
        fresh = np.ones_like(old)
        completed = Future()
        completed.set_result(self.result(0.99, margin=0.9))
        replacement = Future()
        submit = Mock(return_value=replacement)
        locator._workers = SimpleNamespace(closed=False, close=Mock(), acquisitions=SimpleNamespace(submit=submit))
        locator._prepare_base_search = Mock(return_value=True)
        locator._search_future = completed
        locator._search_shape = old.shape
        locator._search_started_at = time.monotonic() - 3
        locator._search_started_generation = locator._search_generation
        locator._search_frame = old.copy()
        locator._search_frame.setflags(write=False)
        locator._active = "map01"
        self.assertIsNone(locator._locate_async(fresh, None, 0))
        submit.assert_called_once()
        submitted = submit.call_args.args[1]
        self.assertFalse(submitted.flags.writeable)
        self.assertFalse(np.shares_memory(submitted, fresh))
        np.testing.assert_array_equal(submitted, fresh)
        self.assertIs(locator._search_future, replacement)

    def test_identical_async_snapshot_is_reused_without_a_second_search(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        completed = self.result(0.99, margin=0.2)
        future = Future()
        future.set_result(completed)
        locator._search_future = future
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_frame = frame.copy()
        locator._search_frame.setflags(write=False)
        locator._search_started_at = 0.0
        locator._active = "map01"
        with patch("src.localization.map_set_locator.time.monotonic", return_value=1.0):
            self.assertIs(locator._locate_async(frame, None, 0), completed)
        self.assertIsNone(locator._search_future)
        self.assertIsNone(locator._search_frame)

    def test_navigation_scope_cleared_while_search_pending_discards_its_result(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        locator.set_navigation_scope(SimpleNamespace(search_identity=lambda: ("map01", "floor1", 1.0, 2.0)))
        locator._search_started_at = time.monotonic()
        future = Future()  # still running when the scope is cleared
        locator._search_future = future
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_frame = frame.copy()
        locator._search_frame.setflags(write=False)
        locator.set_navigation_scope(None)
        future.set_result(self.result(0.99, margin=0.9))
        locator._prepare_base_search = Mock(return_value=False)
        # Same pixels, later sample: the stale completed result must not be reused.
        self.assertIsNone(locator._locate_async(frame.copy(), None, 0))
        locator._prepare_base_search.assert_called_once()

    def test_stale_completed_search_cannot_select_a_map_for_local_matching(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        for age, expected_local in ((0.5, True), (3, False)):
            with self.subTest(age=age):
                future = Future()
                future.set_result(_SearchOutcome(self.result(0.99, margin=0.2), "", 123.5, "map01"))
                locator._search_future = future
                locator._search_shape = frame.shape
                locator._search_started_at = time.monotonic() - age
                with patch.object(locator, "locate_global") as global_match, patch.object(locator, "_load") as load:
                    load.return_value.locate_near.return_value = None
                    load.return_value.last_failure_reason = ""
                    locator.locate_near(frame, (0, 0), None)
                self.assertEqual(load.called, expected_local)
                self.assertEqual(global_match.called, not expected_local)

    def test_cross_map_competition_includes_hidden_runner_up(self):
        locator = self.locator()
        first = self.result(0.80, margin=0.30)
        first_peak = replace(first.best, full_ncc=0.99, world_x=0)
        second = self.result(0.79, margin=0.3)
        visible = replace(second.best, full_ncc=0.60, world_x=5)
        hidden = replace(second.best, ncc=0.78, full_ncc=0.98, world_x=50)
        result = locator._combine(
            [
                ("map01", replace(first, best=first_peak, candidates=(first_peak,))),
                (
                    "map02",
                    replace(second, best=visible, candidates=(visible,), competition_candidates=(visible, hidden)),
                ),
            ],
            0,
            None,
        )
        self.assertAlmostEqual(result.margin, 0.01)
        self.assertNotEqual(result.status, "accepted")

    def test_normalization_updates_after_installing_prepared_resources(self):
        store = SimpleNamespace(has_prepared=Mock(side_effect=[False, True]))
        locator = MapSetLocator(store, ["map01"])
        self.assertFalse(locator.requires_visual_normalization())
        self.assertTrue(locator.requires_visual_normalization())
        self.assertTrue(locator.requires_visual_normalization())
        self.assertEqual(store.has_prepared.call_count, 2)

    def test_navigation_scope_change_invalidates_completed_async_result(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        future = Future()
        future.set_result(self.result(0.99, margin=0.9))
        scope = SimpleNamespace(search_identity=lambda: ("map01", "floor1", 1.0, 2.0))
        locator.set_navigation_scope(scope)
        locator._search_future = future
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_frame = frame.copy()
        locator._search_frame.setflags(write=False)
        locator.set_navigation_scope(None)
        locator._prepare_base_search = Mock(return_value=False)
        self.assertIsNone(locator._locate_async(frame, None, 0))
        locator._prepare_base_search.assert_called_once()

    def test_async_failure_settles_a_scope_change_arrived_while_running(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        locator.set_navigation_scope(SimpleNamespace(search_identity=lambda: ("map01", "floor1", 1.0, 2.0)))
        future = Future()
        locator._search_future = future
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_frame = frame.copy()
        locator._search_frame.setflags(write=False)
        locator._active = "map01"
        locator.set_navigation_scope(None)
        future.set_exception(ValueError("broken competitor"))
        locator._prepare_base_search = Mock(return_value=False)
        self.assertIsNone(locator._locate_async(frame, None, 0))
        locator._prepare_base_search.assert_called_once()
        self.assertFalse(locator._scope_change_pending)
        self.assertIsNone(locator._active)

    def test_sync_reset_does_not_clear_the_active_map_as_a_scope_change(self):
        locator = self.async_locator()
        locator._search_started_generation = locator._search_generation
        locator.reset_tracking()
        locator._active = "map01"
        locator._settle_search_scope()
        self.assertEqual(locator._active, "map01")

    def test_completed_async_resource_error_is_preserved_for_the_caller(self):
        locator = self.async_locator()
        frame = np.zeros((16, 16, 3), np.uint8)
        future = Future()
        future.set_result(None)
        locator._search_future = future
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_frame = frame.copy()
        locator._search_frame.setflags(write=False)
        locator._last_failure_reason = "layer_resource_error: floor1: missing mask"
        self.assertIsNone(locator._locate_async(frame, None, 0))
        self.assertEqual(locator.last_failure_reason, "layer_resource_error: floor1: missing mask")

    def test_reset_during_async_search_discards_its_late_hint(self):
        locator = self.async_locator()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        frame = np.zeros((4, 4, 3), np.uint8)

        def search(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            locator._active = "map01"

        locator._search_global_ready = Mock(side_effect=search)
        locator.locate_global(frame, None)
        self.assertTrue(entered.wait(3))
        locator.reset_tracking()
        release.set()
        locator._search_future.result(timeout=3)
        locator._prepare_base_search = Mock(return_value=False)
        self.assertIsNone(locator.locate_global(frame, None))
        self.assertIsNone(locator._active)

    def test_closing_async_search_defers_cache_cleanup_and_discards_result(self):
        locator = self.async_locator()
        notify = Mock()
        locator.set_search_ready_callback(notify)
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        locator._cache["map01"].close.side_effect = closed.set

        def search(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.result()

        locator._search_global_ready = search
        locator.locate_global(np.zeros((4, 4, 3), np.uint8), None)
        self.assertTrue(entered.wait(3))
        future = locator._search_future
        locator.close()
        self.assertFalse(closed.is_set())
        release.set()
        with self.assertRaises(CancelledError):
            future.result(timeout=3)
        self.assertTrue(closed.wait(3))
        notify.assert_not_called()
        self.assertFalse(locator._cache)

    def test_already_finished_search_still_notifies_for_a_fresh_frame(self):
        locator = self.async_locator()
        ready = threading.Event()
        locator.set_search_ready_callback(ready.set)
        locator._search_global_ready = Mock(return_value=self.result(0.99, margin=0.9))
        self.assertIsNone(locator.locate_global(np.zeros((4, 4, 3), np.uint8), None))
        self.assertTrue(ready.wait(3))
        self.assertIsNone(locator._active)

    def test_async_resource_failure_is_not_a_valid_hint(self):
        locator = self.async_locator()
        locator._search_global_ready = Mock(side_effect=ValueError("corrupt competitor"))
        frame = np.zeros((4, 4, 3), np.uint8)
        self.assertIsNone(locator.locate_global(frame, None))
        wait((locator._search_future,), timeout=3)
        with self.assertRaisesRegex(ValueError, "corrupt competitor"):
            locator.locate_global(frame, None)
        self.assertIsNone(locator._active)

    def test_pending_floor_discards_old_frame_evidence_before_retry(self):
        locator = self.locator()
        floor = self.layers(
            entries=[{"id": "floor"}],
            rank_layers=Mock(return_value=[(1.0, "floor")]),
            locate_proposed_layer=Mock(side_effect=[ResourcePending, self.result(0.99)]),
        )
        locator._cache["map02"] = floor
        locator._compare_base_maps = Mock(return_value=[("map02", self.result(0.5))])
        locator._active = "map01"
        pending = locator._search_snapshot("old", None, 0)
        self.assertEqual(pending.status, "rejected")
        self.assertEqual(pending.reason, "layer_scan_pending")
        self.assertEqual(pending.margin, 0)
        self.assertIsNone(locator._active)
        result = locator._search_snapshot("current", None, 0)
        self.assertEqual(result.best.ncc, 0.99)
        self.assertEqual([c.args[0] for c in floor.rank_layers.call_args_list], ["old", "current"])
        self.assertEqual(floor.locate_proposed_layer.call_args.args[0], "current")
        self.assertEqual(locator._compare_base_maps.call_count, 2)

    def test_confirmed_floor_reuses_one_base_search_with_and_without_workers(self):
        for threaded in (False, True):
            with self.subTest(threaded=threaded):
                locator = MapSetLocator(
                    SimpleNamespace(), ["map01", "map02"], workers=self.workers() if threaded else None
                )
                other = SimpleNamespace(locate_global=Mock(return_value=self.result(0.98)))
                base = SimpleNamespace(locate_global=Mock(return_value=self.result(0.5)))
                floor = self.layers(
                    base=base,
                    entries=[{"id": "floor"}],
                    rank_layers=Mock(return_value=[(1.0, "floor")]),
                    locate_proposed_layer=Mock(return_value=self.result(0.99)),
                )
                locator._cache.update(map01=other, map02=floor)
                result = locator._search_snapshot(np.zeros((3, 3, 3), np.uint8), None, 0)
                self.assertEqual(result.status, "ambiguous")
                other.locate_global.assert_called_once()
                base.locate_global.assert_called_once()

    def test_owned_readonly_acquisition_snapshot_is_shared_by_base_competitors(self):
        frame = np.zeros((10, 10, 3), np.uint8)
        frame.setflags(write=False)

        def search(snapshot, geometry):
            self.assertIs(snapshot, frame)
            return self.result()

        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=self.workers())
        locator._cache.update({mid: SimpleNamespace(locate_global=search) for mid in locator.map_ids})
        self.assertEqual(len(locator._compare_base_maps(frame, None)), 2)

    def test_pending_ranking_never_retains_an_active_map_hint(self):
        locator = self.locator()
        locator._cache["map01"] = self.layers(entries=[{"id": "floor"}], rank_layers=Mock(side_effect=ResourcePending))
        locator._compare_base_maps = Mock(return_value=[("map01", self.result(0.99))])
        locator._active = "map02"
        self.assertIsNone(locator._search_snapshot(None, None, 0))
        self.assertIsNone(locator._active)
        self.assertEqual(locator.last_failure_reason, "layer_scan_pending")

    def test_index_proposals_cannot_bypass_other_map_competition_or_confirmation(self):
        for competing_score, accepted in ((0.98, False), (0.5, True)):
            with self.subTest(competing_score=competing_score):
                locator = self.locator()
                floor = self.layers(
                    entries=[{"id": "last_floor"}],
                    rank_layers=Mock(return_value=[(1.0, "last_floor")]),
                    locate_proposed_layer=Mock(return_value=self.result(0.99)),
                )
                locator._cache.update(map01=SimpleNamespace(), map02=floor)
                locator._frame_gate._prepare_template = Mock(return_value=object())
                locator._warm_base_resources = Mock(return_value=True)
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

    def test_pending_floor_cannot_publish_partial_competition(self):
        locator = self.locator()
        for mid in locator.map_ids:
            locator._cache[mid] = self.layers(
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(1.0 if mid == "map01" else 0.1, "floor")]),
                locate_proposed_layer=Mock(
                    return_value=self.result(0.99)
                    if mid == "map01"
                    else replace(self.result(), reason="layer_scan_pending")
                ),
            )
        locator._compare_base_maps = Mock(return_value=[(mid, self.result(0.5)) for mid in locator.map_ids])
        locator._active = "map01"
        result = locator._search_snapshot(None, None, 0)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "layer_scan_pending")
        self.assertIsNone(locator._active)

    def test_weak_floor_competitors_are_preserved(self):
        locator = self.locator()
        for mid, score, coarse in [("map01", 0.9, 1.0), ("map02", 0.88, 0.0)]:
            locator._cache[mid] = self.layers(
                entries=[{"id": "floor"}],
                rank_layers=Mock(return_value=[(coarse, "floor")]),
                locate_proposed_layer=Mock(return_value=self.result(score, 0.01 if mid == "map02" else 0.3)),
            )
        locator._compare_base_maps = Mock(return_value=[(mid, self.result(0.4)) for mid in locator.map_ids])
        result = locator._search_snapshot(None, None, 0)
        self.assertEqual(result.status, "ambiguous")
        self.assertLess(result.margin, 0.03)
        self.assertIsNone(locator._active)

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

    def test_scoped_readiness_does_not_wait_for_excluded_map(self):
        pool = self.workers()
        set_scope = Mock()
        valid = SimpleNamespace(locate_global=Mock(return_value=self.result()), set_navigation_scope=set_scope)
        locator = MapSetLocator(
            SimpleNamespace(load=Mock(return_value=("map02", valid))),
            ["map01", "map02"],
            workers=pool,
            require_all_maps=True,
        )
        scope = SimpleNamespace(allows_map=lambda map_id: map_id == "map02")
        locator.prewarm(scope)
        self.assertEqual(list(locator._pending_maps), ["map02"])
        self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
        self.assertTrue(locator._warm_base_resources(scope))
        self.assertEqual(list(locator._cache), ["map02"])
        set_scope.assert_called_once_with(scope)

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
        self.assertFalse(wait(tuple(locator._pending_maps.values()), timeout=3).not_done)
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

    def test_identical_async_pixels_are_not_reused_for_changed_minimap_geometry(self):
        frame = np.full((10, 10, 3), 7, np.uint8)
        completed = Future()
        completed.set_result(self.result())
        same_geometry = MinimapGeometry(50, 50, 20)
        changed_geometry = MinimapGeometry(51, 50, 20)

        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=SimpleNamespace(closed=False), async_search=True)
        locator._search_future = completed
        locator._search_frame = frame
        locator._search_geometry = same_geometry
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        with patch("src.localization.map_set_locator.time.monotonic", return_value=1.0):
            locator._search_started_at = 0.0
            self.assertIs(locator._locate_async(np.full_like(frame, 8), same_geometry, 0), completed.result())

        completed = Future()
        completed.set_result(self.result())
        replacement = Future()
        submit = Mock(return_value=replacement)
        workers = SimpleNamespace(closed=False, acquisitions=SimpleNamespace(submit=submit))
        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=workers, async_search=True)
        locator._search_future = completed
        locator._search_frame = frame
        locator._search_geometry = same_geometry
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._warm_base_resources = Mock(return_value=True)
        locator._prepare_base_search = Mock(return_value=True)
        with patch("src.localization.map_set_locator.time.monotonic", return_value=1.0):
            locator._search_started_at = 0.0
            self.assertIsNone(locator._locate_async(frame, changed_geometry, 0))
        locator._warm_base_resources.assert_called_once_with(None)
        submit.assert_called_once()
        self.assertIs(locator._search_future, replacement)

    def test_pending_search_does_not_report_a_stale_floor_error(self):
        locator = MapSetLocator(
            SimpleNamespace(),
            ["map01"],
            workers=SimpleNamespace(closed=False),
            async_search=True,
        )
        locator._search_future = Future()
        locator._search_started_generation = locator._search_generation
        locator._cache["map01"] = SimpleNamespace(
            entries=[{"id": "floor1"}],
            resource_errors={"floor1": "temporary decode failure"},
        )

        self.assertIsNone(locator._locate_async(np.zeros((16, 16, 3), np.uint8), None, 0))
        self.assertEqual(locator.last_failure_reason, "layer_scan_pending")

    def test_async_completion_preserves_the_source_observation_time(self):
        locator = MapSetLocator(
            SimpleNamespace(),
            ["map01"],
            workers=SimpleNamespace(closed=False),
            async_search=True,
        )
        frame = np.zeros((16, 16, 3), np.uint8)
        future = Future()
        future.set_result(_SearchOutcome(self.result(0.99, margin=0.2), "", 123.5))
        locator._search_future = future
        locator._search_frame = frame.copy()
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        with patch("src.localization.map_set_locator.time.monotonic", return_value=1.0):
            locator._search_started_at = 0.0
            result = locator._locate_async(np.full_like(frame, 8), None, 0)
        self.assertEqual(result.observed_at, 123.5)

    def test_async_completion_preserves_a_terminal_resource_reason(self):
        locator = MapSetLocator(
            SimpleNamespace(),
            ["map01"],
            workers=SimpleNamespace(closed=False),
            async_search=True,
        )
        frame = np.zeros((16, 16, 3), np.uint8)
        reason = "layer_resource_error: floor1: corrupt"
        future = Future()
        future.set_result(_SearchOutcome(None, reason, 123.5))
        locator._search_future = future
        locator._search_frame = frame
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        with patch("src.localization.map_set_locator.time.monotonic", return_value=1.0):
            locator._search_started_at = 0.0
            self.assertIsNone(locator._locate_async(frame, None, 0))
        self.assertEqual(locator.last_failure_reason, reason)

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

    def test_normalization_follows_the_prepared_resource_that_will_be_loaded(self):
        prepared_store = SimpleNamespace(require_prepared=False, has_prepared=Mock(return_value=True))
        native_store = SimpleNamespace(require_prepared=False, has_prepared=Mock(return_value=False))
        prepared = MapSetLocator(prepared_store, ["map01"])
        native = MapSetLocator(native_store, ["map01"])

        self.assertTrue(prepared.requires_visual_normalization())
        self.assertFalse(native.requires_visual_normalization())
        prepared_store.has_prepared.assert_called_once_with()
        native_store.has_prepared.assert_called_once_with()
        prepared.requires_visual_normalization()
        prepared_store.has_prepared.assert_called_once_with()

    def test_prediction_wide_search_competes_every_allowed_map(self):
        locator = self.locator()
        locator._active = "map01"
        weaker = replace(self.result(0.80).best, world_x=0, world_z=0)
        stronger = replace(self.result(0.99).best, world_x=100, world_z=0)
        locator._cache.update(
            {
                "map01": SimpleNamespace(
                    locate_global=Mock(return_value=replace(self.result(0.80), best=weaker, candidates=(weaker,)))
                ),
                "map02": SimpleNamespace(
                    locate_global=Mock(return_value=replace(self.result(0.99), best=stronger, candidates=(stronger,)))
                ),
            }
        )
        result = locator.locate_near(None, None, None, use_global=True)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.map_id, "map02")
        locator._cache["map01"].locate_global.assert_called_once_with(None, None)
        locator._cache["map02"].locate_global.assert_called_once_with(None, None)

    def test_strict_wide_search_warms_missing_scoped_maps_before_waiting(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], require_all_maps=True)
        locator._active = "map01"
        locator._cache["map01"] = SimpleNamespace(entries=[], locate_global=Mock(return_value=self.result()))
        locator._warm_base_resources = Mock(return_value=False)
        scope = SimpleNamespace(allows_map=lambda map_id: map_id == "map02")
        locator.set_navigation_scope(scope)

        self.assertIsNone(locator.locate_near(None, None, None, use_global=True))
        locator._warm_base_resources.assert_called_once_with(scope)
        self.assertIn("map02", locator.last_failure_reason)

    def test_pending_async_search_preserves_tracking_state(self):
        calibration = self.locator().calibration
        locator = SimpleNamespace(
            calibration=calibration,
            _scale=Mock(return_value=1.0),
            locate_near=Mock(return_value=None),
            locate_global=Mock(return_value=None),
            last_failure_reason="visual_global_search_pending",
            reset_tracking=Mock(),
            close=Mock(),
        )
        source = VisualPositionSource(
            SimpleNamespace(width=640, height=360, active_time=lambda: 0),
            locator,
            "",
            chain_enabled=False,
        )
        source._initialize_shape((360, 640))
        locator.reset_tracking.reset_mock()
        source._origin = np.array([12.0, 8.0])
        source._last_absolute_t = 4.0
        candidate = self.result().best
        source.tracker._convinced = candidate
        source.tracker._convinced_t = 4.0
        source.tracker._pending = candidate
        source.tracker._pending_hits = 1
        source.tracker._pending_since = 4.0
        frame = np.random.default_rng(7).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        state = source.sample(frame, now=4.5)
        self.assertEqual(state["trust_reason"], "visual_global_search_pending")
        np.testing.assert_array_equal(source._origin, [12.0, 8.0])
        self.assertEqual(source._last_absolute_t, 4.0)
        self.assertIs(source.tracker._convinced, candidate)
        self.assertEqual(source.tracker._pending_hits, 1)
        self.assertIsNone(source.tracker._last_update_t)
        locator.reset_tracking.assert_not_called()
        source.close()

    def test_pending_layer_result_preserves_tracking_state(self):
        calibration = self.locator().calibration
        pending = replace(
            self.result(0.95),
            status="rejected",
            reason="layer_scan_pending",
            spatial_margin=0,
        )
        locator = SimpleNamespace(
            calibration=calibration,
            _scale=Mock(return_value=1.0),
            locate_near=Mock(return_value=pending),
            locate_global=Mock(return_value=pending),
            last_failure_reason="layer_scan_pending",
            reset_tracking=Mock(),
            close=Mock(),
        )
        source = VisualPositionSource(
            SimpleNamespace(width=640, height=360, active_time=lambda: 0),
            locator,
            "",
            chain_enabled=False,
        )
        source._initialize_shape((360, 640))
        locator.reset_tracking.reset_mock()
        source._origin = np.array([12.0, 8.0])
        source._last_absolute_t = 4.0
        candidate = self.result().best
        source.tracker._convinced = candidate
        source.tracker._convinced_t = 4.0
        source.tracker._pending = candidate
        source.tracker._pending_hits = 1
        source.tracker._pending_since = 4.0
        frame = np.random.default_rng(8).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        state = source.sample(frame, now=4.5)
        self.assertEqual(state["trust_reason"], "layer_scan_pending")
        self.assertFalse(state["position_trusted"])
        np.testing.assert_array_equal(source._origin, [12.0, 8.0])
        self.assertIs(source.tracker._convinced, candidate)
        locator.reset_tracking.assert_not_called()
        source.close()

    def test_navigation_scope_skips_excluded_base_preparation_and_match(self):
        locator = self.locator()
        frame = np.zeros((360, 640, 3), np.uint8)
        maps = {
            "map01": SimpleNamespace(
                entries=[],
                base=SimpleNamespace(
                    prepare_global_search=Mock(return_value=True),
                    locate_global=Mock(return_value=self.result(0.99)),
                ),
                locate_global=Mock(return_value=self.result(0.99)),
            ),
            "map02": SimpleNamespace(
                entries=[],
                base=SimpleNamespace(
                    prepare_global_search=Mock(return_value=True),
                    locate_global=Mock(return_value=self.result(0.80)),
                ),
                locate_global=Mock(return_value=self.result(0.80)),
            ),
        }
        locator._cache.update(maps)
        locator._workers = SimpleNamespace(closed=False, resources=object())
        scope = SimpleNamespace(
            search_identity=lambda: ("map02", "__main__"),
            anchor_map_id="map02",
            allows_tier=lambda map_id, tier: map_id == "map02",
        )
        locator.set_navigation_scope(scope)
        self.assertTrue(locator._prepare_base_search(frame))
        maps["map01"].base.prepare_global_search.assert_not_called()
        maps["map02"].base.prepare_global_search.assert_called_once()
        locator._workers = None
        result = locator._compare_base_maps(frame, None)
        self.assertEqual([map_id for map_id, _ in result], ["map02"])
        maps["map01"].base.locate_global.assert_not_called()

    def test_floor_only_scope_skips_main_resources_without_hiding_floor(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"])
        locator._cache["map01"] = SimpleNamespace(entries=[{"id": "floor1"}])
        locator._cache["map02"] = SimpleNamespace(entries=[])
        scope = SimpleNamespace(
            search_identity=lambda: ("map02", "__main__"),
            anchor_map_id="map02",
            allows_tier=lambda map_id, tier: (map_id, tier) in {("map01", "floor1"), ("map02", "__main__")},
        )
        self.assertEqual(locator._maps_in_scope(scope), ["map01", "map02"])
        self.assertEqual(locator._main_maps_in_scope(scope), ["map02"])

    def test_scope_generation_change_stops_old_base_comparison(self):
        locator = self.locator()
        locator._cache["map01"] = SimpleNamespace(locate_global=Mock(return_value=self.result()))
        locator._search_generation = 2
        self.assertEqual(locator._compare_base_maps(None, None, generation=1), [])
        locator._cache["map01"].locate_global.assert_not_called()

    def test_scope_generation_change_drains_submitted_match_before_returning(self):
        pool = self.workers()
        completed = threading.Event()

        def search(*args):
            completed.set()
            return self.result()

        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], workers=pool)
        locator._cache.update(
            {
                "map01": SimpleNamespace(locate_global=search),
                "map02": SimpleNamespace(locate_global=search),
            }
        )
        submit = pool.matches.submit
        submitted = []

        def submit_one(*args, **kwargs):
            future = submit(*args, **kwargs)
            submitted.append(future)
            locator._search_generation += 1
            return future

        with patch.object(pool.matches, "submit", side_effect=submit_one):
            results = locator._compare_base_maps(np.zeros((10, 10, 3), np.uint8), None, generation=0)
        self.assertEqual([mid for mid, _ in results], ["map01"])
        self.assertEqual(len(submitted), 1)
        self.assertTrue(completed.is_set())

    def test_scoped_resource_reason_ignores_excluded_map_errors(self):
        pool = self.workers()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def load(map_id):
            entered.set()
            self.assertTrue(release.wait(3))
            return map_id, SimpleNamespace()

        locator = MapSetLocator(SimpleNamespace(load=load), ["map01", "map02"], workers=pool, require_all_maps=True)
        locator._resource_errors["map01"] = "old map failure"
        scope = SimpleNamespace(allows_map=lambda map_id: map_id == "map02")
        locator.set_navigation_scope(scope)
        locator._frame_gate._prepare_template = Mock(return_value=object())
        self.assertIsNone(locator.locate_global(np.zeros((64, 64, 3), np.uint8), None))
        self.assertIn("map02", locator.last_failure_reason)
        self.assertNotIn("old map failure", locator.last_failure_reason)
        self.assertTrue(entered.wait(3))
        release.set()

    def test_strict_near_readiness_uses_scope_required_maps(self):
        locator = MapSetLocator(SimpleNamespace(), ["map01", "map02"], require_all_maps=True)
        locator._active = "map01"
        locator._cache["map01"] = SimpleNamespace(
            entries=[],
            locate_global=Mock(return_value=self.result()),
            locate_near=Mock(return_value=self.result()),
        )
        scope = SimpleNamespace(
            allows_map=lambda map_id: map_id == "map02",
            allows_tier=lambda map_id, tier: map_id == "map01",
        )
        locator.set_navigation_scope(scope)
        locator._warm_base_resources = Mock(return_value=False)
        self.assertIsNone(locator.locate_near(None, None, None, use_global=True))
        locator._warm_base_resources.assert_called_once_with(scope)
        self.assertIn("visual_map_resources_pending", locator.last_failure_reason)
        self.assertIn("map02", locator.last_failure_reason)

    def test_pending_floor_competition_keeps_active_map_unset_and_uses_wait_reason(self):
        for resource_error in (None, "corrupt floor"):
            with self.subTest(resource_error=resource_error):
                locator = MapSetLocator(SimpleNamespace(), ["map01"])
                cached = SimpleNamespace(
                    entries=[{"id": "floor1"}],
                    rank_layers=Mock(return_value=[(0.99, "floor1")]),
                    locate_proposed_layer=Mock(side_effect=ResourcePending()),
                    resource_errors={} if resource_error is None else {"floor1": resource_error},
                    combine_global_results=Mock(return_value=self.result()),
                )
                locator._cache["map01"] = cached
                base = self.result()
                result = locator._search_snapshot_proposals(
                    None,
                    None,
                    0,
                    [("map01", base)],
                    scope=None,
                    generation=locator._search_generation,
                )
                self.assertIsNone(locator._active)
                self.assertEqual(result.status, "rejected")
                self.assertEqual(
                    result.reason,
                    "layer_scan_pending" if resource_error is None else "layer_resource_error: floor1: corrupt floor",
                )

    def test_active_wide_search_uses_loaded_maps_without_sync_loading(self):
        locator = self.locator()
        locator._active = "map01"
        locator.store.load = Mock(side_effect=AssertionError("synchronous map load"))
        locator._cache["map01"] = SimpleNamespace(locate_global=Mock(return_value=self.result()))
        self.assertIsNotNone(locator.locate_near(None, None, None, use_global=True))
        locator.store.load.assert_not_called()

    def test_current_invalid_frame_reason_wins_over_running_search(self):
        locator = self.locator()
        locator._search_future = Future()
        locator._frame_gate._prepare_template = Mock(return_value=None)
        locator._frame_gate.last_failure_reason = "minimap_insufficient_texture"
        self.assertIsNone(locator.locate_global(np.zeros((360, 640, 3), np.uint8), None))
        self.assertEqual(locator.last_failure_reason, "minimap_insufficient_texture")
        locator._search_future.set_result(None)

    def test_stale_async_exception_is_discarded(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        future = Future()
        future.set_exception(ValueError("old resource failure"))
        workers = SimpleNamespace(closed=False, acquisitions=SimpleNamespace(submit=Mock()))
        locator = MapSetLocator(SimpleNamespace(), ["map01"], workers=workers, async_search=True)
        locator._search_future = future
        locator._search_frame = frame
        locator._search_geometry = None
        locator._search_shape = frame.shape
        locator._search_started_generation = locator._search_generation
        locator._search_started_at = 0.0
        locator.reset_tracking()
        locator._warm_base_resources = Mock(return_value=True)
        locator._prepare_base_search = Mock(return_value=False)
        self.assertIsNone(locator._locate_async(frame, None, 0))
        self.assertEqual(locator.last_failure_reason, "visual_coarse_resources_pending")
        workers.acquisitions.submit.assert_not_called()

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
