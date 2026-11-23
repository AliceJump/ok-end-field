import tempfile
import threading
import unittest
import weakref
from concurrent.futures import CancelledError, Future, wait
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.localization.layer_search_index import LayerSearchIndex
from src.localization.layered_minimap_locator import LayeredMinimapLocator, layer_calibration
from src.localization.localization_workers import (
    DecodeBudget,
    LocalizationWorkers,
    ResourcePending,
    _ResourceExecutor,
)
from src.localization.map_set_locator import MapSetLocator
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MapCandidate,
    MinimapMapLocator,
    MinimapTracker,
)
from src.tasks.navigation.mixin.grid_navigation_mixin import GridNavigationMixin


class TestVisualLayerCandidates(unittest.TestCase):
    def test_failed_index_can_be_resubmitted_with_and_without_waiting(self):
        for wait_for_resources in (False, True):
            with self.subTest(wait=wait_for_resources):
                locator = self.locator()
                failed, recovered = Future(), Future()
                failed.set_exception(OSError("temporary resource failure"))
                index = object()
                recovered.set_result(index)
                submit = Mock(side_effect=[failed, recovered])
                locator._resource_workers = SimpleNamespace(
                    closed=False, indexes=SimpleNamespace(submit=submit), resources=object(), stop=threading.Event()
                )
                with self.assertRaisesRegex(OSError, "temporary resource failure"):
                    locator.prepare_layer_index(wait_for_resources=wait_for_resources)
                self.assertIsNone(locator._pending_index)
                locator.prepare_layer_index(wait_for_resources=wait_for_resources)
                self.assertIs(locator._search_index, index)
                self.assertEqual(submit.call_count, 2)

    def test_complete_floor_searches_merge_once_without_overlap_rematching(self):
        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        rival = replace(self.result(0.98).best, world_x=15, world_z=15)
        floor.locate_global.side_effect = [
            self.result(0.99),
            replace(self.result(0.98), best=rival, candidates=(rival,)),
        ]
        frame = np.zeros((10, 10, 3), np.uint8)
        with patch.object(locator, "_search", side_effect=AssertionError("duplicate overlap search")):
            results = [
                (tier, locator.locate_proposed_layer(frame, None, tier, complete_competition=True))
                for tier in ("floor1", "floor2")
            ]
            result = locator.combine_global_results(self.result(0.5), results)
        self.assertEqual(floor.locate_global.call_count, 2)
        floor.locate_near.assert_not_called()
        self.assertEqual(result.status, "ambiguous")
        self.assertAlmostEqual(result.margin, 0.01)
        self.assertEqual({c.tier_id for c in result.candidates}, {"__main__", None, "floor2"})

    def test_global_merge_keeps_same_position_tier_ambiguity_and_missing_coverage(self):
        for complete, competing in ((True, True), (False, False)):
            with self.subTest(complete=complete):
                locator = self.locator()
                locator.tier_coverage_complete = complete
                results = [("floor1", self.result(0.99))]
                if competing:
                    results.append(("floor2", self.result(0.99)))
                result = locator.combine_global_results(self.result(0.4), results)
                self.assertEqual(result.status, "accepted")
                self.assertIsNone(result.best.tier_id)
                if competing:
                    self.assertEqual(result.layer_margin, 0)

    def test_global_merge_computes_all_margins_before_display_truncation(self):
        locator = self.locator()
        locator.base.top_k = 1
        rival = replace(self.result(0.94).best, world_x=15, world_z=15)
        floor = self.result(0.99)
        floor = replace(floor, candidates=(floor.best, rival))
        result = locator.combine_global_results(self.result(0.4), [("floor1", floor)])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.status, "ambiguous")
        self.assertAlmostEqual(result.margin, 0.05)

    def test_complete_floor_search_recounts_and_evicts_inactive_resources(self):
        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator._cache.update(floor1=floor, unused=SimpleNamespace(map_image=np.ones((20, 20), np.uint8)))
        locator._cache_limit_bytes = 1
        result = locator.locate_proposed_layer(None, None, "floor1", complete_competition=True)
        self.assertIsNotNone(result)
        self.assertEqual(list(locator._cache), ["floor1"])

    def test_failed_full_floor_resource_cannot_be_treated_as_empty_competition(self):
        locator = self.locator()
        locator._load.side_effect = ValueError("broken floor")
        with self.assertRaises(ResourcePending):
            locator.locate_proposed_layer(None, None, "floor1", complete_competition=True)

    def test_empty_main_search_skips_repeat_and_still_competes_floors(self):
        locator = self.locator(0.5, 0.99)
        locator.base.locate_global = Mock(return_value=None)
        locator.base.last_failure_reason = "minimap_no_candidates: search=global"
        locator.rank_layers = Mock(return_value=[])
        frame = np.zeros((100, 100, 3), np.uint8)
        result = locator.locate_global(frame, None)
        self.assertEqual(result.best.tier_id, "floor1")
        locator.base.locate_global.assert_called_once()
        locator.base.locate_global.reset_mock()
        locator._recent = None
        result = locator.locate_global(frame, None, base_result=None)
        self.assertEqual(result.best.tier_id, "floor1")
        locator.base.locate_global.assert_not_called()
        self.assertEqual(locator._load.return_value.locate_global.call_count, 2)

    def test_layer_proposal_without_base_result_rechecks_main_competition(self):
        locator = self.locator(0.5, 0.99)
        locator.rank_layers = Mock(return_value=[(0.99, "floor1")])
        frame = np.zeros((100, 100, 3), np.uint8)
        result = locator.locate_global(frame, None)
        self.assertEqual(result.best.tier_id, "floor1")
        locator.base.locate_near.assert_called_once()

    def test_background_proposal_failure_keeps_raw_ranking(self):
        locator = self.locator()
        template = SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8), dim_terrain=np.ones((32, 32), bool))
        locator.base._prepare_template = Mock(return_value=template)
        locator._background_proposals = Mock(side_effect=OSError("tile unavailable"))
        locator._rank_raw_layers = Mock(return_value=[(0.9, "floor1")])
        proposals = locator.rank_layers(np.zeros((100, 100, 3), np.uint8), None)
        self.assertEqual(proposals, [(0.9, "floor1")])

    def test_single_sided_background_failure_keeps_raw_order(self):
        locator = self.locator()
        locator.entries = [dict(locator.entries[0], id=str(i)) for i in range(4)]
        template = SimpleNamespace(
            flat=np.zeros((32, 32, 3), np.uint8),
            dim_terrain=None,
            proposal_dim_mask=np.ones((32, 32), bool),
        )
        raw = [(0.8 - 0.05 * i, str(i), (10, 12)) for i in range(4)]
        locator.base._prepare_template = Mock(return_value=template)
        locator._rank_raw_layers = Mock(return_value=raw)
        locator.base.proposal_background_score = Mock(side_effect=OSError("unavailable tile"))
        proposals = locator.rank_layers(np.zeros((100, 100, 3), np.uint8), None)
        self.assertEqual([tier for _, tier in proposals], ["0", "1", "2", "3"])
        for (score, _), expected in zip(proposals, (0.8, 0.75, 0.7, 0.65), strict=True):
            self.assertAlmostEqual(score, expected)

    def test_single_sided_background_only_promotes_proposals_and_retains_all_raw_fallbacks(self):
        locator = self.locator()
        locator.entries = [dict(locator.entries[0], id=str(i)) for i in range(8)]
        template = SimpleNamespace(
            flat=np.zeros((32, 32, 3), np.uint8),
            dim_terrain=None,
            proposal_dim_mask=np.ones((32, 32), bool),
        )
        raw = [(0.8 - 0.05 * i, str(i), (10, 12)) for i in range(8)]
        locator.base._prepare_template = Mock(return_value=template)
        locator._rank_raw_layers = Mock(return_value=raw)
        locator.base.proposal_background_score = Mock(side_effect=[0.0, 1.0, 0.0])
        frame = np.zeros((100, 100, 3), np.uint8)
        proposals = locator.rank_layers(frame, None)
        self.assertEqual(proposals[0], (1.0, "1"))
        self.assertEqual({tier for _, tier in proposals}, {str(i) for i in range(8)})
        self.assertEqual(locator.base.proposal_background_score.call_count, 3)
        self.assertIsNone(locator._recent)
        self.assertEqual(locator.rank_layers(frame, None), [(0.0, tier) for _, tier in proposals])

    def test_snapshot_ranking_uses_current_scores_but_stores_only_existing_id_hints(self):
        locator = self.locator()
        template = SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8))
        locator.base._prepare_template = Mock(return_value=template)
        locator._background_proposals = Mock(return_value=[])
        locator._rank_raw_layers = Mock(side_effect=[[(0.8, "a")], [(0.9, "b")]])
        frame = np.zeros((100, 100, 3), np.uint8)
        self.assertEqual(locator.rank_layers(frame, None), [(0.8, "a")])
        self.assertEqual(locator.rank_layers(frame, None), [(0.0, "a")])
        self.assertEqual(locator.rank_layers(frame, None, fresh_scores=True), [(0.9, "b")])
        self.assertEqual(locator._ranked_tiers, ("b",))
        self.assertEqual(locator.rank_layers(frame, None), [(0.0, "b")])

    def test_background_proposal_defers_raw_index_until_its_candidate_is_exhausted(self):
        locator = self.locator()
        template = SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8))
        locator.base._prepare_template = Mock(return_value=template)
        locator._background_proposals = Mock(return_value=[(0.99, "background")])
        raw = SimpleNamespace(rank=Mock(return_value=[(0.9, "fallback"), (0.8, "background"), (0.7, "other")]))
        frame = np.zeros((100, 100, 3), np.uint8)
        with patch.object(LayerSearchIndex, "build", return_value=raw) as load:
            self.assertEqual(locator.rank_layers(frame, None), [(0.99, "background")])
            load.assert_not_called()
            self.assertIsNone(locator._search_index)
            self.assertEqual(
                locator.rank_layers(frame, None, required_count=2),
                [(0.0, "background"), (0.9, "fallback"), (0.7, "other")],
            )
            load.assert_called_once()
            locator._background_proposals.assert_called_once()
            locator.release_proposal_resources()
            self.assertIsNone(locator._search_index)
            self.assertEqual(
                locator.rank_layers(frame, None, required_count=3),
                [(0.0, "background"), (0.0, "fallback"), (0.0, "other")],
            )
            load.assert_called_once()
            # A changed scene must rebuild raw ranking instead of reusing scores.
            template.flat[:] = 100
            locator._background_proposals.return_value = []
            locator.rank_layers(frame, None)
            self.assertEqual(load.call_count, 2)

    def test_single_map_failed_background_proposal_reaches_raw_fallback(self):
        locator = self.locator(0.5, 0.99)
        locator.base._prepare_template = Mock(return_value=SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8)))
        locator._background_proposals = Mock(return_value=[(0.99, "wrong")])
        locator._search_index = SimpleNamespace(rank=Mock(return_value=[(0.95, "floor1")]))
        locator.locate_proposed_layer = Mock(side_effect=[None, self.result(0.99, tier="floor1")])
        locator._search = Mock(return_value=self.result(0.5))
        frame = np.zeros((100, 100, 3), np.uint8)
        locator.locate_global(frame, None)
        locator._search_index.rank.assert_not_called()
        result = locator.locate_global(frame, None)
        self.assertEqual(result.best.tier_id, "floor1")
        self.assertEqual([call.args[2] for call in locator.locate_proposed_layer.call_args_list], ["wrong", "floor1"])

    def test_successful_floor_validation_releases_only_proposal_pixels(self):
        locator = self.locator(0.5, 0.99)
        locator._search_index = SimpleNamespace(images=(np.zeros((32, 32, 3), np.uint8),))
        locator._ranked_tiers = ("floor1",)
        result = locator.locate_proposed_layer(None, None, "floor1")
        self.assertEqual(result.status, "accepted")
        self.assertIsNone(locator._search_index)
        self.assertEqual(locator._ranked_tiers, ("floor1",))
        self.assertIsNotNone(locator._recent)

    def test_proposal_order_reuses_only_ids_and_expires_on_scene_size_time_or_reset(self):
        locator = self.locator()
        pixels = np.zeros((32, 32, 3), np.uint8)
        template = SimpleNamespace(flat=pixels)
        locator.base._prepare_template = Mock(return_value=template)
        locator._search_index = SimpleNamespace(rank=Mock(return_value=[(0.8, "a"), (0.7, "b")]))
        frame = np.zeros((100, 100, 3), np.uint8)
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=1):
            self.assertEqual(locator.rank_layers(frame, None), [(0.8, "a"), (0.7, "b")])
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=2):
            self.assertEqual(locator.rank_layers(frame.copy(), None), [(0.0, "a"), (0.0, "b")])
        self.assertEqual(locator._search_index.rank.call_count, 1)
        self.assertLessEqual(locator._ranked_signature.nbytes, 16 * 16 * 3)
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=3):
            template.flat = np.full_like(pixels, 100)
            locator.rank_layers(frame, None)
            locator.rank_layers(frame[:90], None)
        with patch("src.localization.layered_minimap_locator.time.monotonic", return_value=20):
            locator.rank_layers(frame[:90], None)
            locator.reset_proposals()
            locator.rank_layers(frame[:90], None)
        self.assertEqual(locator._search_index.rank.call_count, 5)
        locator.close()
        self.assertFalse(locator._ranked_tiers)
        self.assertIsNone(locator._ranked_signature)

    def test_background_hint_without_usable_terrain_keeps_every_raw_proposal(self):
        locator = self.locator()
        locator._bundle = object()
        locator._background_hint = (10000, 10000)
        template = SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8), dim_terrain=object())
        locator.base._prepare_template = Mock(return_value=template)
        original = [(0.99, "a"), (0.7, "b"), (0.3, "c")]
        locator._search_index = SimpleNamespace(rank=Mock(return_value=original))
        self.assertEqual(locator.rank_layers(np.zeros((100, 100, 3), np.uint8), None), original)
        locator._load.assert_not_called()
        self.assertIsNone(locator._recent)

    def test_background_job_waits_for_overlap_without_repeating_full_floor_search(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                locator = self.locator(0.5, 0.99)
                floor = locator._load.return_value
                other = SimpleNamespace(
                    map_image=np.empty((0, 0), np.uint8),
                    locate_near=Mock(return_value=self.result(0.3)),
                )
                locator._load = LayeredMinimapLocator._load.__get__(locator)
                locator._cache["floor1"] = floor
                locator.entries.append({**locator.entries[0], "id": "floor2"})
                entered, release = threading.Event(), threading.Event()
                workers = LocalizationWorkers()
                locator._resource_workers = workers

                def decode(entry, entered=entered, release=release, other=other):
                    self.assertEqual(entry["id"], "floor2")
                    entered.set()
                    self.assertTrue(release.wait(3))
                    return other

                locator._decode = decode
                frame = np.zeros((10, 10, 3), np.uint8)
                frame.setflags(write=False)
                try:
                    future = workers.acquisitions.submit(
                        locator.locate_proposed_layer, frame, None, "floor1", wait_for_resources=True
                    )
                    self.assertTrue(entered.wait(3))
                    floor.locate_global.assert_called_once_with(frame, None)
                    self.assertFalse(future.done())
                    if cancel:
                        workers.stop.set()
                        with self.assertRaises(CancelledError):
                            future.result(timeout=3)
                        floor.locate_near.assert_not_called()
                    else:
                        release.set()
                        result = future.result(timeout=3)
                        self.assertEqual(result.best.tier_id, "floor1")
                        self.assertNotEqual(result.reason, "layer_scan_pending")
                        other.locate_near.assert_called_once_with(frame, (10, 10), None, search_radius_m=80.0)
                    floor.locate_global.assert_called_once()
                finally:
                    release.set()
                    workers.close()
                    workers.resources.shutdown(wait=True, cancel_futures=True)
                    workers.acquisitions.shutdown(wait=True, cancel_futures=True)

    def test_failed_overlap_in_waiting_job_cannot_prove_tier_uniqueness(self):
        locator = self.locator(0.5, 0.99)
        floor = locator._load.return_value
        locator.entries.append({**locator.entries[0], "id": "broken"})
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator._cache["floor1"] = floor
        locator._decode = Mock(side_effect=ValueError("bad competitor"))
        workers = LocalizationWorkers()
        locator._resource_workers = workers
        try:
            result = locator.locate_proposed_layer(
                np.zeros((10, 10, 3), np.uint8), None, "floor1", wait_for_resources=True
            )
            self.assertIsNone(result.best.tier_id)
            self.assertEqual(result.status, "rejected")
            self.assertEqual(result.margin, 0)
            self.assertIn("broken", locator._resource_retry_at)
            floor.locate_global.assert_called_once()
        finally:
            workers.close()

    def test_floor_index_canonicalizes_root_alias_without_allowing_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = self.index_fixture(root)
            (root / "alias").mkdir()
            alias = root / "alias" / ".."
            expected = LayerSearchIndex.build(root, [entry], color=True)
            actual = LayerSearchIndex.build(alias, [entry], color=True)
            np.testing.assert_array_equal(actual.images[0][1], expected.images[0][1])
            self.assertEqual(
                str(root.resolve()),
                str(alias.resolve()),
            )
            outside = {**entry, "path": "../outside.png"}
            for operation in (lambda: LayerSearchIndex.build(alias, [outside], color=True),):
                with self.assertRaisesRegex(ValueError, "layer image escapes resource root"):
                    operation()

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
                    LayerSearchIndex.build,
                    root,
                    entries,
                    color=True,
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
                        LayerSearchIndex.build,
                        root,
                        entries,
                        color=True,
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
        locator.base._prepare_template = Mock(return_value=SimpleNamespace(flat=np.zeros((16, 16, 3), np.uint8)))
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
        self.assertTrue(pending, "backoff is still an unchecked required competitor")
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
                map_image=np.empty((0, 0), dtype=np.uint8),
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

    @staticmethod
    def navigation_scope(*tiers, candidate_allowed=True):
        return SimpleNamespace(
            allows_tier=lambda map_id, tier: map_id == "map02" and tier in tiers,
            allows_candidate=lambda map_id, tier, x, z: candidate_allowed,
        )

    def test_navigation_topology_excludes_unreachable_overlapping_floor_before_competition(self):
        locator = self.locator()
        locator.set_navigation_scope(self.navigation_scope("__main__"))
        result = locator.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.tier_id, "__main__")
        self.assertGreater(result.layer_margin, 0.08)
        locator._load.assert_not_called()
        locator.set_navigation_scope(None)
        unrestricted = locator.locate_near(None, (10, 10), None)
        self.assertIsNone(unrestricted.best.tier_id)
        self.assertEqual(unrestricted.layer_margin, 0)

    def test_navigation_topology_can_select_reachable_layer_over_unreachable_main(self):
        locator = self.locator(0.99, 0.95)
        locator.set_navigation_scope(self.navigation_scope("floor1"))
        result = locator.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.tier_id, "floor1")
        self.assertEqual({candidate.tier_id for candidate in result.candidates}, {"floor1"})

    def test_strong_excluded_main_peak_does_not_center_allowed_floor_search(self):
        locator = self.locator(0.99, 0.95)
        far = replace(self.result(0.99).best, world_x=1000, world_z=1000)
        locator.base.locate_global.return_value = replace(self.result(0.99), best=far, candidates=(far,))
        locator.set_navigation_scope(self.navigation_scope("floor1"))
        result = locator.locate_global(None, None)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.tier_id, "floor1")
        locator._load.return_value.locate_global.assert_called_once()
        locator._load.return_value.locate_near.assert_not_called()

    def test_failed_global_floor_decode_advances_the_next_candidate(self):
        locator = self.locator(0.5, 0.95)
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        locator._resource_workers = SimpleNamespace(closed=False, stop=threading.Event())
        floor = locator._load.return_value

        def load(entry):
            if entry["id"] == "floor1":
                locator._layer_errors["floor1"] = "broken"
                raise ValueError("broken")
            return floor

        locator._load = Mock(side_effect=load)
        locator._search(None, None)
        self.assertEqual(locator._scan_index, 1)
        self.assertEqual(locator._layer_errors, {"floor1": "broken"})

    def test_old_floor_error_does_not_advance_or_cancel_a_new_retry(self):
        locator = self.locator(0.5, 0.95)
        floor = locator._load.return_value
        locator.entries.append({**locator.entries[0], "id": "floor2"})
        locator._scan_index = 0
        locator._resource_workers = SimpleNamespace(closed=False, stop=threading.Event())
        retry = Future()
        locator._pending_layers["floor1"] = retry
        locator._layer_errors["floor1"] = "broken"
        locator._load = Mock(side_effect=ResourcePending())
        locator._search(None, None)
        self.assertEqual(locator._scan_index, 0)
        self.assertIs(locator._pending_layers["floor1"], retry)
        self.assertFalse(retry.cancelled())
        retry.set_result(floor)
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        locator._search(None, None)
        self.assertEqual(locator._scan_index, 1)
        self.assertIn("floor1", locator._cache)
        self.assertNotIn("floor1", locator._layer_errors)

    def test_pending_floor_resource_keeps_pending_reason_without_candidates(self):
        locator = self.locator(0.5, 0.95)
        locator.set_navigation_scope(self.navigation_scope("floor1"))
        locator._resource_workers = SimpleNamespace(closed=False, stop=threading.Event())
        locator._load = Mock(side_effect=ResourcePending())
        result = locator._search(None, None)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "layer_scan_pending")

    def test_pending_floor_without_main_peak_reports_wait_reason(self):
        locator = self.locator()
        locator.base.locate_global = Mock(return_value=None)
        locator.base.last_failure_reason = "minimap_no_candidates"
        locator._resource_workers = SimpleNamespace(closed=False, stop=threading.Event())
        locator._load = Mock(side_effect=ResourcePending())
        self.assertIsNone(locator._search(None, None))
        self.assertEqual(locator.last_failure_reason, "layer_scan_pending")

    def test_pending_index_without_main_peak_reports_wait_reason(self):
        locator = self.locator()
        locator.base.locate_global = Mock(return_value=None)
        locator.base.last_failure_reason = "minimap_no_candidates"
        locator.rank_layers = Mock(side_effect=ResourcePending())
        self.assertIsNone(locator.locate_global(None, None))
        self.assertEqual(locator.last_failure_reason, "layer_scan_pending")

    def test_inflight_index_is_rejected_when_scope_changes_its_floor_set(self):
        locator = self.locator()
        floor1 = locator.entries[0]
        floor2 = {**floor1, "id": "floor2"}
        locator.entries.append(floor2)
        first, second = Future(), Future()
        index = object()
        second.set_result(index)
        submit = Mock(side_effect=[first, second])
        locator._resource_workers = SimpleNamespace(
            closed=False,
            indexes=SimpleNamespace(submit=submit),
            resources=object(),
            stop=threading.Event(),
        )
        with self.assertRaises(ResourcePending):
            locator.prepare_layer_index(entries=[floor1])
        locator.prepare_layer_index(entries=[floor2])
        self.assertTrue(first.cancelled())
        self.assertIs(locator._search_index, index)
        self.assertEqual(locator._index_key, ("floor2",))

    def test_cached_ranking_is_bound_to_the_requested_floor_set(self):
        locator = self.locator()
        floor1 = locator.entries[0]
        floor2 = {**floor1, "id": "floor2"}
        locator.entries.append(floor2)
        locator.base._prepare_template = Mock(return_value=SimpleNamespace(flat=np.zeros((32, 32, 3), np.uint8)))
        locator._rank_raw_layers = Mock(
            side_effect=lambda _template, _width, *, wait_for_resources, with_positions=False, entries=None: [
                (0.8, entries[0]["id"])
            ]
        )
        frame = np.zeros((100, 100, 3), np.uint8)
        self.assertEqual(locator.rank_layers(frame, None, entries=[floor1]), [(0.8, "floor1")])
        self.assertEqual(locator.rank_layers(frame, None, entries=[floor2]), [(0.8, "floor2")])

    def test_requested_floor_does_not_wait_for_unrelated_inflight_decode(self):
        locator = self.locator()
        floor = locator._load.return_value
        floor2 = {
            **locator.entries[0],
            "id": "floor2",
            "bounds": {"west": 1000, "east": 1020, "south": 1000, "north": 1020},
        }
        locator.entries.append(floor2)
        locator._cache["floor1"] = floor
        locator._load = LayeredMinimapLocator._load.__get__(locator)
        pending = Future()
        locator._pending_layers["floor2"] = pending
        result, errors = [], []
        finished = threading.Event()

        def locate():
            try:
                result.append(locator.locate_proposed_layer(None, None, "floor1", wait_for_resources=True))
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=locate)
        thread.start()
        try:
            self.assertTrue(finished.wait(3))
        finally:
            if not pending.done():
                pending.set_result(None)
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0])
        floor.locate_global.assert_called_once()

    def test_scope_filters_layered_work_before_ranking_and_full_match(self):
        layered = self.locator(0.5, 0.95)
        allowed = layered.entries[0]
        layered.entries.append({**allowed, "id": "floor2"})
        layered.rank_layers = Mock(return_value=[(1.0, "floor2"), (0.9, "floor1")])
        layered.locate_proposed_layer = Mock(return_value=self.result(0.95, tier="floor1"))
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        maps.set_navigation_scope(self.navigation_scope("floor1"))
        maps._search_snapshot_proposals(None, None, 0, [("map02", self.result(0.5))])
        self.assertEqual(layered.rank_layers.call_args.kwargs["entries"], [allowed])
        self.assertEqual(
            [call.args[2] for call in layered.locate_proposed_layer.call_args_list],
            ["floor1"],
        )

    def test_failed_floor_does_not_stop_later_complete_competition(self):
        layered = self.locator(0.5, 0.95)
        layered.entries.append({**layered.entries[0], "id": "floor2"})
        layered.rank_layers = Mock(return_value=[(0.99, "floor1"), (0.95, "floor2")])
        floor2 = replace(self.result(0.95), best=replace(self.result(0.95).best, tier_id="floor2"))

        def locate(_frame, _geometry, tier, **_kwargs):
            if tier == "floor1":
                raise ResourcePending()
            return floor2

        layered.locate_proposed_layer = Mock(side_effect=locate)
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        result = maps._search_snapshot_proposals(
            np.zeros((100, 100, 3), np.uint8),
            None,
            0,
            [("map02", self.result(0.5))],
        )
        self.assertEqual([call.args[2] for call in layered.locate_proposed_layer.call_args_list], ["floor1", "floor2"])
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "layer_scan_pending")
        self.assertEqual(result.margin, 0)

    def test_map_competition_ignores_diagnostic_no_candidate_sources(self):
        maps = MapSetLocator(SimpleNamespace(), ("map01", "map02"))
        diagnostic = replace(self.result(0.99), reason="navigation_topology_no_candidates")
        allowed = replace(self.result(0.96), reason="accepted")
        result = maps._combine([("map01", diagnostic), ("map02", allowed)], 0, None)
        self.assertEqual(result.best.map_id, "map02")

    def test_layered_main_is_combined_even_when_no_floor_is_in_scope(self):
        layered = self.locator(0.95, 0.5)
        layered.rank_layers = Mock(side_effect=AssertionError("out-of-scope floor ranked"))
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        maps.set_navigation_scope(self.navigation_scope("__main__"))
        result = maps._search_snapshot_proposals(None, None, 0, [("map02", self.result(0.95))])
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.tier_id, "__main__")
        layered.rank_layers.assert_not_called()

    def test_excluded_layered_main_is_still_topology_rejected(self):
        layered = self.locator(0.99, 0.99)
        layered.rank_layers = Mock(return_value=[])
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        maps.set_navigation_scope(self.navigation_scope())
        result = maps._search_snapshot_proposals(None, None, 0, [("map02", self.result(0.99))])
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "navigation_topology_no_candidates")
        layered.rank_layers.assert_not_called()

    def test_reset_discards_completed_async_map_before_near_search(self):
        maps = MapSetLocator(SimpleNamespace(), ("map01",))
        stale = Future()
        stale.set_result(None)
        maps._search_future = stale
        maps._search_started_generation = maps._search_generation
        maps.reset_tracking()
        maps._active = "map01"
        maps._cache["map01"] = SimpleNamespace(locate_near=Mock())
        replacement = object()
        maps.locate_global = Mock(return_value=replacement)
        result = maps.locate_near(None, (0, 0), None)
        self.assertIs(result, replacement)
        maps._cache["map01"].locate_near.assert_not_called()
        maps.locate_global.assert_called_once_with(None, None)

    def test_wait_reason_ignores_errors_outside_the_current_scope(self):
        maps = MapSetLocator(SimpleNamespace(), ("map01", "map02"))
        maps._cache["map01"] = SimpleNamespace(entries=[{"id": "floor"}], resource_errors={"floor": "broken"})
        maps._cache["map02"] = SimpleNamespace(entries=[{"id": "floor"}], resource_errors={})
        maps.set_navigation_scope(self.navigation_scope("floor"))
        self.assertEqual(maps._layer_wait_reason(), "layer_scan_pending")

    def test_single_map_recovery_does_not_set_global_active_hint(self):
        layered = self.locator(0.95, 0.95)
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        result = maps.locate_on_map(None, "map02", (10, 10), None)
        self.assertEqual(result.status, "accepted")
        self.assertIsNone(maps._active)

    def test_navigation_topology_preserves_same_floor_spatial_ambiguity(self):
        locator = self.locator(0.5, 0.95)
        scope = self.navigation_scope("floor1")
        scope.allows_candidate = lambda map_id, tier, x, z: z < 12
        locator.set_navigation_scope(scope)
        floor = locator._load.return_value
        best = self.result(0.95).best
        rival = replace(best, world_z=15, map_y=-60, ncc=0.94, full_ncc=0.94)
        floor.locate_near.return_value = LocateResult("ambiguous", "test", best, (best, rival), 0.001, 1)
        result = locator.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.best.tier_id, "floor1")
        self.assertAlmostEqual(result.margin, 0.01)
        self.assertEqual(len(result.candidates), 2)

    def test_navigation_topology_vetoes_unreachable_best_without_publishing_an_alternative(self):
        locator = self.locator(0.5, 0.99)
        locator.set_navigation_scope(self.navigation_scope("floor1", candidate_allowed=False))
        result = locator.locate_near(None, (10, 10), None)
        self.assertTrue(result is None or result.status == "rejected", result)

    def test_navigation_topology_cannot_hide_pending_reachable_floor_resources(self):
        locator = self.locator(0.99, 0.5)
        locator.set_navigation_scope(self.navigation_scope("__main__", "floor1"))
        locator._load.side_effect = ResourcePending()
        result = locator.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.reason, "layer_scan_pending")
        self.assertEqual(result.margin, 0)

    def test_map_set_passes_topology_scope_to_active_layer_competition(self):
        layered = self.locator()
        maps = MapSetLocator(SimpleNamespace(), ("map02",))
        maps._cache["map02"] = layered
        maps._active = "map02"
        maps.set_navigation_scope(self.navigation_scope("floor1"))
        result = maps.locate_near(None, (10, 10), None)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.best.map_id, "map02")
        self.assertEqual(result.best.tier_id, "floor1")
        maps.set_navigation_scope(None)
        unrestricted = maps.locate_near(None, (10, 10), None)
        self.assertIsNone(unrestricted.best.tier_id)

    def test_map_set_cannot_promote_topology_rejected_high_confidence_candidate(self):
        for reachable_tiers in (("floor1",), ()):
            with self.subTest(reachable_tiers=reachable_tiers):
                layered = self.locator(0.99, 0.99)
                maps = MapSetLocator(SimpleNamespace(), ("map02",))
                maps._cache["map02"] = layered
                maps.set_navigation_scope(self.navigation_scope(*reachable_tiers, candidate_allowed=False))
                result = maps.locate_on_map(None, "map02", (10, 10), None)
                self.assertEqual(result.status, "rejected")
                self.assertTrue(result.reason.startswith("navigation_topology_"), result.reason)
                self.assertGreater(result.confidence, 0.95)
                self.assertIsNone(maps._active)

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
