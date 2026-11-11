"""Application-owned, serial preparation of missing localization resources."""

import logging
import threading
from collections import deque
from concurrent.futures import CancelledError
from pathlib import Path

from src.localization.calibrated_map_store import CalibratedMapStore
from src.localization.tile_preparation import DEFAULT_WIDTHS, prepare_bundle
from src.localization.tiled_map_image import CACHE_ROOT

logger = logging.getLogger(__name__)
_service = None
_service_lock = threading.Lock()


class TilePreparationService:
    def __init__(self, stop, *, cache_root=CACHE_ROOT, widths=DEFAULT_WIDTHS):
        self.stop = stop
        self.cache_root, self.widths = cache_root, widths
        self._condition = threading.Condition()
        self._queue = deque()
        self._states = {}
        self._thread = threading.Thread(target=self._run, name="VisualTilePreparation", daemon=True)
        self._thread.start()

    def request(self, maps_root):
        root = Path(maps_root).expanduser().resolve()
        with self._condition:
            if self.stop.is_set() or root in self._states:
                return
            self._states[root] = "pending"
            self._queue.append(root)
            self._condition.notify()

    def pending(self, maps_root):
        root = Path(maps_root).expanduser().resolve()
        with self._condition:
            return not self.stop.is_set() and self._states.get(root) == "pending"

    def close(self):
        self.stop.set()
        with self._condition:
            self._queue.clear()
            self._condition.notify_all()
        # A native codec call must finish naturally. The daemon checks stop
        # before each following tile and cannot publish an incomplete index.
        self._thread.join(timeout=0.2)

    def _run(self):
        while not self.stop.is_set():
            with self._condition:
                if not self._queue:
                    self._condition.wait(timeout=0.2)
                    continue
                root = self._queue.popleft()
            status = "ready"
            try:
                store = CalibratedMapStore(root)
                manifest = store._read_manifest()
                if manifest is None or store._is_tiled(manifest):
                    continue  # Unbundled maps and published tiles require no generation.
                store._map_files(manifest)  # Validate the configured bundle before generation.
                logger.info("Checking localization tiles: %s", root)
                reused = generated = 0
                for result in prepare_bundle(
                    root.parent, self.widths, cache_root=self.cache_root, verify=True, stop=self.stop
                ):
                    if result["reused"]:
                        reused += 1
                    else:
                        generated += 1
                        logger.info("Prepared localization image %s: %s", reused + generated, result["path"])
                logger.info("Localization tiles ready: generated=%s reused=%s", generated, reused)
            except CancelledError:
                status = "cancelled"
            except Exception:
                status = "failed"
                logger.exception("Localization tile preparation failed; retaining original-image fallback: %s", root)
            finally:
                with self._condition:
                    self._states[root] = status


def request_preparation(maps_root):
    """Only an application startup owner may enable automatic generation."""
    with _service_lock:
        service = _service
    if service is not None:
        service.request(maps_root)


def preparation_pending(maps_root):
    with _service_lock:
        service = _service
    return service is not None and service.pending(maps_root)


def start_startup_preparation(exit_event):
    global _service
    try:
        from src.core.global_config_store import get_global_config
        from src.core.PositionConfig import CONFIG_VISUAL_MAP_ROOT, DEFAULT_VISUAL_MAP_ROOT, POSITION_CONFIG_NAME

        config = get_global_config(POSITION_CONFIG_NAME)
        root = str(config.get(CONFIG_VISUAL_MAP_ROOT) or DEFAULT_VISUAL_MAP_ROOT).strip() or DEFAULT_VISUAL_MAP_ROOT
        with _service_lock:
            if _service is None:
                _service = TilePreparationService(exit_event)
            _service.request(root)
        return _service
    except Exception:
        logger.exception("Could not start localization tile preparation; retaining original-image fallback")
        return None
