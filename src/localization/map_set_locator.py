"""Automatic base-map selection without packet coordinates or WS map IDs."""

import time
from collections import OrderedDict
from dataclasses import replace

import numpy as np

from src.localization.localization_workers import ResourcePending
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MinimapMapLocator,
    candidate_margin,
    deduplicate_candidates,
    minimap_map_scale,
    strong_candidate,
)


class MapSetLocator:
    def __init__(self, store, map_ids, *, workers=None, require_all_maps=False, async_search=False):
        self.color = True
        self.store, self.map_ids = store, tuple(map_ids)
        self.calibration = MapCalibration(
            np.array([[0.25, 0, 0], [0, -0.25, 0]]), np.array([[4.0, 0, 0], [0, -4.0, 0]])
        )
        self._frame_gate = MinimapMapLocator(np.zeros((1, 1, 3), dtype=np.uint8), self.calibration)
        self._cache = OrderedDict()
        self._active = None
        self._scan_index = 0
        self._workers = workers
        self._require_all_maps = require_all_maps
        self._pending_maps = {}
        self._resource_retry_at = {}
        self._resource_errors = {}
        self.last_failure_reason = ""
        self._cold_layers = False
        self._proposal_index = 0
        self._skip_index_once = False
        self._cold_map_order = ()
        self._cold_map_cursor = 0
        self._cold_proposal = None
        self._navigation_scope = None
        self._async_search = bool(async_search and workers is not None)
        self._search_future = None
        self._search_generation = 0
        self._search_started_generation = 0
        self._search_shape = None
        self._search_ready_callback = None
        self._closed = False

    def set_search_ready_callback(self, callback):
        """Wake the owner for a fresh observation; never publish job results."""
        self._search_ready_callback = callback

    def _notify_search_ready(self, future):
        callback = self._search_ready_callback
        if not self._closed and future is self._search_future and callable(callback):
            callback()

    @property
    def last_failure_reason(self):
        if getattr(self, "_search_future", None) is not None:
            return "visual_global_search_pending"
        return self._last_failure_reason

    @last_failure_reason.setter
    def last_failure_reason(self, value):
        self._last_failure_reason = value

    def set_navigation_scope(self, scope):
        self._navigation_scope = scope
        if self._search_future is not None and not self._search_future.done():
            return
        for locator in self._cache.values():
            setter = getattr(locator, "set_navigation_scope", None)
            if callable(setter):
                setter(scope)

    def prewarm(self):
        if self._preparation_pending():
            return
        if self._workers is not None and not self._workers.closed:
            for mid in self.map_ids:
                if (
                    mid not in self._cache
                    and mid not in self._pending_maps
                    and time.monotonic() >= self._resource_retry_at.get(mid, 0)
                ):
                    estimate = getattr(self.store, "decode_memory_bytes", None)
                    size = estimate(mid) if callable(estimate) else 128 * 1024**2
                    self._pending_maps[mid] = self._workers.resources.submit(self.store.load, mid, memory_bytes=size)

    def _preparation_pending(self):
        pending = getattr(self.store, "preparation_pending", None)
        return callable(pending) and pending() is True

    def close(self):
        self._closed = True
        if self._workers is not None:
            self._workers.close()
        self._pending_maps.clear()
        if self._search_future is not None and not self._search_future.done():
            # Native calls cannot be interrupted. Their caches remain owned by
            # the retiring job until it exits, then are closed exactly once.
            self._search_future.add_done_callback(lambda _: self._close_maps())
            return
        self._close_maps()

    def _close_maps(self):
        self._search_future = None
        for locator in self._cache.values():
            close = getattr(locator, "close", None)
            if callable(close):
                close()
        self._cache.clear()

    def _scale(self, width):
        return minimap_map_scale(width)

    def reset_tracking(self):
        self._search_generation += 1
        if self._search_future is not None and not self._search_future.done():
            return
        self._clear_search_hint()

    def _clear_search_hint(self):
        self._active = None
        self._cold_layers = False
        self._cold_proposal = None
        self._proposal_index = 0
        self._cold_map_order = ()
        self._cold_map_cursor = 0
        self._skip_index_once = False
        for locator in self._cache.values():
            if hasattr(locator, "_recent"):
                locator._recent = None
            reset = getattr(locator, "reset_proposals", None)
            if callable(reset):
                reset()

    def _load(self, map_id):
        if map_id not in self._cache:
            self._cache[map_id] = self.store.load(map_id)[1]
            while len(self._cache) > max(2, len(self.map_ids)):
                self._cache.popitem(last=False)
        self._cache.move_to_end(map_id)
        setter = getattr(self._cache[map_id], "set_navigation_scope", None)
        if callable(setter):
            setter(self._navigation_scope)
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
        topology_rejected = chosen_result.reason.startswith("navigation_topology_")
        reason = (
            "layer_scan_pending"
            if pending_resources
            else (chosen_result.reason if topology_rejected else "map competition")
        )
        others = [c for c in sorted(candidates, key=lambda c: c.ncc, reverse=True) if c is not original_best]
        result = LocateResult(
            "rejected" if pending_resources or topology_rejected else status,
            reason,
            best,
            (best, *others[:7]),
            time.perf_counter() - started,
            chosen_result.scale,
            prior is not None,
            spatial_margin=margin,
            layer_margin=chosen_result.layer_margin,
        )
        if not topology_rejected and strong_candidate(best, margin):
            self._active = best.map_id
            self._cold_layers = False
            self._proposal_index = 0
            self._cold_map_cursor = 0
            self._cold_proposal = None
        return result

    @staticmethod
    def _candidate_status(best, margin):
        if best.color:
            if best.ncc < 0.45:
                return "rejected"
            return "accepted" if strong_candidate(best, margin) else "ambiguous"
        if best.ncc < 0.55:
            return "rejected"
        if best.ncc >= 0.70 and margin >= 0.15:
            return "accepted"
        return "ambiguous"

    def _warm_base_resources(self):
        if self._preparation_pending():
            return False
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
                except (OSError, ValueError) as exc:
                    del self._pending_maps[mid]
                    self._resource_retry_at[mid] = time.monotonic() + 5
                    self._resource_errors[mid] = str(exc)
                    continue
                if hasattr(locator, "_resource_workers"):
                    locator._resource_workers = self._workers
                self._cache[mid] = locator
                self._resource_errors.pop(mid, None)
                if len(self.map_ids) == 1:
                    # An explicitly selected map retains its affine odometry
                    # transform; the normalized frame is only for map competition.
                    self.calibration = getattr(locator, "calibration", self.calibration)
                del self._pending_maps[mid]
            return ready and bool(self._cache) and (not self._require_all_maps or not self._resource_errors)
        for mid in self.map_ids:
            if mid not in self._cache:
                # One resource per sample; partial map sets cannot prove uniqueness.
                if time.monotonic() < self._resource_retry_at.get(mid, 0):
                    continue
                try:
                    self._load(mid)
                    self._resource_errors.pop(mid, None)
                except (OSError, ValueError) as exc:
                    self._resource_retry_at[mid] = time.monotonic() + 5
                    self._resource_errors[mid] = str(exc)
                return False
        return bool(self._cache) and (not self._require_all_maps or not self._resource_errors)

    def _compare_base_maps(self, frame, geometry, *, skip_map_id=None):
        if self._workers is not None:
            # One stable frame for all candidates; no worker retains capture buffers.
            snapshot = frame if frame.flags.owndata and not frame.flags.writeable else frame.copy()
            snapshot.setflags(write=False)
            bases = {}
            for mid in self.map_ids:
                if mid not in self._cache or mid == skip_map_id:
                    continue
                locator = self._load(mid)
                bases[mid] = getattr(locator, "base", locator)

            def search_area(mid):
                shape = getattr(getattr(bases[mid], "map_image", None), "shape", (0, 0))
                return shape[0] * shape[1]

            # Start the longest raster first instead of queuing it behind a
            # small map. Stable sorting and collection retain original ties.
            futures, failure = {}, None
            try:
                for mid in sorted(bases, key=search_area, reverse=True):
                    futures[mid] = self._workers.matches.submit(bases[mid].locate_global, snapshot, geometry)
            except Exception as exc:
                failure = exc
            # Drain every task even on failure, before any later frame can touch
            # these per-map caches. Preserve map order for deterministic ties.
            results = []
            for mid in bases:
                if mid not in futures:
                    continue
                try:
                    results.append((mid, futures[mid].result()))
                except Exception as exc:
                    failure = failure or exc
            if failure is not None:
                raise failure
            return results
        results = []
        for mid in self.map_ids:
            if mid not in self._cache or mid == skip_map_id:
                continue
            locator = self._load(mid)
            base = getattr(locator, "base", locator)
            results.append((mid, base.locate_global(frame, geometry)))
        return results

    def _prepare_base_search(self, frame):
        if self._workers is None:
            return True
        if self._workers.closed:
            return False
        ready = True
        for locator in self._cache.values():
            base = getattr(locator, "base", locator)
            prepare = getattr(base, "prepare_global_search", None)
            if callable(prepare) and not prepare(frame.shape[1], self._workers.resources):
                ready = False
        return ready

    def locate_global(self, frame, geometry):
        self.last_failure_reason = ""
        if self._closed:
            self.last_failure_reason = "visual_locator_closed"
            return None
        if self._frame_gate._prepare_template(frame, geometry) is None:
            self.last_failure_reason = self._frame_gate.last_failure_reason
            self.reset_tracking()
            return None
        started = time.perf_counter()
        if self._async_search and self._search_future is not None:
            return self._locate_async(frame, geometry, started)
        if not self._warm_base_resources():
            pending = ",".join(mid for mid in self.map_ids if mid not in self._cache)
            self.last_failure_reason = (
                f"visual_map_resource_error: {self._resource_errors}"
                if self._resource_errors
                else f"visual_map_resources_pending: maps={pending}"
            )
            return None
        if not self._active and not self._prepare_base_search(frame):
            self.last_failure_reason = "visual_coarse_resources_pending"
            return None
        if self._async_search:
            return self._locate_async(frame, geometry, started)
        return self._search_global_ready(frame, geometry, started)

    def _locate_async(self, frame, geometry, started):
        future = self._search_future
        if future is not None:
            if self._search_shape != frame.shape and self._search_started_generation == self._search_generation:
                self.reset_tracking()
            if not future.done():
                self.last_failure_reason = "visual_global_search_pending"
                return None
            self._search_future = None
            future.result()  # Surface resource failures; never publish this old-frame result.
            if self._search_started_generation != self._search_generation:
                self._clear_search_hint()
        # A completed full competition can seed only a search hint. Both the
        # current frame's nearby competition and the tracker's confirmation
        # gates must still pass before any coordinate becomes trusted.
        result = self._active_result(frame, geometry)
        if result is not None:
            return result
        if not self._prepare_base_search(frame):
            self.last_failure_reason = "visual_coarse_resources_pending"
            return None
        snapshot = frame.copy()
        snapshot.setflags(write=False)
        self._search_shape = snapshot.shape
        self._search_started_generation = self._search_generation
        self._search_future = self._workers.acquisitions.submit(self._search_global_ready, snapshot, geometry, started)
        self._search_future.add_done_callback(self._notify_search_ready)
        self.last_failure_reason = "visual_global_search_pending"
        return None

    def _search_global_ready(self, frame, geometry, started):
        if self._async_search:
            return self._search_snapshot(frame, geometry, started)
        # Map identity is only a search hint. Re-evaluate its visual evidence on
        # this frame; do not discard the hint merely because resource decoding
        # took two seconds. Position freshness is enforced by the source/tracker.
        active_result = None if self._async_search else self._active_result(frame, geometry)
        if active_result is not None:
            return active_result
        if self._cold_layers:
            result = self._locate_cold_layers(frame, geometry, started)
            if result is not None or self._cold_layers:
                return result
        # First compare inexpensive main-map searches. Expanding every weak map
        # into every floor makes a cold sample take minutes on a real asset set.
        results = self._compare_base_maps(frame, geometry)
        for mid, result in results:
            seed = getattr(self._cache[mid], "seed_proposals", None)
            if callable(seed):
                seed(result)
        combined = self._combine(results, started, None)
        if combined is None:
            failures = "; ".join(
                f"{mid}={getattr(self._cache[mid], 'last_failure_reason', '') or 'minimap_no_candidates'}"
                for mid, _ in results
            )
            self.last_failure_reason = f"minimap_no_map_candidates: {failures}"
            return None
        strong = strong_candidate(combined.best, combined.margin)
        use_index = not self._skip_index_once
        self._skip_index_once = False
        if not strong and use_index and any(getattr(locator, "entries", ()) for locator in self._cache.values()):
            # Position ambiguity within one map is not a reason to give every
            # map equal decode time. This ordering is a hint, never confidence.
            self._cold_map_order = tuple(
                mid
                for mid, _ in sorted(results, key=lambda item: item[1].confidence if item[1] else -1, reverse=True)
                if getattr(self._cache[mid], "entries", ())
            )
            self._cold_layers = True
            # Only this background job owns an immutable snapshot. Keep its
            # competitor results on the call stack, never in a cross-frame cache.
            return self._locate_cold_layers(
                frame, geometry, started, base_results=results if self._async_search else None
            )
        available = [mid for mid in self.map_ids if mid in self._cache]
        selected = combined.best.map_id if strong else available[self._scan_index % len(available)]
        if not strong:
            self._scan_index += 1
        locator = self._load(selected)
        if hasattr(locator, "base"):
            base_result = next(r for mid, r in results if mid == selected)
            options = {"use_index": use_index} if callable(getattr(locator, "rank_layers", None)) else {}
            layered = locator.locate_global(frame, geometry, base_result=base_result, **options)
            results = [(mid, layered if mid == selected else r) for mid, r in results]
        return self._combine(results, started, None)

    def _search_snapshot(self, frame, geometry, started):
        """Keep one frame's complete main-map evidence on this job's stack only."""
        results = self._compare_base_maps(frame, geometry)
        for mid, result in results:
            seed = getattr(self._cache[mid], "seed_proposals", None)
            if callable(seed):
                seed(result)
        combined = self._combine(results, started, None)
        if combined is None:
            self.last_failure_reason = "minimap_no_map_candidates"
            return None
        strong = strong_candidate(combined.best, combined.margin)
        use_index = not self._skip_index_once
        self._skip_index_once = False
        if not strong and use_index:
            try:
                proposed = self._search_snapshot_proposals(frame, geometry, started, results)
            except ResourcePending:
                self.last_failure_reason = "layer_scan_pending"
                return None
            if proposed is not None:
                if proposed.reason != "layer_scan_pending" and not strong_candidate(proposed.best, proposed.margin):
                    self._skip_index_once = True
                return proposed
            self._skip_index_once = True
        if self._workers.closed:
            return None
        # An accelerator miss still reaches the ordinary rotating full scan.
        available = [mid for mid in self.map_ids if mid in self._cache]
        selected = combined.best.map_id if strong else available[self._scan_index % len(available)]
        if not strong:
            self._scan_index += 1
        locator = self._load(selected)
        if hasattr(locator, "base"):
            base_result = next(r for mid, r in results if mid == selected)
            layered = locator.locate_global(frame, geometry, base_result=base_result, use_index=False)
            results = [(mid, layered if mid == selected else r) for mid, r in results]
        return self._combine(results, started, None)

    def _search_snapshot_proposals(self, frame, geometry, started, base_results):
        """Compare fresh proposals across maps; they never replace confidence."""
        results = dict(base_results)
        maps = [mid for mid in self.map_ids if mid in self._cache and getattr(self._cache[mid], "entries", ())]
        proposals, seen, expanded, validated = {}, {}, set(), {}
        combined = None
        for mid in maps:
            if self._workers.closed:
                return None
            proposals[mid] = self._cache[mid].rank_layers(frame, geometry, wait_for_resources=True, fresh_scores=True)[
                :8
            ]
            seen[mid] = set()
        while True:
            pending = [
                (score, -order, mid, tier)
                for order, mid in enumerate(maps)
                for score, tier in proposals[mid]
                if tier not in seen[mid]
            ]
            if not pending or self._workers.closed:
                return combined
            _, _, mid, tier = max(pending, key=lambda item: (item[0], item[1]))
            seen[mid].add(tier)
            result = self._cache[mid].locate_proposed_layer(frame, geometry, tier, wait_for_resources=True)
            if result is not None and (
                result.reason == "layer_scan_pending" or strong_candidate(result.best, result.margin)
            ):
                previous = validated.get(mid)
                if previous is not None and result.reason != "layer_scan_pending":
                    # A later floor must not erase an earlier competitor from
                    # this same snapshot, even if neither is unique yet.
                    winner, other = (
                        (result, previous) if result.confidence > previous.confidence else (previous, result)
                    )
                    candidates = deduplicate_candidates([*winner.candidates, *other.candidates])
                    spatial = deduplicate_candidates([replace(c, tier_id=None) for c in candidates])
                    result = replace(
                        winner,
                        candidates=tuple(candidates),
                        spatial_margin=min(winner.margin, candidate_margin(spatial)),
                    )
                validated[mid] = result
                results[mid] = result
                combined = self._combine([(key, results[key]) for key in self.map_ids if key in results], started, None)
                if combined is not None and (
                    combined.reason == "layer_scan_pending" or strong_candidate(combined.best, combined.margin)
                ):
                    return combined
            if mid not in expanded and len(seen[mid]) == len(proposals[mid]) and len(proposals[mid]) < 8:
                # Background-only suggestions remain lazy; a miss restores raw
                # proposals without throwing away another map's better hints.
                expanded.add(mid)
                proposals[mid] = self._cache[mid].rank_layers(
                    frame, geometry, wait_for_resources=True, required_count=8
                )[:8]

    def _locate_cold_layers(self, frame, geometry, started, *, base_results=None):
        order = self._cold_map_order or tuple(
            mid for mid, locator in self._cache.items() if callable(getattr(locator, "rank_layers", None))
        )
        mid = order[self._cold_map_cursor % len(order)] if order else None
        options = {"wait_for_resources": True} if self._async_search else {}
        if self._cold_proposal is None or self._cold_proposal[0] != mid:
            try:
                rank_options = {"required_count": min(8, self._proposal_index + 1)} if self._proposal_index else {}
                proposals = self._cache[mid].rank_layers(frame, geometry, **options, **rank_options) if mid else []
            except ResourcePending:
                self.last_failure_reason = "layer_index_pending"
                return None
            if not proposals or self._proposal_index >= min(8, len(proposals)):
                # An accelerator miss must still reach the ordinary full scan.
                self._cold_layers = False
                self._proposal_index = 0
                self._cold_proposal = None
                self._skip_index_once = True
                self._cold_map_cursor += 1
                self.last_failure_reason = "layer_index_no_confirmed_candidate"
                return None
            self._cold_proposal = (mid, proposals[self._proposal_index][1])
        # Retain only the tier hint while decoding, never its score or pixels.
        # Full matching and all competitors below use the current frame.
        _, tier = self._cold_proposal
        try:
            result = self._cache[mid].locate_proposed_layer(frame, geometry, tier, **options)
        except ResourcePending:
            self.last_failure_reason = "layer_scan_pending"
            return None
        if result is None or (
            result.reason != "layer_scan_pending" and not strong_candidate(result.best, result.margin)
        ):
            self._proposal_index += 1
            self._cold_proposal = None
            self.last_failure_reason = "layer_index_candidate_rejected"
            return None
        if result.reason == "layer_scan_pending":
            return self._combine([(mid, result)], started, None)
        # Other maps still receive full global competition on this same frame;
        # no thumbnail score is used for confidence/margin.
        # The proposed floor's result already contains its main-map/overlap
        # competition. Previously the full base search for this map was run
        # and immediately overwritten here, contributing no evidence at all.
        results = dict(
            self._compare_base_maps(frame, geometry, skip_map_id=mid) if base_results is None else base_results
        )
        results[mid] = result
        combined = self._combine([(key, results[key]) for key in self.map_ids if key in results], started, None)
        if combined is not None and not strong_candidate(combined.best, combined.margin):
            self._proposal_index += 1
            self._cold_proposal = None
        return combined

    def _active_result(self, frame, geometry):
        if not self._active:
            return None
        result = self.locate_near(frame, (0, 0), geometry, search_radius_m=80, use_global=True)
        if result is not None and result.reason == "layer_scan_pending":
            return result
        if result is not None and strong_candidate(result.best, result.margin):
            return result
        return None

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0, use_global=False):
        self.last_failure_reason = ""
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
            self.last_failure_reason = getattr(locator, "last_failure_reason", "") or "minimap_no_candidates"
            self.reset_tracking()
        return self._combine([(self._active, result)], started, previous_world)

    def locate_on_map(self, frame, map_id, previous_world, geometry, *, search_radius_m=80.0):
        """Bound live-chain recovery to its anchored map, independently of hints."""
        self.last_failure_reason = ""
        if map_id not in self._cache:
            self.last_failure_reason = "visual_recovery_map_unavailable"
            return None
        started = time.perf_counter()
        locator = self._cache[map_id]
        result = locator.locate_near(frame, previous_world, geometry, search_radius_m=search_radius_m)
        if result is None:
            self.last_failure_reason = getattr(locator, "last_failure_reason", "") or "minimap_no_candidates"
        return self._combine([(map_id, result)], started, previous_world)
