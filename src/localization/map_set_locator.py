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
        self._workers = workers
        self._require_all_maps = require_all_maps
        self._pending_maps = {}
        self._resource_retry_at = {}
        self._resource_errors = {}
        self.last_failure_reason = ""
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

    def _combine(self, results, started, prior, *, commit_hint=True):
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
        if commit_hint and not pending_resources and not topology_rejected and strong_candidate(best, margin):
            self._active = best.map_id
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
        if not self._async_search:
            result = self._active_result(frame, geometry)
            if result is not None:
                return result
        return self._search_snapshot(frame, geometry, started)

    def _search_snapshot(self, frame, geometry, started):
        """Complete one frame's map/floor competition before retaining a hint."""
        self._active = None
        results = self._compare_base_maps(frame, geometry)
        for mid, result in results:
            seed = getattr(self._cache[mid], "seed_proposals", None)
            if callable(seed):
                seed(result)
        results = [
            (
                mid,
                self._cache[mid].locate_global(frame, geometry, base_result=result, use_index=False)
                if hasattr(self._cache[mid], "base") and not getattr(self._cache[mid], "entries", ())
                else result,
            )
            for mid, result in results
        ]
        if not any(getattr(locator, "entries", ()) for locator in self._cache.values()):
            return self._combine(results, started, None)
        try:
            return self._search_snapshot_proposals(frame, geometry, started, results)
        except ResourcePending:
            self.last_failure_reason = "layer_scan_pending"
            return None

    def _search_snapshot_proposals(self, frame, geometry, started, base_results):
        """Proposal scores order work; every declared floor remains a competitor."""
        base_by_map = dict(base_results)
        results = dict(base_by_map)
        maps = [mid for mid in self.map_ids if mid in self._cache and getattr(self._cache[mid], "entries", ())]
        pending = []
        for order, mid in enumerate(maps):
            if self._workers is not None and self._workers.closed:
                return None
            locator = self._cache[mid]
            proposals = locator.rank_layers(
                frame, geometry, wait_for_resources=True, fresh_scores=True, required_count=len(locator.entries)
            )
            tiers = {tier for _, tier in proposals}
            proposals = [*proposals, *((0.0, entry["id"]) for entry in locator.entries if entry.get("id") not in tiers)]
            pending.extend((score, -order, mid, tier) for score, tier in proposals)
        validated = {}
        for _, _, mid, tier in sorted(pending, key=lambda item: (item[0], item[1]), reverse=True):
            if self._workers is not None and self._workers.closed:
                return None
            result = self._cache[mid].locate_proposed_layer(
                frame,
                geometry,
                tier,
                wait_for_resources=True,
                complete_competition=True,
                base_result=base_by_map.get(mid),
            )
            if result is None:
                continue
            if result.reason == "layer_scan_pending":
                return self._combine([(mid, result)], started, None, commit_hint=False)
            previous = validated.get(mid)
            if previous is not None:
                winner, other = (result, previous) if result.confidence > previous.confidence else (previous, result)
                candidates = deduplicate_candidates([*winner.candidates, *other.candidates])
                spatial = deduplicate_candidates([replace(c, tier_id=None) for c in candidates])
                layer_margin = candidate_margin(
                    [candidates[0], *(c for c in candidates[1:] if c.tier_id != candidates[0].tier_id)]
                )
                best = candidates[0]
                if winner.best.tier_id is None or layer_margin < (0.10 if best.color else 0.08):
                    best = replace(best, tier_id=None)
                result = replace(
                    winner,
                    best=best,
                    candidates=(best, *candidates[1:]),
                    spatial_margin=min(winner.margin, candidate_margin(spatial)),
                    layer_margin=layer_margin,
                )
            validated[mid] = result
            results[mid] = result
        return self._combine([(mid, results[mid]) for mid in self.map_ids if mid in results], started, None)

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
        if self._search_future is not None and not self._search_future.done():
            self.last_failure_reason = "visual_global_search_pending"
            return None
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
        if self._search_future is not None and not self._search_future.done():
            self.last_failure_reason = "visual_global_search_pending"
            return None
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
