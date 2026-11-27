"""Compete base-map and layer images in one calibrated world X/Z space."""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from src.localization.composite_floor_image import CompositeFloorImage
from src.localization.layer_search_index import LayerSearchIndex
from src.localization.localization_workers import ResourcePending, decode_memory_bytes, ordered_results
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MinimapMapLocator,
    candidate_margin,
    deduplicate_candidates,
    strong_candidate,
)
from src.localization.native_tile_image import NativeTileImage

_UNSEARCHED = object()


def layer_calibration(bounds, width, height):
    try:
        west, east, south, north = (float(bounds[k]) for k in ("west", "east", "south", "north"))
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid layer bounds") from exc
    if not all(math.isfinite(v) for v in (west, east, south, north)) or east <= west or north <= south:
        raise ValueError("invalid layer bounds")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("invalid layer image size")
    sx, sy = (east - west) / width, (north - south) / height
    # Account for the pixel-center convention when resampling to 4 px/m.
    matrix = np.array([[0.25, 0, west + 0.125 - sx / 2], [0, -0.25, north - 0.125 + sy / 2]])
    inverse = np.linalg.inv(np.vstack((matrix, [0, 0, 1])))[:2]
    return MapCalibration(matrix, inverse), (4 * sx, 4 * sy)


class LayeredMinimapLocator:
    def __init__(
        self,
        base,
        map_id,
        layer_root,
        catalog,
        *,
        bundle=None,
        prepared=None,
        allow_unprepared=True,
    ):
        self.base, self.map_id = base, map_id
        if prepared is None and not allow_unprepared:
            from src.localization.prepared_localization import PREPARED_MISSING_MESSAGE

            raise ValueError(PREPARED_MISSING_MESSAGE)
        self.color = getattr(base, "color", False) is True
        self.calibration = base.calibration
        self.root = Path(layer_root).resolve()
        self._bundle = bundle
        self._prepared = prepared
        entries = prepared.floor_entries(map_id) if prepared is not None else catalog.get("layers", [])
        if not all(isinstance(e, dict) for e in entries):
            raise ValueError("invalid_visual_layer_entry")
        if prepared is not None:
            self.entries = [dict(e) for e in entries if e.get("map_category") == "layered"]
        else:
            self.entries = [
                dict(e) for e in entries if e.get("map_name") == map_id and e.get("map_category") == "layered"
            ]
        seen_ids = set()
        for entry in self.entries:
            required = ("id", "width", "height", "bounds")
            if not all(k in entry for k in required):
                raise ValueError("invalid_visual_layer_entry")
            if not isinstance(entry["id"], str) or not entry["id"]:
                raise ValueError("invalid_visual_layer_entry")
            if prepared is None and (not isinstance(entry.get("path"), str) or not entry["path"]):
                raise ValueError("invalid_visual_layer_entry")
            if bundle is not None and entry["id"] in seen_ids:
                raise ValueError("duplicate_visual_layer_id")
            seen_ids.add(entry["id"])
            layer_calibration(entry["bounds"], entry["width"], entry["height"])
            entry["bounds"] = {k: float(entry["bounds"][k]) for k in ("west", "east", "south", "north")}
            if prepared is None and not (self.root / entry["path"]).resolve().is_relative_to(self.root):
                raise ValueError("layer image escapes resource root")
        self._cache = OrderedDict()
        self._composites = {}
        self._cache_bytes = 0
        self._cache_limit_bytes = 256 * 1024 * 1024
        self._pinned_ids = set()
        self._recent = None
        self._scan_index = 0
        self._resource_workers = None
        self._pending_layers = {}
        self._resource_retry_at = {}
        self._layer_errors = {}
        self._last_layer_failed_ids = set()
        self._search_index = None
        self._index_key = None
        self._pending_index = None
        self._pending_index_key = None
        self._proposal_index = 0
        self._last_failure_reason = None
        self._background_hint = None
        self._ranked_tiers = ()
        self._ranked_entry_key = None
        self._ranked_signature = None
        self._ranked_at = 0.0
        self._ranked_shape = None
        self._ranked_complete = False
        self._navigation_scope = None
        self._navigation_scope_key = None
        self.tier_coverage_complete = (
            prepared.tier_coverage_complete(map_id)
            if prepared is not None
            else not any(str(k).startswith(f"scene_{map_id}_") for k in catalog.get("missing_logic_ids", []))
        )

    def _scale(self, width):
        return self.base._scale(width)

    def set_navigation_scope(self, scope):
        """Apply a short-lived reachability envelope from a visual anchor."""
        identity = getattr(scope, "search_identity", None)
        scope_key = identity() if callable(identity) else scope
        if scope_key != self._navigation_scope_key:
            self.reset_proposals()
        self._navigation_scope = scope
        self._navigation_scope_key = scope_key

    def requires_visual_normalization(self):
        return self._prepared is not None

    def _allowed_entries(self):
        scope = self._navigation_scope
        return [entry for entry in self.entries if scope is None or scope.allows_tier(self.map_id, entry["id"])]

    def close(self):
        self.reset_proposals()
        for locator in self._composites.values():
            locator.close()
        self._composites.clear()
        for locator in (self.base, *self._cache.values()):
            close = getattr(locator, "close", None)
            if callable(close):
                close()
        for future in self._pending_layers.values():
            future.cancel()
        self._pending_layers.clear()
        if self._pending_index is not None:
            self._pending_index.cancel()
            self._pending_index = None
            self._pending_index_key = None
        self._search_index = None
        self._index_key = None
        self._pending_index_key = None
        self._cache.clear()
        self._cache_bytes = 0

    @property
    def last_failure_reason(self):
        return self._last_failure_reason or self.base.last_failure_reason

    @last_failure_reason.setter
    def last_failure_reason(self, value):
        self._last_failure_reason = value

    def _load(self, entry):
        key = entry["id"]
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if time.monotonic() < self._resource_retry_at.get(key, 0):
            raise ValueError(f"layer_decode_backoff:{key}")
        if self._prepared is not None or self._resource_workers is None:
            try:
                locator = self._decode(entry)
            except (OSError, ValueError) as exc:
                self._resource_retry_at[key] = time.monotonic() + 5
                self._layer_errors[key] = str(exc)
                raise
        else:
            if self._resource_workers.closed:
                raise ResourcePending()
            if key not in self._pending_layers:
                bounds = entry["bounds"]
                output_pixels = (bounds["east"] - bounds["west"]) * (bounds["north"] - bounds["south"]) * 16
                self._pending_layers[key] = self._resource_workers.resources.submit(
                    self._decode,
                    dict(entry),
                    memory_bytes=(
                        64 * 1024**2
                        if self._bundle is not None
                        else decode_memory_bytes(entry["width"], entry["height"], output_pixels)
                    ),
                )
            future = self._pending_layers[key]
            if not future.done():
                raise ResourcePending()
            try:
                locator = future.result()
            except (OSError, ValueError) as exc:
                self._resource_retry_at[key] = time.monotonic() + 5
                self._layer_errors[key] = str(exc)
                raise
            finally:
                del self._pending_layers[key]
        self._cache[key] = locator
        self._layer_errors.pop(key, None)
        self._evict_layers()
        return locator

    @property
    def resource_errors(self):
        """Latest floor decode failures; each entry is retried after backoff."""
        return dict(self._layer_errors)

    def _decode(self, entry):
        """Decode without changing caches, search hints or tracking state."""
        key = entry["id"]
        if self._prepared is not None:
            image = self._prepared.open_floor(entry)
            return MinimapMapLocator(image, image.calibration)
        calibration, (fx, fy) = layer_calibration(entry["bounds"], entry["width"], entry["height"])
        if self._bundle is not None:
            return MinimapMapLocator(self._bundle.open_floor(self.root, entry), calibration)
        path = (self.root / entry["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("layer image escapes resource root")
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"missing_layer_image:{key}")
        if image.dtype != np.uint8:
            raise ValueError(f"unsupported_layer_image_depth:{key}")
        height, width = image.shape[:2]
        if (width, height) != (entry["width"], entry["height"]):
            raise ValueError(f"layer_image_size_mismatch:{key}")
        if image.ndim == 3 and image.shape[2] == 4:
            color = image[:, :, :3].copy()
            color[image[:, :, 3] == 0] = 0
            image = color
        if self.color and image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif not self.color and image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        image = cv2.resize(image, None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
        return MinimapMapLocator(image, calibration)

    def _evict_layers(self):
        def resident(locator):
            return getattr(locator, "resident_bytes", locator.map_image.nbytes)

        self._cache_bytes = sum(resident(locator) for locator in self._cache.values()) + sum(
            locator.resident_bytes for locator in self._composites.values()
        )
        while self._cache_bytes > self._cache_limit_bytes and len(self._cache) > 1:
            removable = next((k for k in self._cache if k not in self._pinned_ids), None)
            if removable is None:
                # Keep the current overlapping working set intact. A strict LRU
                # smaller than that set can otherwise cycle forever without ever
                # checking every floor on one frame. Evict it when the ROI moves.
                break
            removed = self._cache.pop(removable)
            self._cache_bytes -= resident(removed)
            composite = self._composites.pop(removable, None)
            if composite is not None:
                self._cache_bytes -= composite.resident_bytes
                composite.close()
            # Prepared floor images release their atlas lease only through close().
            close = getattr(removed, "close", None)
            if callable(close):
                close()
            self._layer_errors.pop(removable, None)

    def _matching_locator(self, entry, floor):
        """Keep raw floor resources for ranking; match their rendered overlay."""
        if not isinstance(floor.map_image, NativeTileImage):
            return floor
        key = entry["id"]
        if key not in self._composites:
            image = CompositeFloorImage(
                floor.map_image,
                floor.calibration,
                self.base.map_image,
                self.base.calibration,
            )
            self._composites[key] = MinimapMapLocator(image, image.calibration)
        return self._composites[key]

    def _prepare_global_search(self, locator, frame, *, wait_for_resources=False):
        if self._prepared is not None:
            return True
        if self._resource_workers is None:
            return True
        if self._resource_workers.closed:
            return False
        prepare = getattr(locator, "prepare_global_search", None)
        options = {"wait": True, "stop": self._resource_workers.stop} if wait_for_resources else {}
        return not callable(prepare) or prepare(frame.shape[1], self._resource_workers.resources, **options)

    def reset_proposals(self):
        """Discard only untrusted order/position hints, never reuse observations."""
        self._proposal_index = 0
        self._background_hint = None
        self._ranked_tiers = ()
        self._ranked_entry_key = None
        self._ranked_signature = None
        self._ranked_shape = None
        self._ranked_at = 0.0
        self._ranked_complete = False

    def release_proposal_resources(self):
        """Keep compact ordering hints after validation, not ranking pixels."""
        self._search_index = None
        self._index_key = None
        self._pending_index_key = None

    def seed_proposals(self, base_result):
        # Base matching already includes independently normalized dim terrain.
        # Preserve only its best coordinate as a place to rank floor hypotheses.
        scope = self._navigation_scope
        if base_result is not None and (scope is None or scope.allows_tier(self.map_id, "__main__")):
            self._background_hint = (base_result.best.world_x, base_result.best.world_z)
        else:
            self._background_hint = None

    def _await_resources(self, futures):
        workers = self._resource_workers
        # _load adopts each result and applies the existing per-floor
        # failure/backoff rules; broken competitors cannot prove a tier.
        with suppress(OSError, ValueError):
            ordered_results(tuple(futures), None if workers is None else workers.stop)

    def _warm_layers(self, entries, *, wait_for_resources):
        """Submit all required resources before waiting; keep this set pinned."""
        self._pinned_ids.update(entry["id"] for entry in entries)
        ready, pending = [], []
        for entry in entries:
            try:
                ready.append((entry, self._load(entry)))
            except ResourcePending:
                pending.append(entry)
            except (OSError, ValueError):
                pass
        if pending and wait_for_resources:
            self._await_resources(
                self._pending_layers[entry["id"]] for entry in pending if entry["id"] in self._pending_layers
            )
            for entry in pending:
                with suppress(OSError, ValueError):
                    ready.append((entry, self._load(entry)))
        elif pending:
            raise ResourcePending()
        return ready

    def _background_proposals(self, frame, geometry, template, *, wait_for_resources, entries=None):
        if self._bundle is None or self._background_hint is None or template.dim_terrain is None:
            return []
        # A base-map hint does not limit the eventual floor search. It only
        # identifies small rendered patches worth checking before raw thumbnails.
        prior, radius = self._background_hint, 8.0
        entries = self._allowed_entries() if entries is None else entries
        entries = [entry for entry in entries if self._near_bounds(entry, prior, radius)]
        ready = self._warm_layers(entries, wait_for_resources=wait_for_resources)
        ready = [(entry, self._matching_locator(entry, floor)) for entry, floor in ready]
        if not ready:
            return []
        results = self._match_near_layers(frame, geometry, prior, radius, ready)
        proposals = [
            (result.confidence, entry["id"])
            for (entry, _), result in zip(ready, results, strict=True)
            if result is not None
            and not isinstance(result, (OSError, ValueError))
            and strong_candidate(result.best, result.margin)
        ]
        return sorted(proposals, reverse=True)

    def _single_sided_proposals(self, template, frame_width, *, wait_for_resources, entries=None):
        entries = self._allowed_entries() if entries is None else entries
        ranked = self._rank_raw_layers(
            template,
            frame_width,
            wait_for_resources=wait_for_resources,
            with_positions=True,
            entries=entries,
        )
        entries_by_id = {entry["id"]: entry for entry in entries}
        proposals = []
        for index, (score, tier, center) in enumerate(ranked):
            if tier not in entries_by_id:
                continue
            if index < 3:
                bounds = entries_by_id[tier]["bounds"]
                ppm = LayerSearchIndex.pixels_per_meter
                prior = (bounds["west"] + center[0] / ppm, bounds["north"] - center[1] / ppm)
                try:
                    background = self.base.proposal_background_score(template, frame_width, prior)
                except (OSError, ValueError):
                    background = 0.0
                # Dark terrain can promote an ordering hint, never remove a
                # candidate or supply the final floor score/position/margin.
                score += (1 - score) * background
            proposals.append((score, tier))
        return sorted(proposals, reverse=True)

    @staticmethod
    def _near_bounds(entry, prior, radius):
        if prior is None:
            return True
        b = entry["bounds"]
        dx = max(b["west"] - prior[0], 0, prior[0] - b["east"])
        dz = max(b["south"] - prior[1], 0, prior[1] - b["north"])
        return math.hypot(dx, dz) <= radius

    def _search(self, frame, geometry, prior=None, radius=80.0, *, base_result=_UNSEARCHED, extra_candidates=()):
        started = time.perf_counter()
        self.last_failure_reason = ""
        base = self._base_result(frame, geometry, prior, radius, base_result)
        base_missing = base is None
        if base_missing and self._invalid_base_search():
            self._recent = None
            return None
        scope = self._navigation_scope

        def allows_tier(tier):
            return scope is None or scope.allows_tier(self.map_id, tier)

        search_prior = prior
        if base is not None and prior is None and allows_tier("__main__") and strong_candidate(base.best, base.margin):
            search_prior = (base.best.world_x, base.best.world_z)

        scope_excluded = not allows_tier("__main__")
        candidates = (
            [replace(c, tier_id="__main__") for c in (base.competition_candidates or base.candidates)]
            if base is not None and allows_tier("__main__")
            else []
        )
        candidates.extend(c for c in extra_candidates if allows_tier(c.tier_id))
        complete = self.tier_coverage_complete
        scan_pending = False
        entries = [entry for entry in self.entries if allows_tier(entry["id"])]
        scope_excluded = len(entries) != len(self.entries)
        global_scan = search_prior is None and bool(entries)
        if global_scan and len(entries) > 1:
            # A weak main-map frame must not decode and globally scan dozens of
            # floors in one synchronous sample. Probe one floor per call; a strong
            # proposal seeds bounded competition with all overlapping floors next.
            scan_entry = entries[self._scan_index % len(entries)]
            entries = [scan_entry]
            if self._resource_workers is None:
                self._scan_index += 1
            complete = False
            scan_pending = True
        else:
            scan_entry = None
        layer_candidates, layers_complete, layers_pending = self._layer_candidates(
            frame, geometry, search_prior, radius, entries
        )
        scan_failed = scan_entry is not None and scan_entry["id"] in self._last_layer_failed_ids
        if global_scan and self._resource_workers is not None and (not layers_pending or scan_failed):
            self._scan_index += 1
        candidates.extend(layer_candidates)
        if base_missing:
            if not candidates:
                self.last_failure_reason = (
                    self._scan_pending_reason()
                    if scan_pending or layers_pending
                    else "navigation_topology_no_candidates"
                )
                return None
            scale = self.base._scale(frame.shape[1]) if isinstance(frame, np.ndarray) and frame.ndim >= 2 else 1.0
            base = LocateResult(
                "rejected",
                getattr(self.base, "last_failure_reason", "") or "minimap_no_candidates",
                candidates[0],
                (),
                0.0,
                scale,
                prior is not None,
            )
        complete = complete and layers_complete
        scan_pending = scan_pending or layers_pending
        return self._finish_search(
            candidates,
            base,
            started,
            prior,
            complete=complete,
            scan_pending=scan_pending,
            scope_excluded=scope_excluded,
        )

    def _finish_search(
        self, candidates, base, started, prior, *, complete, scan_pending=False, margin_limit=None, scope_excluded=False
    ):
        scope = self._navigation_scope
        if not candidates:
            if scan_pending:
                errors = {
                    tier: error
                    for tier, error in self._layer_errors.items()
                    if not scope or scope.allows_tier(self.map_id, tier)
                }
                reason = (
                    "layer_resource_error: " + "; ".join(f"{tier}: {error}" for tier, error in sorted(errors.items()))
                    if errors
                    else "layer_scan_pending"
                )
                self.last_failure_reason = reason
                return replace(base, status="rejected", reason=reason, spatial_margin=0)
            return replace(base, status="rejected", reason="navigation_topology_no_candidates", spatial_margin=0)
        candidates = deduplicate_candidates(candidates)
        spatial = deduplicate_candidates([replace(c, tier_id=None) for c in candidates])
        status, reason = self.base._status(spatial)
        spatial_margin = candidate_margin(spatial)
        if margin_limit is not None:
            spatial_margin = min(spatial_margin, margin_limit)
        if status == "accepted" and spatial_margin < self.base.min_accept_margin:
            status, reason = "ambiguous", f"NCC={candidates[0].ncc:.3f}, margin={spatial_margin:.3f}"
        competing_layers = [c for c in candidates[1:] if c.tier_id != candidates[0].tier_id]
        layer_margin = candidate_margin([candidates[0], *competing_layers])
        reachable = scope is None or scope.allows_candidate(
            self.map_id, candidates[0].tier_id, candidates[0].world_x, candidates[0].world_z
        )
        if not complete or layer_margin < (0.10 if self.color else 0.08):
            # Identical X/Z on two floors can prove position but cannot prove tier.
            candidates[0] = replace(candidates[0], tier_id=None)
        result = LocateResult(
            status if reachable else "rejected",
            reason if reachable else "navigation_topology_unreachable",
            candidates[0],
            tuple(candidates[: self.base.top_k]),
            time.perf_counter() - started,
            base.scale,
            prior is not None,
            spatial_margin=spatial_margin,
            layer_margin=layer_margin,
            competition_candidates=tuple(candidates),
        )
        # Keep same-floor competitors even when their cells are unreachable:
        # per-floor top-k truncation may hide weaker reachable alternatives.
        # Reachability can veto a peak, never manufacture spatial uniqueness.
        if reachable and strong_candidate(result.best, result.margin):
            self._recent = (time.monotonic(), (result.best.world_x, result.best.world_z))
            self._proposal_index = 0
        if scan_pending:
            # Resource warm-up is not evidence of uniqueness. Hold publication
            # until all nearby floor alternatives have been checked on one frame.
            errors = {
                tier: error
                for tier, error in self._layer_errors.items()
                if not self._navigation_scope or self._navigation_scope.allows_tier(self.map_id, tier)
            }
            reason = (
                "layer_resource_error: " + "; ".join(f"{tier}: {error}" for tier, error in sorted(errors.items()))
                if errors
                else "layer_scan_pending"
            )
            self.last_failure_reason = reason
            result = replace(result, status="rejected", reason=reason, spatial_margin=0)
        return result

    def _scan_pending_reason(self):
        scope = self._navigation_scope
        errors = {
            tier: error
            for tier, error in self._layer_errors.items()
            if scope is None or scope.allows_tier(self.map_id, tier)
        }
        if not errors:
            return "layer_scan_pending"
        return "layer_resource_error: " + "; ".join(f"{tier}: {error}" for tier, error in sorted(errors.items()))

    def combine_global_results(self, base_result, layer_results):
        """Merge one immutable frame's complete searches without matching any ROI again."""
        started = time.perf_counter()
        scope = self._navigation_scope
        sources = [("__main__", base_result), *layer_results]
        candidates, owners = [], []
        for tier, result in sources:
            if result is None or (scope is not None and not scope.allows_tier(self.map_id, tier)):
                continue
            for candidate in result.competition_candidates or result.candidates:
                candidates.append(replace(candidate, tier_id=tier))
                owners.append(result)
        base = next((r for _, r in sources if r is not None), None)
        if base is None:
            return None
        best_index = max(range(len(candidates)), key=lambda i: candidates[i].ncc) if candidates else None
        limit = owners[best_index].margin if best_index is not None else 1.0
        result = self._finish_search(
            candidates, base, started, None, complete=self.tier_coverage_complete, margin_limit=limit
        )
        if result is not None and strong_candidate(result.best, result.margin):
            self.release_proposal_resources()
        return result

    def _base_result(self, frame, geometry, prior, radius, base_result):
        if base_result is not _UNSEARCHED:
            return base_result
        if prior is None:
            return self.base.locate_global(frame, geometry)
        return self.base.locate_near(frame, prior, geometry, search_radius_m=radius)

    def _world_candidates(self, result, entry):
        bounds = entry["bounds"]
        for candidate in result.competition_candidates or result.candidates:
            if not (
                bounds["west"] <= candidate.world_x <= bounds["east"]
                and bounds["south"] <= candidate.world_z <= bounds["north"]
            ):
                continue
            x, y = self.calibration.world_to_map(candidate.world_x, candidate.world_z)
            yield replace(candidate, map_x=x, map_y=y, tier_id=entry["id"])

    def _layer_candidates(self, frame, geometry, prior, radius, entries):
        candidates = []
        failed = set()
        self._last_layer_failed_ids = failed
        complete, pending, decoded = True, False, False
        bundled_near = self._bundle is not None and prior is not None
        ready = []
        self._pinned_ids = {entry["id"] for entry in entries if self._near_bounds(entry, prior, radius)}
        for key in list(self._pending_layers):
            if key not in self._pinned_ids and key not in self._layer_errors:
                self._pending_layers.pop(key).cancel()
        for entry in entries:
            if not self._near_bounds(entry, prior, radius):
                continue
            if time.monotonic() < self._resource_retry_at.get(entry["id"], 0):
                failed.add(entry["id"])
                pending, complete = True, False
                continue
            if self._resource_workers is None and entry["id"] not in self._cache and decoded:
                pending, complete = True, False
                continue
            try:
                decoded = decoded or entry["id"] not in self._cache
                locator = self._load(entry)
            except ResourcePending:
                pending, complete = True, False
                continue
            except (OSError, ValueError):
                failed.add(entry["id"])
                pending, complete = True, False
                continue
            try:
                locator = self._matching_locator(entry, locator)
                if prior is None and not self._prepare_global_search(locator, frame):
                    pending, complete = True, False
                    continue
                if bundled_near:
                    ready.append((entry, locator))
                    continue
                result = (
                    locator.locate_global(frame, geometry)
                    if prior is None
                    else locator.locate_near(frame, prior, geometry, search_radius_m=radius)
                )
            except (OSError, ValueError) as exc:
                self._layer_errors[entry["id"]] = str(exc)
                failed.add(entry["id"])
                pending, complete = True, False
                continue
            self._layer_errors.pop(entry["id"], None)
            if result is not None:
                candidates.extend(self._world_candidates(result, entry))
        if ready:
            for (entry, _), result in zip(
                ready, self._match_near_layers(frame, geometry, prior, radius, ready), strict=True
            ):
                if isinstance(result, (OSError, ValueError)):
                    self._layer_errors[entry["id"]] = str(result)
                    failed.add(entry["id"])
                    pending, complete = True, False
                    continue
                self._layer_errors.pop(entry["id"], None)
                if result is not None:
                    candidates.extend(self._world_candidates(result, entry))
        # Region reads grow tiled locators after loading their metadata. Recount
        # on cache hits too, once the current overlapping set has been checked.
        self._evict_layers()
        return candidates, complete, pending

    def _match_near_layers(self, frame, geometry, prior, radius, ready):
        first = ready[0][1]
        template = first._prepare_template(frame, geometry)
        if template is None:
            for _, locator in ready:
                locator.last_failure_reason = first.last_failure_reason
            return [None] * len(ready)
        args = (template, frame.shape[1], prior, radius)

        def match(locator):
            try:
                return locator._locate_near_template(*args)
            except (OSError, ValueError) as exc:
                return exc

        workers = self._resource_workers
        if workers is None or len(ready) == 1:
            return [match(locator) for _, locator in ready]

        # Only pass the prepared pixels, with no reference to the capture
        # buffer. The shared template is immutable before entering the pool.
        template.flat.setflags(write=False)
        template.ring.setflags(write=False)
        template.mask.setflags(write=False)
        template.mask8.setflags(write=False)
        _ = template.color_zncc
        _ = template.dim_terrain
        futures = []
        try:
            for _, locator in ready:
                futures.append(workers.matches.submit(match, locator))
        finally:
            # Drain even after a submission or matching failure. Another
            # frame must never reuse a floor locator while it is still busy.
            results = ordered_results(futures)
        return results

    def search_complete_frame(self, frame, geometry, *, base_result):
        """Complete-frame floor competition without the _recent shortcut."""
        return self._search(frame, geometry, base_result=base_result)

    def locate_global(self, frame, geometry, *, base_result=_UNSEARCHED, use_index=True):
        # This is only a search hint; publication freshness belongs to the
        # tracker. Resource decoding and turning must not erase the ROI hint.
        if self._recent is not None:
            result = self._search(frame, geometry, self._recent[1])
            if result is not None and (
                result.reason == "layer_scan_pending"
                or (result.reason != "navigation_topology_unreachable" and strong_candidate(result.best, result.margin))
            ):
                return result
        if base_result is _UNSEARCHED:
            base_result = self.base.locate_global(frame, geometry)
        if base_result is None:
            if self._invalid_base_search():
                self._recent = None
                return None
        else:
            self.seed_proposals(base_result)
        main_allowed = self._navigation_scope is None or self._navigation_scope.allows_tier(self.map_id, "__main__")
        if use_index and (
            base_result is None or not strong_candidate(base_result.best, base_result.margin) or not main_allowed
        ):
            try:
                options = {"required_count": min(8, self._proposal_index + 1)} if self._proposal_index else {}
                proposals = self.rank_layers(frame, geometry, **options)
                if proposals and self._proposal_index < min(8, len(proposals)):
                    result = self.locate_proposed_layer(frame, geometry, proposals[self._proposal_index][1])
                    if result is not None:
                        if result.reason == "navigation_topology_unreachable" or (
                            result.reason != "layer_scan_pending" and not strong_candidate(result.best, result.margin)
                        ):
                            self._proposal_index += 1
                        return result
                    self._proposal_index += 1
                else:
                    self._proposal_index = 0
            except ResourcePending:
                if base_result is None:
                    self.last_failure_reason = self._scan_pending_reason()
                    return None
                return replace(base_result, status="rejected", reason="layer_scan_pending", spatial_margin=0)
        return self._search(frame, geometry, base_result=base_result)

    def _invalid_base_search(self):
        reason = str(getattr(self.base, "last_failure_reason", ""))
        return reason.startswith(
            (
                "minimap_frame_missing_or_invalid",
                "minimap_roi_out_of_bounds",
                "minimap_mask_too_small",
                "minimap_insufficient_texture",
                "minimap_icon_occlusion",
            )
        )

    def rank_layers(
        self,
        frame,
        geometry,
        *,
        wait_for_resources=False,
        required_count=1,
        fresh_scores=False,
        entries=None,
    ):
        """Rank only; coarse scores can never become accepted coordinates."""
        entries = self._allowed_entries() if entries is None else list(entries)
        if not entries:
            return []
        allowed = {entry["id"] for entry in entries}
        entry_key = tuple(entry["id"] for entry in entries)
        filter_scope = self._navigation_scope is not None
        template = self.base._prepare_template(frame, geometry)
        if template is None:
            return []
        if self._resource_workers is not None and self._resource_workers.closed:
            raise ResourcePending()
        signature = cv2.resize(template.flat, (16, 16), interpolation=cv2.INTER_AREA)
        now = time.monotonic()
        reusable = (
            not fresh_scores
            and self._ranked_tiers
            and self._ranked_entry_key == entry_key
            and self._ranked_shape == frame.shape
            and 0 <= now - self._ranked_at <= 10
            and cv2.norm(signature, self._ranked_signature, cv2.NORM_L1) / signature.size <= 8
        )
        if reusable and (self._ranked_complete or len(self._ranked_tiers) >= required_count):
            # Only IDs survive. Scores from earlier frames cannot supply
            # confidence, coordinates, coverage or temporal confirmation.
            return [(0.0, tier) for tier in self._ranked_tiers if not filter_scope or tier in allowed]
        if (
            not reusable
            and getattr(template, "dim_terrain", None) is None
            and getattr(template, "proposal_dim_mask", None) is not None
        ):
            proposals = self._single_sided_proposals(
                template,
                frame.shape[1],
                wait_for_resources=wait_for_resources,
                entries=entries,
            )
            self._ranked_complete = True
        else:
            if reusable:
                background = [(0.0, tier) for tier in self._ranked_tiers]
            else:
                try:
                    background = self._background_proposals(
                        frame,
                        geometry,
                        template,
                        wait_for_resources=wait_for_resources,
                        entries=entries,
                    )
                except (OSError, ValueError):
                    background = []
            if not reusable:
                self._ranked_complete = False
            proposals = background
            if len(background) < required_count:
                raw = self._rank_raw_layers(
                    template,
                    frame.shape[1],
                    wait_for_resources=wait_for_resources,
                    entries=entries,
                )
                preferred = {tier for _, tier in background}
                proposals = background + [(score, tier) for score, tier in raw if tier not in preferred]
                self._ranked_complete = True
        if filter_scope:
            proposals = [(score, tier) for score, tier in proposals if tier in allowed]
        self._ranked_tiers = tuple(tier for _, tier in proposals)
        self._ranked_entry_key = entry_key
        self._ranked_signature = signature
        self._ranked_shape = frame.shape
        self._ranked_at = time.monotonic()
        return proposals

    def _rank_raw_layers(self, template, frame_width, *, wait_for_resources, with_positions=False, entries=None):
        entries = self._allowed_entries() if entries is None else list(entries)
        if not entries:
            return []
        self.prepare_layer_index(wait_for_resources=wait_for_resources, entries=entries)
        options = {"with_positions": True} if with_positions else {}
        if self._prepared is not None or self._resource_workers is None:
            return self._search_index.rank(template, self._scale(frame_width), **options)
        if self._resource_workers.closed:
            raise ResourcePending()
        return self._search_index.rank(
            template,
            self._scale(frame_width),
            executor=self._resource_workers.rankings,
            stop=self._resource_workers.stop,
            **options,
        )

    def prepare_layer_index(self, *, wait_for_resources=False, entries=None):
        """Prepare compact resources without producing visual evidence."""
        entries = self._allowed_entries() if entries is None else list(entries)
        if not entries:
            return
        index_key = tuple(entry["id"] for entry in entries)
        index_changed = self._search_index is not None and self._index_key is not None and self._index_key != index_key
        if self._search_index is None or index_changed:
            if index_changed:
                if self._pending_index is not None:
                    self._pending_index.cancel()
                    self._pending_index = None
                self._pending_index_key = None
                self._search_index = None
                self._index_key = None
            if self._prepared is not None:
                options = {"prepared": self._prepared}
            elif self._bundle is not None:
                options = {"bundle": self._bundle}
            else:
                options = {}
            if self._prepared is not None or self._resource_workers is None:
                self._search_index = LayerSearchIndex.build(self.root, entries, color=self.color, **options)
                self._index_key = index_key
            else:
                if self._resource_workers.closed:
                    raise ResourcePending()
                if self._pending_index is not None and self._pending_index_key != index_key:
                    self._pending_index.cancel()
                    self._pending_index = None
                    self._pending_index_key = None
                if self._pending_index is None:
                    self._pending_index = self._resource_workers.indexes.submit(
                        LayerSearchIndex.build,
                        self.root,
                        tuple(entries),
                        color=self.color,
                        resources=self._resource_workers.resources,
                        stop=self._resource_workers.stop,
                        **options,
                    )
                    self._pending_index_key = index_key
                future = self._pending_index
                submitted_key = self._pending_index_key
                if not wait_for_resources and not future.done():
                    raise ResourcePending()
                try:
                    if wait_for_resources:
                        ordered_results((future,), self._resource_workers.stop)
                    index = future.result()
                    if self._pending_index is not future or self._pending_index_key != submitted_key:
                        raise ResourcePending()
                    self._search_index = index
                    self._index_key = submitted_key
                finally:
                    if self._pending_index is future:
                        self._pending_index = None
                        self._pending_index_key = None

    def locate_proposed_layer(
        self,
        frame,
        geometry,
        tier_id,
        *,
        wait_for_resources=False,
        complete_competition=False,
        base_result=_UNSEARCHED,
    ):
        pending = self._pending_layers.get(tier_id)
        if wait_for_resources and pending is not None:
            self._await_resources((pending,))
        pending = self._pending_layers.get(tier_id)
        if pending is not None and not pending.done():
            # Waiting frames add no uniqueness evidence. Once resources finish,
            # redo the full search and overlap competition on a fresh frame.
            raise ResourcePending()
        entry = next(entry for entry in self.entries if entry["id"] == tier_id)
        self._pinned_ids = {tier_id}
        if wait_for_resources:
            self._warm_layers([entry], wait_for_resources=True)
        try:
            floor = self._load(entry)
        except (OSError, ValueError):
            if complete_competition:
                raise ResourcePending() from None
            return None
        try:
            floor = self._matching_locator(entry, floor)
            if not self._prepare_global_search(floor, frame, wait_for_resources=wait_for_resources):
                raise ResourcePending()
            result = floor.locate_global(frame, geometry)
        except (OSError, ValueError) as exc:
            self._layer_errors[tier_id] = str(exc)
            if complete_competition:
                raise ResourcePending() from exc
            return None
        else:
            self._layer_errors.pop(tier_id, None)
        finally:
            if complete_competition:
                self._evict_layers()
        if result is None or (not complete_competition and not strong_candidate(result.best, result.margin)):
            return None
        candidates = tuple(self._world_candidates(result, entry))
        if not candidates:
            return None
        if complete_competition:
            # MapSetLocator owns all maps/floors for this same frame. Preserve the
            # full-floor alternatives here and merge them once after every search.
            return replace(
                result,
                best=candidates[0],
                candidates=candidates,
                competition_candidates=candidates,
                spatial_margin=result.margin,
            )
        prior = (result.best.world_x, result.best.world_z)
        if wait_for_resources:
            # The immutable background snapshot and its full-floor result stay
            # in this one acquisition job. Publication still requires new frames.
            scope = self._navigation_scope
            entries = [
                e
                for e in self.entries
                if self._near_bounds(e, prior, 80) and (scope is None or scope.allows_tier(self.map_id, e["id"]))
            ]
            self._warm_layers(entries, wait_for_resources=True)
        combined = self._search(frame, geometry, prior, base_result=base_result, extra_candidates=candidates)
        if combined is not None and strong_candidate(combined.best, combined.margin):
            self.release_proposal_resources()
        return combined

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0):
        return self._search(frame, geometry, previous_world, search_radius_m)
