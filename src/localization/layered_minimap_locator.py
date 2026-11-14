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
    def __init__(self, base, map_id, layer_root, catalog, *, bundle=None):
        self.base, self.map_id = base, map_id
        self.color = getattr(base, "color", False) is True
        self.calibration = base.calibration
        self.root = Path(layer_root).resolve()
        self._bundle = bundle
        entries = catalog.get("layers", [])
        if not all(isinstance(e, dict) for e in entries):
            raise ValueError("invalid_visual_layer_entry")
        self.entries = [dict(e) for e in entries if e.get("map_name") == map_id and e.get("map_category") == "layered"]
        seen_ids = set()
        for entry in self.entries:
            if not all(k in entry for k in ("id", "path", "width", "height", "bounds")):
                raise ValueError("invalid_visual_layer_entry")
            if not all(isinstance(entry[k], str) and entry[k] for k in ("id", "path")):
                raise ValueError("invalid_visual_layer_entry")
            if bundle is not None and entry["id"] in seen_ids:
                raise ValueError("duplicate_visual_layer_id")
            seen_ids.add(entry["id"])
            layer_calibration(entry["bounds"], entry["width"], entry["height"])
            entry["bounds"] = {k: float(entry["bounds"][k]) for k in ("west", "east", "south", "north")}
            if not (self.root / entry["path"]).resolve().is_relative_to(self.root):
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
        self._search_index = None
        self._pending_index = None
        self._proposal_index = 0
        self._background_hint = None
        self._ranked_tiers = ()
        self._ranked_signature = None
        self._ranked_at = 0.0
        self._ranked_shape = None
        self._ranked_complete = False
        self._navigation_scope = None
        self.tier_coverage_complete = not any(
            str(k).startswith(f"scene_{map_id}_") for k in catalog.get("missing_logic_ids", [])
        )

    def _scale(self, width):
        return self.base._scale(width)

    def set_navigation_scope(self, scope):
        """Apply a short-lived reachability envelope from a visual anchor."""
        self._navigation_scope = scope

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
        self._search_index = None
        self._cache.clear()
        self._cache_bytes = 0

    @property
    def last_failure_reason(self):
        return self.base.last_failure_reason

    def _load(self, entry):
        key = entry["id"]
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if time.monotonic() < self._resource_retry_at.get(key, 0):
            raise ValueError(f"layer_decode_backoff:{key}")
        if self._resource_workers is None:
            try:
                locator = self._decode(entry)
            except (OSError, ValueError):
                self._resource_retry_at[key] = time.monotonic() + 5
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
            except (OSError, ValueError):
                self._resource_retry_at[key] = time.monotonic() + 5
                raise
            finally:
                del self._pending_layers[key]
        self._cache[key] = locator
        self._evict_layers()
        return locator

    def _decode(self, entry):
        """Decode without changing caches, search hints or tracking state."""
        key = entry["id"]
        calibration, (fx, fy) = layer_calibration(entry["bounds"], entry["width"], entry["height"])
        if self._bundle is not None:
            return MinimapMapLocator(self._bundle.open_floor(self.root, entry), calibration)
        path = (self.root / entry["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("layer image escapes resource root")
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"missing_layer_image:{key}")
        height, width = image.shape[:2]
        if (width, height) != (entry["width"], entry["height"]):
            raise ValueError(f"layer_image_size_mismatch:{key}")
        if image.ndim == 3 and image.shape[2] == 4:
            color = image[:, :, :3].copy()
            color[image[:, :, 3] == 0] = 0
            image = color
        elif image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
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
        if self._resource_workers is None:
            return True
        if self._resource_workers.closed:
            return False
        prepare = getattr(locator, "prepare_global_search", None)
        options = {"wait": True, "stop": self._resource_workers.stop} if wait_for_resources else {}
        return not callable(prepare) or prepare(frame.shape[1], self._resource_workers.resources, **options)

    def reset_proposals(self):
        """Discard only untrusted order/position hints, never reuse observations."""
        self._background_hint = None
        self._ranked_tiers = ()
        self._ranked_signature = None
        self._ranked_shape = None
        self._ranked_at = 0.0
        self._ranked_complete = False

    def release_proposal_resources(self):
        """Keep compact ordering hints after validation, not ranking pixels."""
        self._search_index = None

    def seed_proposals(self, base_result):
        # Base matching already includes independently normalized dim terrain.
        # Preserve only its best coordinate as a place to rank floor hypotheses.
        if base_result is not None:
            self._background_hint = (base_result.best.world_x, base_result.best.world_z)

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

    def _background_proposals(self, frame, geometry, template, *, wait_for_resources):
        if self._bundle is None or self._background_hint is None or template.dim_terrain is None:
            return []
        # A base-map hint does not limit the eventual floor search. It only
        # identifies small rendered patches worth checking before raw thumbnails.
        prior, radius = self._background_hint, 8.0
        entries = [entry for entry in self.entries if self._near_bounds(entry, prior, radius)]
        ready = self._warm_layers(entries, wait_for_resources=wait_for_resources)
        ready = [(entry, self._matching_locator(entry, floor)) for entry, floor in ready]
        if not ready:
            return []
        results = self._match_near_layers(frame, geometry, prior, radius, ready)
        proposals = [
            (result.confidence, entry["id"])
            for (entry, _), result in zip(ready, results, strict=True)
            if result is not None and strong_candidate(result.best, result.margin)
        ]
        return sorted(proposals, reverse=True)

    def _single_sided_proposals(self, template, frame_width, *, wait_for_resources):
        ranked = self._rank_raw_layers(
            template, frame_width, wait_for_resources=wait_for_resources, with_positions=True
        )
        entries = {entry["id"]: entry for entry in self.entries}
        proposals = []
        for index, (score, tier, center) in enumerate(ranked):
            if index < 3:
                bounds = entries[tier]["bounds"]
                ppm = LayerSearchIndex.pixels_per_meter
                prior = (bounds["west"] + center[0] / ppm, bounds["north"] - center[1] / ppm)
                background = self.base.proposal_background_score(template, frame_width, prior)
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

    def _search(self, frame, geometry, prior=None, radius=80.0, *, base_result=None, extra_candidates=()):
        started = time.perf_counter()
        base = self._base_result(frame, geometry, prior, radius, base_result)
        if base is None:
            self._recent = None
            return None
        search_prior = prior
        if prior is None and strong_candidate(base.best, base.margin):
            search_prior = (base.best.world_x, base.best.world_z)
        scope = self._navigation_scope

        def allows_tier(tier):
            return scope is None or scope.allows_tier(self.map_id, tier)

        candidates = [replace(c, tier_id="__main__") for c in base.candidates] if allows_tier("__main__") else []
        candidates.extend(c for c in extra_candidates if allows_tier(c.tier_id))
        complete = self.tier_coverage_complete
        scan_pending = False
        entries = [entry for entry in self.entries if allows_tier(entry["id"])]
        global_scan = search_prior is None and bool(entries)
        if global_scan:
            # A weak main-map frame must not decode and globally scan dozens of
            # floors in one synchronous sample. Probe one floor per call; a strong
            # proposal seeds bounded competition with all overlapping floors next.
            entries = [entries[self._scan_index % len(entries)]]
            if self._resource_workers is None:
                self._scan_index += 1
            complete = False
            scan_pending = True
        layer_candidates, layers_complete, layers_pending = self._layer_candidates(
            frame, geometry, search_prior, radius, entries
        )
        if global_scan and self._resource_workers is not None and not layers_pending:
            self._scan_index += 1
        candidates.extend(layer_candidates)
        complete = complete and layers_complete
        scan_pending = scan_pending or layers_pending
        return self._finish_search(candidates, base, started, prior, complete=complete, scan_pending=scan_pending)

    def _finish_search(self, candidates, base, started, prior, *, complete, scan_pending=False, margin_limit=None):
        scope = self._navigation_scope
        if not candidates:
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
            result = replace(result, status="rejected", reason="layer_scan_pending", spatial_margin=0)
        return result

    def combine_global_results(self, base_result, layer_results):
        """Merge one immutable frame's complete searches without matching any ROI again."""
        started = time.perf_counter()
        scope = self._navigation_scope
        sources = [("__main__", base_result), *layer_results]
        candidates, owners = [], []
        for tier, result in sources:
            if result is None or (scope is not None and not scope.allows_tier(self.map_id, tier)):
                continue
            for candidate in result.candidates:
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
        if base_result is not None:
            return base_result
        if prior is None:
            return self.base.locate_global(frame, geometry)
        return self.base.locate_near(frame, prior, geometry, search_radius_m=radius)

    def _world_candidates(self, result, entry):
        bounds = entry["bounds"]
        for candidate in result.candidates:
            if not (
                bounds["west"] <= candidate.world_x <= bounds["east"]
                and bounds["south"] <= candidate.world_z <= bounds["north"]
            ):
                continue
            x, y = self.calibration.world_to_map(candidate.world_x, candidate.world_z)
            yield replace(candidate, map_x=x, map_y=y, tier_id=entry["id"])

    def _layer_candidates(self, frame, geometry, prior, radius, entries):
        candidates = []
        complete, pending, decoded = True, False, False
        bundled_near = self._bundle is not None and prior is not None
        ready = []
        self._pinned_ids = {entry["id"] for entry in entries if self._near_bounds(entry, prior, radius)}
        for key in list(self._pending_layers):
            if key not in self._pinned_ids:
                self._pending_layers.pop(key).cancel()
        for entry in entries:
            if not self._near_bounds(entry, prior, radius):
                continue
            if time.monotonic() < self._resource_retry_at.get(entry["id"], 0):
                complete = False
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
                complete = False
                continue
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
            if result is not None:
                candidates.extend(self._world_candidates(result, entry))
        if ready:
            for (entry, _), result in zip(
                ready, self._match_near_layers(frame, geometry, prior, radius, ready), strict=True
            ):
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
        workers = self._resource_workers
        if workers is None or len(ready) == 1:
            return [locator._locate_near_template(*args) for _, locator in ready]

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
                futures.append(workers.matches.submit(locator._locate_near_template, *args))
        finally:
            # Drain even after a submission or matching failure. Another
            # frame must never reuse a floor locator while it is still busy.
            results = ordered_results(futures)
        return results

    def locate_global(self, frame, geometry, *, base_result=_UNSEARCHED, use_index=True):
        # This is only a search hint; publication freshness belongs to the
        # tracker. Resource decoding and turning must not erase the ROI hint.
        if self._recent is not None:
            result = self._search(frame, geometry, self._recent[1])
            if result is not None and (
                result.reason == "layer_scan_pending" or (strong_candidate(result.best, result.margin))
            ):
                return result
        if base_result is _UNSEARCHED:
            base_result = self.base.locate_global(frame, geometry)
        if base_result is None:
            self._recent = None
            return None
        self.seed_proposals(base_result)
        if use_index and not strong_candidate(base_result.best, base_result.margin):
            try:
                options = {"required_count": min(8, self._proposal_index + 1)} if self._proposal_index else {}
                proposals = self.rank_layers(frame, geometry, **options)
                if proposals and self._proposal_index < min(8, len(proposals)):
                    result = self.locate_proposed_layer(frame, geometry, proposals[self._proposal_index][1])
                    if result is not None:
                        if result.reason != "layer_scan_pending" and not strong_candidate(result.best, result.margin):
                            self._proposal_index += 1
                        return result
                    self._proposal_index += 1
                else:
                    self._proposal_index = 0
            except ResourcePending:
                return replace(base_result, status="rejected", reason="layer_scan_pending", spatial_margin=0)
        return self._search(frame, geometry, base_result=base_result)

    def rank_layers(self, frame, geometry, *, wait_for_resources=False, required_count=1, fresh_scores=False):
        """Rank only; coarse scores can never become accepted coordinates."""
        if not self.entries:
            return []
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
            and self._ranked_shape == frame.shape
            and 0 <= now - self._ranked_at <= 10
            and cv2.norm(signature, self._ranked_signature, cv2.NORM_L1) / signature.size <= 8
        )
        if reusable and (self._ranked_complete or len(self._ranked_tiers) >= required_count):
            # Only IDs survive. Scores from earlier frames cannot supply
            # confidence, coordinates, coverage or temporal confirmation.
            return [(0.0, tier) for tier in self._ranked_tiers]
        if (
            not reusable
            and getattr(template, "dim_terrain", None) is None
            and getattr(template, "proposal_dim_mask", None) is not None
        ):
            proposals = self._single_sided_proposals(template, frame.shape[1], wait_for_resources=wait_for_resources)
            self._ranked_complete = True
        else:
            background = (
                [(0.0, tier) for tier in self._ranked_tiers]
                if reusable
                else self._background_proposals(frame, geometry, template, wait_for_resources=wait_for_resources)
            )
            if not reusable:
                self._ranked_complete = False
            proposals = background
            if len(background) < required_count:
                raw = self._rank_raw_layers(template, frame.shape[1], wait_for_resources=wait_for_resources)
                preferred = {tier for _, tier in background}
                proposals = background + [(score, tier) for score, tier in raw if tier not in preferred]
                self._ranked_complete = True
        self._ranked_tiers = tuple(tier for _, tier in proposals)
        self._ranked_signature = signature
        self._ranked_shape = frame.shape
        self._ranked_at = time.monotonic()
        return proposals

    def _rank_raw_layers(self, template, frame_width, *, wait_for_resources, with_positions=False):
        self.prepare_layer_index(wait_for_resources=wait_for_resources)
        options = {"with_positions": True} if with_positions else {}
        if self._resource_workers is None:
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

    def prepare_layer_index(self, *, wait_for_resources=False):
        """Prepare compact resources without producing visual evidence."""
        if not self.entries:
            return
        if self._search_index is None:
            options = {"bundle": self._bundle} if self._bundle is not None else {}
            if self._resource_workers is None:
                self._search_index = LayerSearchIndex.build(self.root, self.entries, color=self.color, **options)
            else:
                if self._resource_workers.closed:
                    raise ResourcePending()
                if self._pending_index is None:
                    self._pending_index = self._resource_workers.indexes.submit(
                        LayerSearchIndex.build,
                        self.root,
                        tuple(self.entries),
                        color=self.color,
                        resources=self._resource_workers.resources,
                        stop=self._resource_workers.stop,
                        **options,
                    )
                if wait_for_resources:
                    ordered_results((self._pending_index,), self._resource_workers.stop)
                if not self._pending_index.done():
                    raise ResourcePending()
                self._search_index = self._pending_index.result()
                self._pending_index = None

    def locate_proposed_layer(
        self, frame, geometry, tier_id, *, wait_for_resources=False, complete_competition=False, base_result=None
    ):
        if wait_for_resources:
            self._await_resources(self._pending_layers.values())
        if any(not future.done() for future in self._pending_layers.values()):
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
        floor = self._matching_locator(entry, floor)
        if not self._prepare_global_search(floor, frame, wait_for_resources=wait_for_resources):
            raise ResourcePending()
        # Search the entire original floor, not a small ROI around a coarse
        # peak. Distant lookalikes remain competitors under the original gates.
        try:
            result = floor.locate_global(frame, geometry)
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
            return replace(result, best=candidates[0], candidates=candidates, spatial_margin=result.margin)
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
