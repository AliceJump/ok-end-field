import ast
import json
import subprocess
import sys
import threading
import unittest
from concurrent.futures import CancelledError, Future, wait
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
from src.localization.layered_minimap_locator import LayeredMinimapLocator
from src.localization.localization_workers import LocalizationWorkers, ResourcePending
from src.localization.map_set_locator import MapSetLocator, _SearchOutcome
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MapCandidate,
    MinimapGeometry,
    MinimapMapLocator,
    TrackResult,
    candidate_margin,
)
from src.localization.position_config_migration import (
    backup_retired_position_settings,
    migrate_item_position_overrides,
    migrate_visual_map_root,
)
from src.localization.position_service import WorldPoseService
from src.localization.visual_position_source import VisualPositionSource
from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic
from src.runtime_state.world_pose import publish_world_pose
from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask
from tests.localization_bundle_fixture import LocalizationBundleFixture


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

    def test_direct_source_normalizes_non_1280_frame_before_geometry(self):
        source = VisualPositionSource(self.task, self.locator, "map02")
        rejection = SimpleNamespace(status="lost", accepted=False, reason="test_rejection", locate=None)
        with (
            patch.object(source.locator, "requires_visual_normalization", return_value=True, create=True),
            patch.object(source.tracker, "update", return_value=rejection) as update,
        ):
            source.sample(np.zeros((1080, 1920, 3), np.uint8), now=1)
        self.assertEqual(update.call_args.args[0].shape, (720, 1280, 3))
        self.assertEqual(update.call_args.args[1], MinimapGeometry.from_frame(1280, 720))

    def test_published_frame_time_uses_the_async_search_snapshot(self):
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
        self.assertEqual(state["frame_t"], 4.0)

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


class TestMapAssetsAndSourceService(LocalizationBundleFixture, unittest.TestCase):
    bundle_map_id = "map02"

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

    def test_only_one_map_can_be_selected_automatically(self):
        self.assertEqual(CalibratedMapStore(self.root / "maps", require_prepared=False).load()[0], "map02")
        self.write_map("map01")
        map_id, locator = CalibratedMapStore(self.root / "maps", require_prepared=False).load()
        self.assertEqual(map_id, "")
        self.assertEqual(locator.map_ids, ("map01", "map02"))
        store = CalibratedMapStore(self.root / "maps", require_prepared=False)
        with self.assertRaisesRegex(ValueError, "invalid_visual_map_id"):
            store.load("../map02")

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


class TestAutomaticMapCompetition(unittest.TestCase):
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
        pool.resources.submit(lambda: None).result(timeout=3)
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
        pool.resources.submit(lambda: None).result(timeout=3)
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
            pool.resources.submit(lambda: None).result(timeout=3)
            self.assertFalse(locator._warm_base_resources())
            self.assertFalse(locator._warm_base_resources())
            self.assertEqual(store.load.call_count, 1)
        with patch("src.localization.map_set_locator.time.monotonic", return_value=16):
            locator.prewarm()
            pool.resources.submit(lambda: None).result(timeout=3)
            self.assertTrue(locator._warm_base_resources())
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
