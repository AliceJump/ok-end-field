"""Compete base-map and layer images in one calibrated world X/Z space."""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from src.localization.layer_search_index import LayerSearchIndex
from src.localization.localization_workers import ResourcePending
from src.localization.minimap_map_locator import (
    LocateResult,
    MapCalibration,
    MinimapMapLocator,
    candidate_margin,
    deduplicate_candidates,
    strong_candidate,
)


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
    def __init__(self, base, map_id, layer_root, catalog):
        self.base, self.map_id = base, map_id
        self.color = getattr(base, "color", False) is True
        self.calibration = base.calibration
        self.root = Path(layer_root).resolve()
        entries = catalog.get("layers", [])
        if not all(isinstance(e, dict) for e in entries):
            raise ValueError("invalid_visual_layer_entry")
        self.entries = [dict(e) for e in entries if e.get("map_name") == map_id and e.get("map_category") == "layered"]
        for entry in self.entries:
            if not all(k in entry for k in ("id", "path", "width", "height", "bounds")):
                raise ValueError("invalid_visual_layer_entry")
            if not all(isinstance(entry[k], str) and entry[k] for k in ("id", "path")):
                raise ValueError("invalid_visual_layer_entry")
            layer_calibration(entry["bounds"], entry["width"], entry["height"])
            entry["bounds"] = {k: float(entry["bounds"][k]) for k in ("west", "east", "south", "north")}
            if not (self.root / entry["path"]).resolve().is_relative_to(self.root):
                raise ValueError("layer image escapes resource root")
        self._cache = OrderedDict()
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
        self.tier_coverage_complete = not any(
            str(k).startswith(f"scene_{map_id}_") for k in catalog.get("missing_logic_ids", [])
        )

    def _scale(self, width):
        return self.base._scale(width)

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
                self._pending_layers[key] = self._resource_workers.resources.submit(self._decode, dict(entry))
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
        self._cache_bytes += locator.map_image.nbytes
        self._evict_layers()
        return locator

    def _decode(self, entry):
        """Decode without changing caches, search hints or tracking state."""
        key = entry["id"]
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
        calibration, (fx, fy) = layer_calibration(entry["bounds"], width, height)
        image = cv2.resize(image, None, fx=fx, fy=fy, interpolation=cv2.INTER_AREA)
        return MinimapMapLocator(image, calibration)

    def _evict_layers(self):
        while self._cache_bytes > self._cache_limit_bytes and len(self._cache) > 1:
            removable = next((k for k in self._cache if k not in self._pinned_ids), None)
            if removable is None:
                # Keep the current overlapping working set intact. A strict LRU
                # smaller than that set can otherwise cycle forever without ever
                # checking every floor on one frame. Evict it when the ROI moves.
                break
            removed = self._cache.pop(removable)
            self._cache_bytes -= removed.map_image.nbytes

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
        candidates = [replace(c, tier_id="__main__") for c in base.candidates]
        candidates.extend(extra_candidates)
        complete = self.tier_coverage_complete
        scan_pending = False
        entries = self.entries
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
        candidates = deduplicate_candidates(candidates)
        spatial = deduplicate_candidates([replace(c, tier_id=None) for c in candidates])
        status, reason = self.base._status(spatial)
        spatial_margin = candidate_margin(spatial)
        competing_layers = [c for c in candidates[1:] if c.tier_id != candidates[0].tier_id]
        layer_margin = candidate_margin([candidates[0], *competing_layers])
        if not complete or layer_margin < (0.10 if self.color else 0.08):
            # Identical X/Z on two floors can prove position but cannot prove tier.
            candidates[0] = replace(candidates[0], tier_id=None)
        result = LocateResult(
            status,
            reason,
            candidates[0],
            tuple(candidates[: self.base.top_k]),
            time.perf_counter() - started,
            base.scale,
            prior is not None,
            spatial_margin=spatial_margin,
            layer_margin=layer_margin,
        )
        if strong_candidate(result.best, result.margin):
            self._recent = (time.monotonic(), (result.best.world_x, result.best.world_z))
            self._proposal_index = 0
        if scan_pending:
            # Resource warm-up is not evidence of uniqueness. Hold publication
            # until all nearby floor alternatives have been checked on one frame.
            result = replace(result, status="rejected", reason="layer_scan_pending", spatial_margin=0)
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
            result = (
                locator.locate_global(frame, geometry)
                if prior is None
                else locator.locate_near(frame, prior, geometry, search_radius_m=radius)
            )
            if result is not None:
                candidates.extend(self._world_candidates(result, entry))
        return candidates, complete, pending

    def locate_global(self, frame, geometry, *, base_result=None, use_index=True):
        # This is only a search hint; publication freshness belongs to the
        # tracker. Resource decoding and turning must not erase the ROI hint.
        if self._recent is not None:
            result = self._search(frame, geometry, self._recent[1])
            if result is not None and (
                result.reason == "layer_scan_pending" or (strong_candidate(result.best, result.margin))
            ):
                return result
        base_result = base_result or self.base.locate_global(frame, geometry)
        if use_index and base_result is not None and not strong_candidate(base_result.best, base_result.margin):
            try:
                proposals = self.rank_layers(frame, geometry)
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

    def rank_layers(self, frame, geometry):
        """Rank only; coarse scores can never become accepted coordinates."""
        if not self.entries:
            return []
        template = self.base._prepare_template(frame, geometry)
        if template is None:
            return []
        self.prepare_layer_index()
        return self._search_index.rank(template, self._scale(frame.shape[1]))

    def prepare_layer_index(self):
        """Prepare compact resources without producing visual evidence."""
        if not self.entries:
            return
        if self._search_index is None:
            if self._resource_workers is None:
                self._search_index = LayerSearchIndex.build(self.root, self.entries, color=self.color)
            else:
                if self._resource_workers.closed:
                    raise ResourcePending()
                if self._pending_index is None:
                    self._pending_index = self._resource_workers.resources.submit(
                        LayerSearchIndex.build, self.root, tuple(self.entries), color=self.color
                    )
                if not self._pending_index.done():
                    raise ResourcePending()
                self._search_index = self._pending_index.result()
                self._pending_index = None

    def locate_proposed_layer(self, frame, geometry, tier_id):
        entry = next(entry for entry in self.entries if entry["id"] == tier_id)
        self._pinned_ids = {tier_id}
        try:
            floor = self._load(entry)
        except (OSError, ValueError):
            return None
        # Search the entire original floor, not a small ROI around a coarse
        # peak. Distant lookalikes remain competitors under the original gates.
        result = floor.locate_global(frame, geometry)
        if result is None or not strong_candidate(result.best, result.margin):
            return None
        candidates = tuple(self._world_candidates(result, entry))
        if not candidates:
            return None
        prior = (result.best.world_x, result.best.world_z)
        return self._search(frame, geometry, prior, extra_candidates=candidates)

    def locate_near(self, frame, previous_world, geometry, *, search_radius_m=80.0):
        return self._search(frame, geometry, previous_world, search_radius_m)
