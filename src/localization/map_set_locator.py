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
    def __init__(self, store, map_ids):
        self.store, self.map_ids = store, tuple(map_ids)
        self.calibration = MapCalibration(
            np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]])
        )
        self._frame_gate = MinimapMapLocator(np.zeros((1, 1), dtype=np.uint8), self.calibration)
        self._cache = OrderedDict()
        self._active = None
        self._scan_index = 0

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
        status = "rejected" if best.ncc < 0.55 else "accepted" if best.ncc >= 0.70 and margin >= 0.15 else "ambiguous"
        pending_resources = chosen_result.reason == "layer_scan_pending"
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

    def locate_global(self, frame, geometry):
        if self._frame_gate._prepare_template(frame, geometry) is None:
            self.reset_tracking()
            return None
        started = time.perf_counter()
        for mid in self.map_ids:
            if mid not in self._cache:
                # Decode at most one large base resource in a sample. Cached
                # resources survive lost/pending fixes, but no partial map set
                # can supply acquisition evidence.
                self._load(mid)
                return None
        # Map identity is only a search hint. Re-evaluate its visual evidence on
        # this frame; do not discard the hint merely because resource decoding
        # took two seconds. Position freshness is enforced by the source/tracker.
        if self._active:
            result = self.locate_near(frame, (0, 0), geometry, search_radius_m=80, use_global=True)
            if result is not None and result.reason == "layer_scan_pending":
                return result
            if (
                result is not None
                and result.confidence >= 0.85
                and result.best.full_ncc >= 0.65
                and result.margin >= 0.08
            ):
                return result
        # First compare inexpensive main-map searches. Expanding every weak map
        # into every floor makes a cold sample take minutes on a real asset set.
        results = []
        for mid in self.map_ids:
            locator = self._load(mid)
            base = getattr(locator, "base", locator)
            results.append((mid, base.locate_global(frame, geometry)))
        combined = self._combine(results, started, None)
        if combined is None:
            return None
        strong = combined.confidence >= 0.85 and combined.best.full_ncc >= 0.65 and combined.margin >= 0.08
        selected = combined.best.map_id if strong else self.map_ids[self._scan_index % len(self.map_ids)]
        if not strong:
            self._scan_index += 1
        locator = self._load(selected)
        if hasattr(locator, "base"):
            base_result = next(r for mid, r in results if mid == selected)
            layered = locator.locate_global(frame, geometry, base_result=base_result)
            results = [(mid, layered if mid == selected else r) for mid, r in results]
        return self._combine(results, started, None)

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
