"""Automatic base-map selection without packet coordinates or WS map IDs."""

import time
from collections import OrderedDict
from dataclasses import replace

import numpy as np

from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MinimapMapLocator,
    candidate_margin,
    deduplicate_candidates,
    minimap_map_scale,
)


class MapSetLocator:
    def __init__(self, store, map_ids, *, workers=None):
        self.store, self.map_ids = store, tuple(map_ids)
        self.calibration = MapCalibration(
            np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]])
        )
        self._frame_gate = MinimapMapLocator(np.zeros((1, 1), dtype=np.uint8), self.calibration)
        self._cache = OrderedDict()
        self._active = None
        self._scan_index = 0
        self._workers = workers
        self._pending_maps = {}
        self._resource_retry_at = {}

    def prewarm(self):
        if self._workers is not None and not self._workers.closed:
            for mid in self.map_ids:
                if (
                    mid not in self._cache
                    and mid not in self._pending_maps
                    and time.monotonic() >= self._resource_retry_at.get(mid, 0)
                ):
                    self._pending_maps[mid] = self._workers.resources.submit(self.store.load, mid)

    def close(self):
        if self._workers is not None:
            self._workers.close()
        self._pending_maps.clear()
        self._cache.clear()

    def _scale(self, width):
        return minimap_map_scale(width)

    def reset_tracking(self):
        self._active = None
        for locator in self._cache.values():
            if hasattr(locator, "_recent"):
                locator._recent = None

    def _load(self, map_id):
        if map_id not in self._cache:
            self._cache[map_id] = self.store.load(map_id)[1]
            while len(self._cache) > max(2, len(self.map_ids)):
                self._cache.popitem(last=False)
        self._cache.move_to_end(map_id)
        return self._cache[map_id]

    def _combine(self, results, started, prior):
        results = [(mid, r) for mid, r in results if r is not None]
        if not results:
            return None
        candidates = []
        for mid, result in results:
            for c in result.candidates:
                x, y = self.calibration.world_to_map(c.world_x, c.world_z)
                candidates.append(replace(c, map_x=x, map_y=y, map_id=mid))
        # A world coordinate shared by different map spaces is not one candidate.
        spatial = deduplicate_candidates([replace(c, tier_id=None) for c in candidates])
        best = max(candidates, key=lambda c: c.ncc)
        original_best = best
        chosen_result = next(r for mid, r in results if mid == best.map_id)
        # Preserve unknown tier identity decided by the within-map competition.
        if chosen_result.best.tier_id is None:
            best = replace(best, tier_id=None)
        margin = candidate_margin(spatial)
        margin = min(margin, chosen_result.margin)
        status = self._candidate_status(best, margin)
        pending_resources = any(result.reason == "layer_scan_pending" for _, result in results)
        others = [c for c in sorted(candidates, key=lambda c: c.ncc, reverse=True) if c is not original_best]
        result = LocateResult(
            "rejected" if pending_resources else status,
            "layer_scan_pending" if pending_resources else "map competition",
            best,
            (best, *others[:7]),
            time.perf_counter() - started,
            chosen_result.scale,
            prior is not None,
            spatial_margin=margin,
            layer_margin=chosen_result.layer_margin,
        )
        if best.ncc >= 0.85 and best.full_ncc >= 0.65 and margin >= 0.08:
            self._active = best.map_id
        return result

    @staticmethod
    def _candidate_status(best, margin):
        if best.ncc < 0.55:
            return "rejected"
        if best.ncc >= 0.70 and margin >= 0.15:
            return "accepted"
        return "ambiguous"

    def _warm_base_resources(self):
        if self._workers is not None:
            if self._workers.closed:
                return False
            self.prewarm()
            ready = True
            for mid in self.map_ids:
                if mid in self._cache:
                    continue
                future = self._pending_maps.get(mid)
                if future is None and time.monotonic() < self._resource_retry_at.get(mid, 0):
                    continue
                if future is None or not future.done():
                    ready = False
                    continue
                try:
                    locator = future.result()[1]
                except (OSError, ValueError):
                    del self._pending_maps[mid]
                    self._resource_retry_at[mid] = time.monotonic() + 5
                    continue
                if hasattr(locator, "_resource_workers"):
                    locator._resource_workers = self._workers
                self._cache[mid] = locator
                if len(self.map_ids) == 1:
                    # An explicitly selected map retains its affine odometry
                    # transform; the normalized frame is only for map competition.
                    self.calibration = getattr(locator, "calibration", self.calibration)
                del self._pending_maps[mid]
            return ready and bool(self._cache)
        for mid in self.map_ids:
            if mid not in self._cache:
                # One resource per sample; partial map sets cannot prove uniqueness.
                if time.monotonic() < self._resource_retry_at.get(mid, 0):
                    continue
                try:
                    self._load(mid)
                except (OSError, ValueError):
                    self._resource_retry_at[mid] = time.monotonic() + 5
                return False
        return bool(self._cache)

    def _compare_base_maps(self, frame, geometry):
        if self._workers is not None:
            # One stable frame for all candidates; no worker retains capture buffers.
            snapshot = frame.copy()
            snapshot.setflags(write=False)
            futures = []
            for mid in self.map_ids:
                if mid not in self._cache:
                    continue
                locator = self._load(mid)
                base = getattr(locator, "base", locator)
                futures.append((mid, self._workers.matches.submit(base.locate_global, snapshot, geometry)))
            # Drain every task even on failure, before any later frame can touch
            # these per-map caches. Preserve map order for deterministic ties.
            results, failure = [], None
            for mid, future in futures:
                try:
                    results.append((mid, future.result()))
                except Exception as exc:
                    failure = failure or exc
            if failure is not None:
                raise failure
            return results
        results = []
        for mid in self.map_ids:
            if mid not in self._cache:
                continue
            locator = self._load(mid)
            base = getattr(locator, "base", locator)
            results.append((mid, base.locate_global(frame, geometry)))
        return results

    def locate_global(self, frame, geometry):
        if self._frame_gate._prepare_template(frame, geometry) is None:
            self.reset_tracking()
            return None
        started = time.perf_counter()
        if not self._warm_base_resources():
            return None
        # Map identity is only a search hint. Re-evaluate its visual evidence on
        # this frame; do not discard the hint merely because resource decoding
        # took two seconds. Position freshness is enforced by the source/tracker.
        active_result = self._active_result(frame, geometry)
        if active_result is not None:
            return active_result
        # First compare inexpensive main-map searches. Expanding every weak map
        # into every floor makes a cold sample take minutes on a real asset set.
        results = self._compare_base_maps(frame, geometry)
        combined = self._combine(results, started, None)
        if combined is None:
            return None
        strong = combined.confidence >= 0.85 and combined.best.full_ncc >= 0.65 and combined.margin >= 0.08
        available = [mid for mid in self.map_ids if mid in self._cache]
        selected = combined.best.map_id if strong else available[self._scan_index % len(available)]
        if not strong:
            self._scan_index += 1
        locator = self._load(selected)
        if hasattr(locator, "base"):
            base_result = next(r for mid, r in results if mid == selected)
            layered = locator.locate_global(frame, geometry, base_result=base_result)
            results = [(mid, layered if mid == selected else r) for mid, r in results]
        return self._combine(results, started, None)

    def _active_result(self, frame, geometry):
        if not self._active:
            return None
        result = self.locate_near(frame, (0, 0), geometry, search_radius_m=80, use_global=True)
        if result is not None and result.reason == "layer_scan_pending":
            return result
        if result is not None and result.confidence >= 0.85 and result.best.full_ncc >= 0.65 and result.margin >= 0.08:
            return result
        return None

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0, use_global=False):
        started = time.perf_counter()
        if not self._active:
            return self.locate_global(frame, geometry)
        locator = self._load(self._active)
        result = (
            locator.locate_global(frame, geometry)
            if use_global
            else locator.locate_near(frame, previous_world, geometry, search_radius_m=search_radius_m)
        )
        if result is None:
            self.reset_tracking()
        return self._combine([(self._active, result)], started, previous_world)
