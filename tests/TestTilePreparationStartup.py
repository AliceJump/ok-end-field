import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.localization import tile_preparation, tile_preparation_service
from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.localization_workers import DecodeBudget, LocalizationWorkers
from src.localization.map_set_locator import MapSetLocator
from src.localization.tile_preparation import prepare_image
from src.localization.tile_preparation_service import TilePreparationService
from src.localization.tiled_map_image import TiledMapImage


class TestTilePreparationStartup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "cache"
        self.maps = self.root / "maps"
        self.maps.mkdir()
        self.path = self.maps / "map.png"
        self.image = np.random.default_rng(12).integers(0, 255, (64, 64, 3), dtype=np.uint8)
        cv2.imwrite(str(self.path), self.image)
        manifest = {
            "kind": "okef-visual-localization",
            "schema_version": 1,
            "maps_root": "maps",
            "layers_root": "layers",
            "layer_catalog": "layers/catalog.json",
            "base_maps": [
                {
                    "map_id": "map01",
                    "zoom": "4",
                    "image": "maps/map.png",
                    "mapping": "maps/mapping.json",
                    "width": 64,
                    "height": 64,
                }
            ],
        }
        (self.root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (self.root / "layers").mkdir()
        (self.root / "layers/catalog.json").write_text('{"schema_version": 1, "layers": []}', encoding="utf-8")

    def service(self):
        service = TilePreparationService(threading.Event(), cache_root=self.cache, widths=(1920,))
        self.addCleanup(service.close)
        return service

    def finish(self, service):
        deadline = time.monotonic() + 5
        while service.pending(self.maps) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service.pending(self.maps), "preparation did not finish")

    def prepare(self, **kwargs):
        return prepare_image(self.path, (64, 64), (0.25,), cache_root=self.cache, tile_size=32, **kwargs)

    def test_first_start_generates_and_later_start_reuses_without_decoding(self):
        first = self.service()
        first.request(self.maps)
        self.finish(first)
        self.assertEqual(first._states[self.maps], "ready")
        self.assertIsNotNone(TiledMapImage.find(self.path, (64, 64), cache_root=self.cache))
        first.close()
        with patch.object(tile_preparation.cv2, "imread", side_effect=AssertionError("decoded valid original")):
            second = self.service()
            second.request(self.maps)
            self.finish(second)
            self.assertEqual(second._states[self.maps], "ready")

    def test_startup_uses_configured_root_exit_signal_and_one_service(self):
        from src.core.PositionConfig import CONFIG_VISUAL_MAP_ROOT

        exit_event = threading.Event()
        service = Mock()
        with (
            patch(
                "src.core.global_config_store.get_global_config", return_value={CONFIG_VISUAL_MAP_ROOT: str(self.maps)}
            ),
            patch.object(tile_preparation_service, "_service", None),
            patch.object(tile_preparation_service, "TilePreparationService", return_value=service) as factory,
        ):
            self.assertIs(tile_preparation_service.start_startup_preparation(exit_event), service)
            self.assertIs(tile_preparation_service.start_startup_preparation(exit_event), service)
        factory.assert_called_once_with(exit_event)
        service.request.assert_called_with(str(self.maps))

    def test_missing_or_corrupt_derived_files_are_rebuilt(self):
        for damage in ("missing", "corrupt", "index"):
            with self.subTest(damage=damage):
                self.prepare()
                source = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
                path = source.root / source.metadata["tiles"]["0,0"]["path"]
                if damage == "index":
                    (source.root / "index.json").write_text("broken", encoding="utf-8")
                elif damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"damaged")
                result = self.prepare(verify=True)
                self.assertFalse(result["reused"])
                repaired = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
                repaired.verify_resources()
                np.testing.assert_array_equal(repaired[:, :], self.image)

    def test_updated_source_gets_new_tiles_on_next_start(self):
        first = self.service()
        first.request(self.maps)
        self.finish(first)
        first.close()
        cv2.imwrite(str(self.path), np.zeros_like(self.image))
        second = self.service()
        second.request(self.maps)
        self.finish(second)
        source = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
        np.testing.assert_array_equal(source[:, :], np.zeros_like(self.image))

    def test_cancelled_rebuild_preserves_index_removes_partial_generation_and_releases_budget(self):
        self.prepare()
        source = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
        index = (source.root / "index.json").read_bytes()
        generations = set(source.root.glob("generation-*"))
        stop = threading.Event()
        budget = DecodeBudget(available=lambda: 0)
        write = tile_preparation._write_image

        def cancel_after_write(*args, **kwargs):
            result = write(*args, **kwargs)
            stop.set()
            return result

        with (
            patch.object(tile_preparation, "_DECODE_BUDGET", budget),
            patch.object(tile_preparation, "_write_image", side_effect=cancel_after_write),
            self.assertRaises(CancelledError),
        ):
            self.prepare(force=True, stop=stop)
        self.assertEqual((source.root / "index.json").read_bytes(), index)
        self.assertEqual(set(source.root.glob("generation-*")), generations)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_write_failure_preserves_original_index_and_releases_budget(self):
        self.prepare()
        source = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
        index = (source.root / "index.json").read_bytes()
        generations = set(source.root.glob("generation-*"))
        budget = DecodeBudget(available=lambda: 0)
        with (
            patch.object(tile_preparation, "_DECODE_BUDGET", budget),
            patch.object(tile_preparation, "_write_image", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.prepare(force=True)
        self.assertEqual((source.root / "index.json").read_bytes(), index)
        self.assertEqual(set(source.root.glob("generation-*")), generations)
        self.assertEqual((budget.active, budget.reserved_bytes), (0, 0))

    def test_failed_repair_cannot_reuse_known_broken_index(self):
        self.prepare()
        source = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
        self.addCleanup(TiledMapImage.allow_prepared, source.root)
        (source.root / source.metadata["tiles"]["0,0"]["path"]).write_bytes(b"bad")
        with (
            patch.object(tile_preparation, "_write_image", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.prepare(verify=True)
        self.assertIsNone(TiledMapImage.open_prepared(self.path, (64, 64), cache_root=self.cache))
        self.prepare(verify=True)
        repaired = TiledMapImage.find(self.path, (64, 64), cache_root=self.cache)
        np.testing.assert_array_equal(repaired[:, :], self.image)

    def test_shutdown_while_waiting_for_memory_does_not_decode_or_leak_budget(self):
        budget = DecodeBudget(available=lambda: 0)
        budget.acquire(100)
        stop, waiting = threading.Event(), threading.Event()
        acquire = budget.acquire

        def attempt(size):
            waiting.set()
            return acquire(size)

        with (
            patch.object(tile_preparation, "_DECODE_BUDGET", budget),
            patch.object(budget, "acquire", side_effect=attempt),
            patch.object(tile_preparation.cv2, "imread", side_effect=AssertionError("decoded while budget busy")),
            ThreadPoolExecutor(1) as workers,
        ):
            future = workers.submit(self.prepare, stop=stop)
            try:
                self.assertTrue(waiting.wait(3))
            finally:
                stop.set()
            with self.assertRaises(CancelledError):
                future.result(timeout=3)
        self.assertEqual((budget.active, budget.reserved_bytes), (1, 100))
        budget.release(100)

    def test_navigation_waits_for_single_background_job_then_can_load(self):
        entered, release = threading.Event(), threading.Event()
        generate = tile_preparation.prepare_bundle

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release preparation")
            yield from generate(*args, **kwargs)

        service = self.service()
        store = CalibratedMapStore(self.maps)
        workers = LocalizationWorkers()
        locator = MapSetLocator(store, ["map01"], workers=workers)
        self.addCleanup(locator.close)
        with (
            patch.object(tile_preparation_service, "_service", service),
            patch.object(tile_preparation_service, "prepare_bundle", side_effect=delayed) as prepare,
            patch.object(store, "decode_memory_bytes", return_value=1),
            patch.object(store, "load", return_value=("map01", Mock())),
        ):
            try:
                service.request(self.maps)
                self.assertTrue(entered.wait(3))
                for _ in range(3):
                    tile_preparation_service.request_preparation(self.maps)
                    locator.prewarm()
                    self.assertFalse(locator._warm_base_resources())
                self.assertFalse(locator._pending_maps)
            finally:
                release.set()
            self.finish(service)
            prepare.assert_called_once()
            locator.prewarm()
            self.assertIn("map01", locator._pending_maps)
            locator._pending_maps["map01"].result(timeout=3)

    def test_failure_unblocks_navigation_without_repeated_build_loop(self):
        service = self.service()
        with (
            patch.object(tile_preparation_service, "prepare_bundle", side_effect=OSError("disk full")) as prepare,
            self.assertLogs(tile_preparation_service.logger, level="ERROR"),
        ):
            service.request(self.maps)
            self.finish(service)
            service.request(self.maps)
        prepare.assert_called_once()
        self.assertEqual(service._states[self.maps], "failed")

    def test_app_exit_cancels_generation_and_leaves_no_worker(self):
        service = self.service()
        entered = threading.Event()

        def waiting(*args, stop, **kwargs):
            entered.set()
            stop.wait(3)
            raise CancelledError()

        with patch.object(tile_preparation_service, "prepare_bundle", side_effect=waiting):
            service.request(self.maps)
            self.assertTrue(entered.wait(3))
            service.close()
        self.assertFalse(service._thread.is_alive())
        self.assertFalse(service.pending(self.maps))
        self.assertEqual(service._states[self.maps], "cancelled")

    def test_unbundled_legacy_directory_keeps_original_loader(self):
        (self.root / "manifest.json").unlink()
        service = self.service()
        with patch.object(tile_preparation_service, "prepare_bundle") as prepare:
            service.request(self.maps)
            self.finish(service)
        prepare.assert_not_called()
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
