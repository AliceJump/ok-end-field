import ast
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.core.config_migration import apply_value_migrations
from src.core.NavConfig import NAV_MAP_ID_KEY
from src.core.PositionConfig import CONFIG_USE_EXPERIMENTAL, CONFIG_VISUAL_MAP_ROOT, DEFAULT_POSITION_CONFIG
from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.layered_minimap_locator import LayeredMinimapLocator, layer_calibration
from src.localization.map_set_locator import MapSetLocator
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MapCandidate,
    MinimapGeometry,
    MinimapMapLocator,
    MinimapTracker,
    candidate_margin,
)
from src.localization.position_config_migration import backup_retired_position_settings, migrate_item_position_overrides
from src.localization.position_service import WorldPoseService
from src.localization.visual_position_source import VisualPositionSource
from src.runtime_state.state_hub import RuntimeStateHub
from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin
from src.tasks.trigger.ItemNavigatorTask import ItemNavigatorTask


class TestVisualPositionSource(unittest.TestCase):
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
        self.assertTrue(before.mask.flags.writeable is False)
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


class TestMapAssetsAndSourceService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write_map("map02")

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

    def test_only_one_map_can_be_selected_automatically(self):
        self.assertEqual(CalibratedMapStore(self.root).load()[0], "map02")
        self.write_map("map01")
        map_id, locator = CalibratedMapStore(self.root).load()
        self.assertEqual(map_id, "")
        self.assertEqual(locator.map_ids, ("map01", "map02"))
        with self.assertRaisesRegex(ValueError, "invalid_visual_map_id"):
            CalibratedMapStore(self.root).load("../map02")

    def test_service_has_no_ws_mixin_or_runtime_import(self):
        self.assertFalse(any("Ws" in cls.__name__ for cls in WorldPoseService.__mro__))
        root = Path(__file__).resolve().parents[1] / "src/localization"
        for name in ("position_service.py", "capture_pose_mixin.py", "visual_position_source.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("ws_position", node.module or "")
                    self.assertNotIn("minimap_position_mixin", node.module or "")

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
        with self.assertRaises(ValueError):
            CalibratedMapStore(self.root).load("map02")

    def test_resource_failure_during_lazy_search_is_untrusted(self):
        service = self.service()
        service.start_minimap_position(wait_stable=False)
        service._visual_source.sample = Mock(side_effect=ValueError("broken lazy layer"))
        result = service.sample_world_pose(frame=np.zeros((360, 640, 3), np.uint8), now=10)
        self.assertFalse(result["position_trusted"])
        self.assertIsNone(result["x"])


class TestAutomaticMapCompetition(unittest.TestCase):
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
