"""Automatic base-map selection without packet coordinates or WS map IDs."""

import logging
import math
import threading
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, replace

import numpy as np

from src.localization.localization_workers import ResourcePending
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MinimapGeometry,
    MinimapMapLocator,
    candidate_margin,
    deduplicate_candidates,
    minimap_map_scale,
    strong_candidate,
)

_LOG = logging.getLogger(f"ok.{__name__}")


def _hud_changes(first, second, geometry):
    if first is None or second is None or first.shape != second.shape:
        return None
    if geometry is not None:
        x0, y0, x1, y1 = geometry.hud_bounds()
        first, second = first[max(0, y0) : y1, max(0, x0) : x1], second[max(0, y0) : y1, max(0, x0) : x1]
    changed = first != second
    return int(np.count_nonzero(np.any(changed, axis=2) if changed.ndim == 3 else changed))


_SEARCH_RESULT_MAX_AGE_S = 2.0
_COLD_SEARCH_MAX_AGE_S = 30.0


@dataclass(frozen=True)
class _SearchOutcome:
    result: LocateResult | None
    failure_reason: str
    observed_at: float
    active_map: str | None = None
    map_ids: frozenset[str] = frozenset()


@dataclass
class _CompletedCompetition:
    result: LocateResult
    frame: np.ndarray
    geometry: MinimapGeometry | None
    expires: float
    observed_at: float
    capture_time: float | None
    map_ids: frozenset[str]
    confirmation: tuple[np.ndarray, float, float | None] | None = None


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
        self._job_state = threading.local()
        self.last_failure_reason = ""
        self._navigation_scope = None
        self._navigation_scope_key = None
        self._async_search = bool(async_search and workers is not None)
        self._completed_competition = None
        self.stationary_navigation_start = False
        self._search_future = None
        self._search_generation = 0
        self._search_started_generation = 0
        self._scope_change_pending = False
        self._search_shape = None
        self._search_geometry = None
        self._search_frame = None
        self._search_started_at = 0.0
        self._search_observed_at = None
        self._search_ready_callback = None
        self._frame_time = None
        self._capture_time = None
        self._search_capture_time = None
        self._expanded_scope = None
        self._visual_normalization_required = False
        self._closed = False

    def set_search_ready_callback(self, callback):
        """Wake the owner for a fresh observation; never publish job results."""
        self._search_ready_callback = callback

    def set_frame_time(self, stamp, *, capture_timed=True):
        self._frame_time = stamp
        self._capture_time = stamp if capture_timed else None

    def requires_visual_normalization(self):
        if not self._visual_normalization_required:
            has_prepared = getattr(self.store, "has_prepared", None)
            if callable(has_prepared):
                self._visual_normalization_required = bool(has_prepared())
            else:
                self._visual_normalization_required = bool(getattr(self.store, "require_prepared", False))
        return self._visual_normalization_required

    def _notify_search_ready(self, future):
        callback = self._search_ready_callback
        if not self._closed and future is self._search_future and callable(callback):
            callback()

    @property
    def last_failure_reason(self):
        return getattr(self._job_state, "failure_reason", self._last_failure_reason)

    @last_failure_reason.setter
    def last_failure_reason(self, value):
        if hasattr(self._job_state, "failure_reason"):
            self._job_state.failure_reason = value
        else:
            self._last_failure_reason = value

    def set_navigation_scope(self, scope):
        identity = getattr(scope, "search_identity", None)
        scope_key = identity() if callable(identity) else scope
        scope_changed = scope_key is not self._navigation_scope_key and scope_key != self._navigation_scope_key
        # A running job measures its original frame under its original reachability budget.
        # Apply growth only after consuming that job. Moving the anchor or tightening
        # the budget still invalidates it immediately.
        if self._search_future is not None and getattr(scope, "expands_from", lambda _: False)(self._navigation_scope):
            self._expanded_scope = scope
            return
        self._expanded_scope = None
        self._navigation_scope = scope
        self._navigation_scope_key = scope_key
        if scope_changed:
            self._completed_competition = None
            if self._search_future is not None:
                self._search_generation += 1
                self._scope_change_pending = True
            else:
                self._apply_navigation_scope_to_cache()
        if self._search_future is not None and not self._search_future.done():
            return
        self._settle_search_scope()

    def _settle_search_scope(self):
        """Apply a scope change that arrived while a search was still running."""
        if self._expanded_scope is not None and self._search_future is None:
            self._navigation_scope = self._expanded_scope
            self._navigation_scope_key = self._expanded_scope.search_identity()
            self._expanded_scope = None
            self._apply_navigation_scope_to_cache()
        if not self._scope_change_pending:
            return
        self._clear_search_hint()
        self._apply_navigation_scope_to_cache()
        self._scope_change_pending = False

    def _discard_stale_completed_search(self):
        """A reset generation owns no hint, even if the old worker already finished."""
        future = self._search_future
        if future is None or not future.done() or self._search_started_generation == self._search_generation:
            return
        self._search_future = None
        self._search_frame = None
        self._search_geometry = None
        self._search_started_at = 0.0
        self._search_observed_at = None
        with suppress(BaseException):
            future.result()
        self._clear_search_hint()
        self._scope_change_pending = True
        self._settle_search_scope()

    def _apply_navigation_scope_to_cache(self):
        scope = self._navigation_scope
        for locator in self._cache.values():
            setter = getattr(locator, "set_navigation_scope", None)
            if callable(setter):
                setter(scope)

    def _map_in_scope(self, map_id, scope=None):
        scope = self._navigation_scope if scope is None else scope
        if scope is None:
            return True
        locator = self._cache.get(map_id)
        entries = getattr(locator, "entries", ())
        if scope.allows_tier(map_id, "__main__") or any(scope.allows_tier(map_id, entry["id"]) for entry in entries):
            return True
        return map_id == getattr(scope, "anchor_map_id", None)

    def _maps_in_scope(self, scope=None):
        return [map_id for map_id in self.map_ids if self._map_in_scope(map_id, scope)]

    def _main_maps_in_scope(self, scope=None):
        scope = self._navigation_scope if scope is None else scope
        if scope is None:
            return list(self.map_ids)
        return [map_id for map_id in self.map_ids if scope.allows_tier(map_id, "__main__")]

    @staticmethod
    def _is_waiting_reason(reason):
        return reason in {
            "visual_global_search_pending",
            "visual_map_resources_pending",
            "visual_coarse_resources_pending",
            "layer_scan_pending",
        } or reason.startswith("visual_map_resources_pending:")

    def _required_map_ids(self, scope=None):
        allows_map = getattr(scope, "allows_map", None)
        if not callable(allows_map):
            return list(self.map_ids)
        return [map_id for map_id in self.map_ids if allows_map(map_id)]

    def prewarm(self, scope=None):
        if self._workers is not None and not self._workers.closed:
            for mid in self._required_map_ids(scope):
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
        self._completed_competition = None
        self._active = None
        self._search_frame = None
        self._search_geometry = None
        self._search_started_at = 0.0
        self._search_observed_at = None
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
        diagnostics = [(mid, result) for mid, result in results if result.reason == "navigation_topology_no_candidates"]
        results = [(mid, result) for mid, result in results if result.reason != "navigation_topology_no_candidates"]
        if not results:
            mid, diagnostic = diagnostics[0]
            candidates = []
            for candidate in diagnostic.candidates:
                x, y = self.calibration.world_to_map(candidate.world_x, candidate.world_z)
                candidates.append(replace(candidate, map_x=x, map_y=y, map_id=mid))
            if not candidates:
                return diagnostic
            return LocateResult(
                "rejected",
                diagnostic.reason,
                candidates[0],
                tuple(candidates[:8]),
                time.perf_counter() - started,
                diagnostic.scale,
                prior is not None,
                spatial_margin=0,
                layer_margin=diagnostic.layer_margin,
            )
        candidates = []
        for mid, result in results:
            for c in result.competition_candidates or result.candidates:
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
        resource_error = next((r.reason for _, r in results if r.reason.startswith("layer_resource_error:")), None)
        pending_resources = resource_error or any(r.reason == "layer_scan_pending" for _, r in results)
        topology_rejected = chosen_result.reason.startswith("navigation_topology_")
        reason = (
            (resource_error or "layer_scan_pending")
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
            competition_candidates=(best, *others),
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

    def _warm_base_resources(self, scope=None):
        required = self._required_map_ids(scope)
        if self._workers is not None:
            if self._workers.closed:
                return False
            self.prewarm(scope)
            ready = True
            for mid in required:
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
                if hasattr(locator, "set_navigation_scope"):
                    locator.set_navigation_scope(scope)
                self._cache[mid] = locator
                self._resource_errors.pop(mid, None)
                if len(self.map_ids) == 1:
                    # An explicitly selected map retains its affine odometry
                    # transform; the normalized frame is only for map competition.
                    self.calibration = getattr(locator, "calibration", self.calibration)
                del self._pending_maps[mid]
            scoped_errors = self._resource_errors.keys() & required
            return (
                ready
                and any(mid in self._cache for mid in required)
                and (not self._require_all_maps or not scoped_errors)
            )
        for mid in required:
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
        scoped_errors = self._resource_errors.keys() & required
        return any(mid in self._cache for mid in required) and (not self._require_all_maps or not scoped_errors)

    def _compare_base_maps(self, frame, geometry, *, skip_map_id=None, scope=None, generation=None):
        if generation is not None and generation != self._search_generation:
            return []
        scoped_map_ids = set(self._main_maps_in_scope(scope))
        if self._workers is not None:
            # One stable frame for all candidates; no worker retains capture buffers.
            snapshot = frame if frame.flags.owndata and not frame.flags.writeable else frame.copy()
            snapshot.setflags(write=False)
            bases = {}
            for mid in self.map_ids:
                if mid not in self._cache or mid == skip_map_id or mid not in scoped_map_ids:
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
                    if generation is not None and generation != self._search_generation:
                        break
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
            if mid not in self._cache or mid == skip_map_id or mid not in scoped_map_ids:
                continue
            locator = self._load(mid)
            base = getattr(locator, "base", locator)
            results.append((mid, base.locate_global(frame, geometry)))
        return results

    def _prepare_base_search(self, frame, *, scope=None, generation=None):
        if generation is not None and generation != self._search_generation:
            return False
        if self._workers is None:
            return True
        if self._workers.closed:
            return False
        ready = True
        for map_id in self._main_maps_in_scope(scope):
            locator = self._cache.get(map_id)
            if locator is None:
                continue
            base = getattr(locator, "base", locator)
            prepare = getattr(base, "prepare_global_search", None)
            if callable(prepare) and not prepare(frame.shape[1], self._workers.resources):
                ready = False
        return ready

    def locate_global(self, frame, geometry):
        if self._closed:
            self.last_failure_reason = "visual_locator_closed"
            return None
        self._discard_stale_completed_search()
        if self._frame_gate._prepare_template(frame, geometry) is None:
            self.last_failure_reason = self._frame_gate.last_failure_reason
            self.reset_tracking()
            return None
        started = time.perf_counter()
        if self._async_search and self._search_future is not None:
            return self._locate_async(frame, geometry, started)
        self.last_failure_reason = ""
        if not self._warm_base_resources(self._navigation_scope):
            required = set(self._required_map_ids(self._navigation_scope))
            pending = ",".join(mid for mid in required if mid not in self._cache)
            scoped_errors = {mid: error for mid, error in self._resource_errors.items() if mid in required}
            self.last_failure_reason = (
                f"visual_map_resource_error: {scoped_errors}"
                if scoped_errors
                else f"visual_map_resources_pending: maps={pending}"
            )
            return None
        if not self._active and not self._prepare_base_search(
            frame, scope=self._navigation_scope, generation=self._search_generation
        ):
            self.last_failure_reason = "visual_coarse_resources_pending"
            return None
        if self._async_search:
            return self._locate_async(frame, geometry, started)
        observed_at = self._frame_time if self._frame_time is not None else time.time()
        completed = self._search_global_ready(frame, geometry, started)
        return replace(completed, observed_at=observed_at) if completed is not None else None

    def _loaded_competitors(self):
        return frozenset(mid for mid in self._required_map_ids(self._navigation_scope) if mid in self._cache)

    def _poll_completed_competition(self, frame, geometry):
        cached = self._completed_competition
        now = self._frame_time if self._frame_time is not None else time.time()
        changes = _hud_changes(cached.frame, frame, geometry)
        compatible = (
            time.monotonic() <= cached.expires
            and cached.frame.shape == frame.shape
            and cached.frame.dtype == frame.dtype
            and cached.map_ids == self._loaded_competitors()
            and geometry == cached.geometry
            and 0 <= now - cached.observed_at <= _COLD_SEARCH_MAX_AGE_S
        )
        if compatible:
            # A later poll is not proof of a later capture. Without comparable
            # capture times, require different pixels even outside the HUD.
            observations = [(cached.frame, cached.observed_at, cached.capture_time)]
            if cached.confirmation is not None:
                observations.append(cached.confirmation)
            for previous, observed_at, capture_time in observations:
                timed = self._capture_time is not None and capture_time is not None
                if now <= observed_at or (not timed and np.array_equal(frame, previous)):
                    self.last_failure_reason = "visual_global_search_pending"
                    return None

            result = cached.result if changes == 0 else None
            if self.stationary_navigation_start:
                seed = cached.result.best
                result = self.locate_on_map(
                    frame, seed.map_id, (seed.world_x, seed.world_z), geometry, search_radius_m=12.0
                )
                if (
                    result is None
                    or result.status != "accepted"
                    or result.best.tier_id != seed.tier_id
                    or not strong_candidate(result.best, result.margin)
                    or math.hypot(result.best.world_x - seed.world_x, result.best.world_z - seed.world_z) > 2.0
                ):
                    result = None
            if result is not None:
                _LOG.info(
                    "global_search confirmed hud_changed_px=%s stationary_start=%s ncc=%.4f margin=%.4f",
                    changes,
                    self.stationary_navigation_start,
                    result.best.ncc,
                    result.margin,
                )
                self._active = result.best.map_id
                if cached.confirmation is None:
                    cached.confirmation = (frame.copy(), now, self._capture_time)
                else:
                    self._completed_competition = None
                return replace(result, observed_at=now)
        _LOG.info(
            "global_search retry=%s hud_changed_px=%s",
            ("start_candidate_failed" if self.stationary_navigation_start else "matching_pixels_changed")
            if compatible
            else "expired_or_incompatible_competition",
            changes,
        )
        # Any changed search input requires all maps and floors to compete again.
        self._clear_search_hint()
        return None

    def _locate_async(self, frame, geometry, started):
        future = self._search_future
        if future is not None:
            if self._search_shape != frame.shape and self._search_started_generation == self._search_generation:
                self.reset_tracking()
            if not future.done():
                self.last_failure_reason = self._pending_search_reason()
                return None
            expanded = self._expanded_scope is not None
            stale = self._search_started_generation != self._search_generation
            self._search_future = None
            snapshot = self._search_frame
            search_geometry = self._search_geometry
            search_age = time.monotonic() - self._search_started_at
            search_observed_at = self._search_observed_at
            self._search_frame = None
            self._search_geometry = None
            self._search_started_at = 0.0
            self._search_observed_at = None
            try:
                if stale:
                    with suppress(BaseException):
                        future.result()
                    outcome = None
                else:
                    outcome = future.result()  # Surface current-frame resource failures.
            finally:
                self._settle_search_scope()
            if stale:
                self._clear_search_hint()
                self._apply_navigation_scope_to_cache()
                outcome = None
            if isinstance(outcome, _SearchOutcome):
                completed = outcome.result
                failure_reason = outcome.failure_reason
                search_observed_at = outcome.observed_at
            elif outcome is not None:
                completed = outcome
                failure_reason = self.last_failure_reason
            else:
                completed = None
                failure_reason = "" if stale else self.last_failure_reason
            if (
                completed is not None
                and snapshot is not None
                and search_geometry == geometry
                and search_age <= _SEARCH_RESULT_MAX_AGE_S
                and not self.stationary_navigation_start
            ):
                _LOG.info(
                    "global_search delivered_snapshot age_s=%.3f hud_changed_px=%s",
                    search_age,
                    _hud_changes(snapshot, frame, geometry),
                )
                # The result belongs to the completed snapshot, whose age was
                # checked above. Preserve that observation time so consumers do
                # not relabel stale pixels with the polling frame's time.
                if isinstance(outcome, _SearchOutcome):
                    self._active = outcome.active_map
                if search_observed_at is None:
                    return completed
                return replace(completed, observed_at=search_observed_at)
            if (
                isinstance(outcome, _SearchOutcome)
                and snapshot is not None
                and search_geometry == geometry
                and not expanded
                and (self.stationary_navigation_start or search_age > _SEARCH_RESULT_MAX_AGE_S)
                and search_age <= _COLD_SEARCH_MAX_AGE_S
                and completed is not None
                and completed.status == "accepted"
                and completed.best.map_id
                and completed.best.map_id == outcome.active_map
                and completed.best.tier_id is not None
                and strong_candidate(completed.best, completed.margin)
            ):
                self._completed_competition = _CompletedCompetition(
                    completed,
                    snapshot,
                    geometry,
                    time.monotonic() + _SEARCH_RESULT_MAX_AGE_S,
                    search_observed_at,
                    self._search_capture_time,
                    outcome.map_ids,
                )
            else:
                _LOG.info(
                    "global_search retry=%s age_s=%.3f hud_changed_px=%s",
                    failure_reason
                    or ("scope_changed" if stale or expanded else (completed.status if completed else "no_candidate")),
                    search_age,
                    _hud_changes(snapshot, frame, geometry),
                )
                self._clear_search_hint()
            self.last_failure_reason = failure_reason
            if completed is None and failure_reason.startswith(("layer_resource_error:", "visual_map_resource_error:")):
                # Preserve a resource error for this call. The next fresh frame
                # retries the search under the existing backoff.
                return None
        if self._completed_competition is not None:
            confirmed = self._poll_completed_competition(frame, geometry)
            if confirmed is not None or self._completed_competition is not None:
                return confirmed
        if not self._warm_base_resources(self._navigation_scope):
            required = set(self._required_map_ids(self._navigation_scope))
            pending = ",".join(mid for mid in required if mid not in self._cache)
            scoped_errors = {mid: error for mid, error in self._resource_errors.items() if mid in required}
            self.last_failure_reason = (
                f"visual_map_resource_error: {scoped_errors}"
                if scoped_errors
                else f"visual_map_resources_pending: maps={pending}"
            )
            return None
        if not self._prepare_base_search(frame, scope=self._navigation_scope, generation=self._search_generation):
            self.last_failure_reason = "visual_coarse_resources_pending"
            return None
        snapshot = frame.copy()
        snapshot.setflags(write=False)
        self._search_shape = snapshot.shape
        self._search_geometry = geometry
        self._search_frame = snapshot
        self._search_started_generation = self._search_generation
        self._search_started_at = time.monotonic()
        self._search_capture_time = self._capture_time
        self._search_observed_at = self._frame_time if self._frame_time is not None else time.time()
        self._search_future = self._workers.acquisitions.submit(
            self._search_global_outcome, snapshot, geometry, started, self._search_observed_at
        )
        self._search_future.add_done_callback(self._notify_search_ready)
        self.last_failure_reason = "visual_global_search_pending"
        return None

    def _search_global_ready(self, frame, geometry, started):
        result = self._search_snapshot(frame, geometry, started)
        _LOG.info(
            "global_search status=%s reason=%s elapsed_s=%.3f margin=%s candidates(map,tier,x,z,ncc,full_ncc)=%s",
            result.status if result else "pending",
            result.reason if result else self.last_failure_reason,
            time.perf_counter() - started,
            result.margin if result else None,
            [
                (
                    c.map_id,
                    c.tier_id,
                    round(c.world_x, 2),
                    round(c.world_z, 2),
                    round(c.ncc, 4),
                    round(c.full_ncc, 4),
                )
                for c in result.candidates
            ]
            if result
            else [],
        )
        return result

    def _search_global_outcome(self, frame, geometry, started, observed_at):
        self._job_state.failure_reason = ""
        try:
            map_ids = self._loaded_competitors()
            result = self._search_global_ready(frame, geometry, started)
            return _SearchOutcome(result, self._job_state.failure_reason, observed_at, self._active, map_ids)
        finally:
            self._active = None
            del self._job_state.failure_reason

    def _search_snapshot(self, frame, geometry, started):
        """Complete one frame's map/floor competition before retaining a hint."""
        scope = self._navigation_scope
        generation = self._search_started_generation if self._search_future is not None else self._search_generation
        if generation != self._search_generation:
            return None
        self._active = None
        results = self._compare_base_maps(frame, geometry, scope=scope, generation=generation)
        if generation != self._search_generation:
            return None
        for mid, result in results:
            seed = getattr(self._cache[mid], "seed_proposals", None)
            if callable(seed):
                seed(result)
        completed = []
        for mid, result in results:
            locator = self._cache[mid]
            if not hasattr(locator, "base") or getattr(locator, "entries", ()):
                allows = getattr(scope, "allows_candidate", None)
                if (
                    result is not None
                    and callable(allows)
                    and not allows(mid, "__main__", result.best.world_x, result.best.world_z)
                ):
                    # Preserve the peak as a competitor; it may veto but may not win.
                    result = replace(
                        result, status="rejected", reason="navigation_topology_unreachable", spatial_margin=0
                    )
                completed.append((mid, result))
                continue
            # A single-tier map must keep the full-frame peak computed above.
            # The fallback keeps lightweight locator doubles and older callers
            # compatible without restoring the _recent shortlist path.
            search_complete = getattr(locator, "search_complete_frame", None)
            if callable(search_complete):
                complete = search_complete(frame, geometry, base_result=result)
            elif result is not None and strong_candidate(
                result.best,
                result.margin if result.margin is not None else 1.0,
            ):
                complete = locator.locate_global(frame, geometry, base_result=result)
            else:
                complete = result
            completed.append((mid, complete))
        results = completed
        if not any(getattr(locator, "entries", ()) for locator in self._cache.values()):
            return self._combine(results, started, None)
        try:
            return self._search_snapshot_proposals(
                frame, geometry, started, results, scope=scope, generation=generation
            )
        except ResourcePending:
            self.last_failure_reason = self._layer_wait_reason()
            return None

    def _pending_search_reason(self, scope=None):
        scope = self._navigation_scope if scope is None else scope
        required = set(self._required_map_ids(scope))
        for map_id, locator in tuple(self._cache.items()):
            if map_id not in required or not getattr(locator, "entries", ()):
                continue
            entries = getattr(locator, "entries", ())
            if scope is None or any(scope.allows_tier(map_id, entry["id"]) for entry in entries):
                return "layer_scan_pending"
        return "visual_global_search_pending"

    def _layer_wait_reason(self, scope=None):
        """Surface the last scoped floor decode failure while competition is pending."""
        # Iterate a snapshot: an acquisition worker may move entries in _cache
        # (LRU) while this runs, which would raise "mutated during iteration".
        scope = self._navigation_scope if scope is None else scope
        required = set(self._required_map_ids(scope))
        layer_errors = {
            tier: error
            for map_id, locator in tuple(self._cache.items())
            for tier, error in getattr(locator, "resource_errors", {}).items()
            if map_id in required and (scope is None or scope.allows_tier(map_id, tier))
        }
        if not layer_errors:
            return "layer_scan_pending"
        return "layer_resource_error: " + "; ".join(f"{tier}: {error}" for tier, error in sorted(layer_errors.items()))

    def _search_snapshot_proposals(self, frame, geometry, started, base_results, *, scope=None, generation=None):
        """Proposal scores order work; every declared floor remains a competitor."""
        scope = self._navigation_scope if scope is None else scope
        generation = self._search_generation if generation is None else generation
        if generation != self._search_generation:
            return None
        base_by_map = dict(base_results)
        results = dict(base_by_map)
        allowed_entries = {
            mid: [
                entry
                for entry in getattr(self._cache[mid], "entries", ())
                if scope is None or scope.allows_tier(mid, entry["id"])
            ]
            for mid in self.map_ids
            if mid in self._cache and getattr(self._cache[mid], "entries", ())
        }
        layered_maps = [mid for mid in self.map_ids if mid in allowed_entries]
        pending = []
        pending_competition = False
        searchable_maps = [mid for mid in layered_maps if allowed_entries[mid]]
        for order, mid in enumerate(searchable_maps):
            if (self._workers is not None and self._workers.closed) or generation != self._search_generation:
                return None
            locator = self._cache[mid]
            proposals = locator.rank_layers(
                frame,
                geometry,
                wait_for_resources=True,
                fresh_scores=True,
                required_count=len(allowed_entries[mid]),
                entries=allowed_entries[mid],
            )
            allowed_tiers = {entry["id"] for entry in allowed_entries[mid]}
            proposals = [(score, tier) for score, tier in proposals if tier in allowed_tiers]
            tiers = {tier for _, tier in proposals}
            proposals = [
                *proposals,
                *((0.0, entry["id"]) for entry in allowed_entries[mid] if entry.get("id") not in tiers),
            ]
            pending.extend((score, -order, mid, tier) for score, tier in proposals)
        layer_results = {mid: [] for mid in layered_maps}
        for _, _, mid, tier in sorted(pending, key=lambda item: (item[0], item[1]), reverse=True):
            if (self._workers is not None and self._workers.closed) or generation != self._search_generation:
                return None
            try:
                result = self._cache[mid].locate_proposed_layer(
                    frame, geometry, tier, wait_for_resources=True, complete_competition=True
                )
            except ResourcePending:
                pending_competition = True
                layer_results[mid].append((tier, None))
                continue
            if result is not None and (
                result.reason == "layer_scan_pending" or result.reason.startswith("layer_resource_error:")
            ):
                pending_competition = True
            layer_results[mid].append((tier, result))
        for mid in layered_maps:
            results[mid] = self._cache[mid].combine_global_results(base_by_map.get(mid), layer_results[mid])
        combined = self._combine(
            [(mid, results[mid]) for mid in self.map_ids if mid in results],
            started,
            None,
            commit_hint=not pending_competition,
        )
        if generation != self._search_generation:
            return None
        if pending_competition:
            self.last_failure_reason = self._layer_wait_reason(scope)
            if combined is None:
                return None
            return replace(combined, status="rejected", reason=self.last_failure_reason, spatial_margin=0)
        return combined

    def _consume_search_hint(self, frame, geometry):
        self._discard_stale_completed_search()
        future = self._search_future
        if future is not None:
            valid = (
                self._search_shape == frame.shape
                and self._search_geometry == geometry
                and 0 <= time.monotonic() - self._search_started_at <= _SEARCH_RESULT_MAX_AGE_S
            )
            self._search_future = None
            try:
                outcome = future.result()
            finally:
                self._clear_search_hint()
                self._settle_search_scope()
            if valid and isinstance(outcome, _SearchOutcome):
                self._active = outcome.active_map
        self._settle_search_scope()

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0, use_global=False):
        if self._closed:
            self.last_failure_reason = "visual_locator_closed"
            return None
        future = self._search_future
        if future is not None and not future.done():
            self.last_failure_reason = "visual_global_search_pending"
            return None
        self._consume_search_hint(frame, geometry)
        self.last_failure_reason = ""
        started = time.perf_counter()
        if not self._active:
            return self.locate_global(frame, geometry)
        if use_global:
            required = self._required_map_ids(self._navigation_scope)
            missing = [map_id for map_id in required if map_id not in self._cache]
            if missing and self._require_all_maps:
                self._warm_base_resources(self._navigation_scope)
                missing = [map_id for map_id in required if map_id not in self._cache]
            if missing and self._require_all_maps:
                scoped_errors = {mid: error for mid, error in self._resource_errors.items() if mid in required}
                self.last_failure_reason = (
                    f"visual_map_resource_error: {scoped_errors}"
                    if scoped_errors
                    else f"visual_map_resources_pending: maps={','.join(missing)}"
                )
                return None
            result = self._search_snapshot(frame, geometry, started)
        else:
            locator = self._load(self._active)
            result = locator.locate_near(frame, previous_world, geometry, search_radius_m=search_radius_m)
            if result is None:
                self.last_failure_reason = getattr(locator, "last_failure_reason", "") or "minimap_no_candidates"
                if not self._is_waiting_reason(self.last_failure_reason):
                    self.reset_tracking()
            result = self._combine([(self._active, result)], started, previous_world)
        if result is None and not self._is_waiting_reason(self.last_failure_reason):
            self.reset_tracking()
        return result

    def locate_on_map(self, frame, map_id, previous_world, geometry, *, search_radius_m=80.0):
        """Bound live-chain recovery to its anchored map, independently of hints."""
        future = self._search_future
        if future is not None and not future.done():
            self.last_failure_reason = "visual_global_search_pending"
            return None
        self._consume_search_hint(frame, geometry)
        self.last_failure_reason = ""
        if map_id not in self._cache:
            self.last_failure_reason = "visual_recovery_map_unavailable"
            return None
        started = time.perf_counter()
        locator = self._cache[map_id]
        result = locator.locate_near(frame, previous_world, geometry, search_radius_m=search_radius_m)
        if result is None:
            self.last_failure_reason = getattr(locator, "last_failure_reason", "") or "minimap_no_candidates"
        return self._combine([(map_id, result)], started, previous_world, commit_hint=False)
