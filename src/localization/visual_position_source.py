"""Absolute map matching and visual anchor chains, without WS or packet input."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

from src.localization.minimap_map_locator import MinimapGeometry, MinimapTracker
from src.localization.minimap_odometry import MinimapOdometry
from src.localization.minimap_visual_anchor_chain import (
    MinimapKeyframeVisualAnchorChain,
    MinimapVisualAnchorChainCorrector,
    MinimapVisualChainEvidence,
)


@dataclass(frozen=True)
class KeyframeConfig:
    interval: int = 10
    edges: int = 8
    min_response: float = 0.3
    weight: float = 25.0


class VisualPositionSource:
    def __init__(
        self,
        task,
        locator,
        map_id,
        *,
        chain_enabled=True,
        window_size=60,
        edge_gap=12,
        min_response=0.25,
        max_correction_m=1.0,
        anchor_weight=50.0,
        commit_min_shift_px=2.0,
        keyframes=None,
    ):
        if keyframes is None:
            keyframes = KeyframeConfig()
        self.task, self.locator, self.map_id = task, locator, map_id
        self._configured_map_id = map_id
        self.tracker = MinimapTracker(locator, pending_max_gap_s=1.0)
        self.chain_enabled = chain_enabled
        self.chain_options = {
            "window_size": window_size,
            "edge_gap": edge_gap,
            "min_response": min_response,
            "max_correction_m": max_correction_m,
            "anchor_weight": anchor_weight,
        }
        self.commit_min_shift_px = max(0, commit_min_shift_px)
        self.keyframe_interval = max(1, keyframes.interval)
        self.keyframe_weight = keyframes.weight
        self.keyframe_options = {
            "max_edges": keyframes.edges,
            "min_response": keyframes.min_response,
            "anchor_weight": anchor_weight,
            "max_correction_m": max_correction_m,
        }
        self.keyframe_chain = None
        self._keyframe_tick = self._keyframe_uid = 0
        self.odometry = None
        self.chain = None
        self.evidence = None
        self._evidence_result = None
        self._shape = None
        self._origin = None
        self._last_absolute_t = None
        self._anchor_tier_id = None
        self._last_clock = None
        self._last_state = {}
        self._sequence = 0
        self._scale = None
        self._state_lock = threading.RLock()
        self._closed = False
        self._navigation_topology = None
        self._topology_anchor = None

    def set_navigation_topology(self, topology):
        with self._state_lock:
            if topology is self._navigation_topology:
                return
            self._navigation_topology = topology
            self._topology_anchor = None
            self._set_navigation_scope(None)
            state = self._last_state
            if topology is not None and state.get("absolute_position_valid") and state.get("tier_trusted"):
                self._remember_topology_anchor(state)

    def _set_navigation_scope(self, scope):
        setter = getattr(self.locator, "set_navigation_scope", None)
        if callable(setter):
            setter(scope)

    def _remember_topology_anchor(self, state):
        self._topology_anchor = (state["frame_t"], state["map_id"], state["tier_id"], (state["x"], state["z"]))

    def _prepare_navigation_scope(self, clock):
        anchor = self._topology_anchor
        scope = None
        if self._navigation_topology is not None and anchor is not None:
            age = clock - anchor[0]
            if 0 <= age <= self.tracker.stale_after_s:
                scope = self._navigation_topology.scope(
                    anchor[1],
                    anchor[2],
                    anchor[3],
                    self.tracker.consistency_distance_m + self.tracker.max_speed_m_s * age,
                )
            else:
                self._topology_anchor = None
        self._set_navigation_scope(scope)
        return scope

    def close(self):
        with self._state_lock:
            self._closed = True
            self.reset()
            close = getattr(self.locator, "close", None)
            if callable(close):
                close()

    def reset(self):
        with self._state_lock:
            self._reset()

    def _reset(self):
        self._topology_anchor = None
        self._set_navigation_scope(None)
        self.tracker.reset()
        self.map_id = self._configured_map_id
        reset_locator = getattr(self.locator, "reset_tracking", None)
        if callable(reset_locator):
            reset_locator()
        self.odometry = self.chain = None
        self.evidence = None
        self._evidence_result = None
        self.keyframe_chain = None
        self._keyframe_tick = self._keyframe_uid = 0
        self._shape = self._origin = self._last_absolute_t = self._last_clock = None
        self._anchor_tier_id = None
        self._last_state = {}
        self._sequence += 1

    @property
    def map_to_world_px(self):
        if self._scale is None:
            return None
        return tuple(tuple(float(v) for v in row) for row in self.locator.calibration.matrix[:, :2] / self._scale)

    @property
    def rest_diag(self):
        return {"map_speed_m_s": self._last_state.get("speed"), "reason": self._last_state.get("trust_reason")}

    def estimate(self):
        return dict(self._last_state) if self._last_state.get("position_trusted") else None

    def unavailable(self, reason, *, locate=None):
        with self._state_lock:
            return self._unavailable(reason, locate=locate)

    def _unavailable(self, reason, *, locate=None):
        self._last_state = {
            "x": None,
            "y": None,
            "z": None,
            "map_id": self.map_id,
            "position_trusted": False,
            "trust_reason": reason,
            "anchor_set": False,
            "sync_needed": False,
            "sync_supported": False,
            "sync_seq": self._sequence,
            "odom_ok": False,
            "odom_reason": reason,
            "rest": False,
            "source": "visual_map",
            "tier_id": None,
            "tier_trusted": False,
            "tier_evidence_required": True,
            "visual_confirmation_pending": False,
        }
        if locate is not None:
            # Keep rejection evidence observable without publishing a candidate
            # coordinate as usable navigation state.
            self._last_state.update(
                visual_confidence=locate.confidence,
                visual_full_ncc=locate.best.full_ncc,
                visual_margin=locate.margin,
                visual_match_elapsed_s=locate.elapsed_s,
                visual_locate_status=locate.status,
                visual_locate_reason=locate.reason,
                visual_candidate_tier_id=locate.best.tier_id,
                visual_candidate_tier_margin=locate.layer_margin,
            )
        return dict(self._last_state)

    def sample(self, frame, *, now=None):
        with self._state_lock:
            if self._closed:
                return self.unavailable("visual_source_closed")
            return self._sample(frame, now=now)

    def _sample(self, frame, *, now=None):
        clock = time.time() if now is None else float(now)
        if not math.isfinite(clock):
            raise ValueError("non-finite visual frame time")
        if frame is None:
            self.reset()
            return self.unavailable("no_frame")
        shape = frame.shape[:2]
        if shape == self._shape and clock == self._last_clock and self._last_state:
            # A retained presented frame is not another observation, even after
            # confirmation. Keep the anchor and its original freshness.
            return {**self._last_state, "visual_duplicate_frame": True}
        if shape != self._shape or (self._last_clock is not None and clock < self._last_clock):
            self._initialize_shape(shape)
        previous_clock = self._last_clock
        self._last_clock = clock
        geometry = MinimapGeometry.from_frame(shape[1], shape[0])
        measured = self.odometry.sample(frame, now=clock) if self.odometry is not None else None
        if measured and measured.get("reanchored") and not measured.get("benign_reanchor"):
            # A discontinuous frame must compete without the old floor filter,
            # even if a lookalike would otherwise produce a strong match.
            self._topology_anchor = None
            self._set_navigation_scope(None)
        predicted_world = None
        if (
            self._origin is not None
            and measured
            and measured.get("sampled")
            and measured.get("ok")
            and not measured.get("reanchored")
        ):
            current_delta = np.asarray(self.odometry.position_px())
            predicted = self._origin + current_delta
            if self._last_state.get("position_trusted"):
                previous_corrected = (
                    np.asarray(self.locator.calibration.world_to_map(self._last_state["x"], self._last_state["z"]))
                    * self._scale
                )
                predicted = previous_corrected + current_delta - np.asarray(self._last_state["dmap_px"])
            predicted_world = self.locator.calibration.map_to_world(*(predicted / self._scale))
            if previous_clock is None or clock - previous_clock > self.tracker.stale_after_s:
                predicted_world = None
        recovery_world = None
        topology_scope = self._prepare_navigation_scope(clock)
        if self._has_live_visual_chain(clock) and self._last_state.get("position_trusted"):
            recovery_world = predicted_world or (self._last_state["x"], self._last_state["z"])
        elif topology_scope is not None:
            # Ambiguity may break the motion chain, but a recent absolute fix
            # still bounds reachable floors. It never becomes a published pose.
            recovery_world = self._topology_anchor[3]
        match = self.tracker.update(
            frame,
            geometry,
            now=clock,
            prediction_world=predicted_world,
            prediction_radius_m=self.tracker.min_search_radius_m,
            recovery_world=recovery_world,
            recovery_map_id=self.map_id,
        )
        match, measured = self._confirm_motion(frame, match, clock, measured)
        # A pending NCC observation may still be propagated for a bounded
        # interval through verified visual edges. Its candidate position is
        # never used as a position.
        if self._origin is not None and match.locate is not None and self._identity_changed(match.locate.best):
            return self._lose_tracking(match)
        if not match.accepted and not self._can_continue_visual_chain(match, clock):
            return self._lose_tracking(match)
        delta, anchor, strong = self._update_absolute_anchor(match, clock)
        if self._origin is None or self._last_absolute_t is None:
            return self._lose_tracking(match)
        if (
            not strong
            and measured
            and measured.get("reason") == "too_soon"
            and self._last_state.get("position_trusted")
            and self._evidence_result is not None
            and self._evidence_result.trusted
        ):
            # Throttling is not a failed visual edge. Return the last measured
            # position with its ORIGINAL frame time; neither evidence nor its
            # inherited budget/absolute anchor is advanced by this request.
            return {
                **self._last_state,
                "visual_duplicate_frame": True,
                "absolute_position_valid": False,
                "odom_ok": False,
                "odom_reason": "too_soon",
            }
        if not strong and measured and measured.get("reanchored") and not measured.get("benign_reanchor"):
            self._topology_anchor = None
            self._set_navigation_scope(None)
            self.tracker.reset()
            self._lose_tracking(match)
            return self.unavailable("visual_odometry_lost")
        raw = self._origin + delta
        corrected, chain_result = self._correct_visual_chains(
            clock, raw, anchor, sampled=bool(measured and measured.get("sampled", False))
        )
        if (
            not strong
            and (not match.accepted or self.chain is not None)
            and not self._chain_within_error_budget(chain_result)
        ):
            evidence = self._evidence_result
            self._lose_tracking(match)
            self._last_state.update(
                trust_reason=f"visual_chain_{evidence.reason}" if evidence else "visual_chain_evidence_lost",
                visual_chain_error_budget_m=(
                    evidence.error_m if evidence and math.isfinite(evidence.error_m) else None
                ),
            )
            return dict(self._last_state)
        return self._publish_visual_state(clock, match, measured, delta, corrected, chain_result, strong)

    def _can_continue_visual_chain(self, match, clock):
        # Unknown tier is absence of a new floor observation. It cannot replace
        # the anchor, and propagation still requires fresh edges and the full
        # inherited error/residual budget below. A known transition breaks it.
        return bool(
            match.locate is not None
            and self._has_live_visual_chain(clock)
            and self._anchor_tier_id is not None
            and not self._identity_changed(match.locate.best)
        )

    def _has_live_visual_chain(self, clock):
        return bool(
            self._origin is not None
            and self._last_absolute_t is not None
            and self.chain is not None
            and self.evidence is not None
            and self.evidence.previous is not None
            and self._evidence_result is not None
            and self._evidence_result.trusted
            and 0 < clock - self.evidence.previous.time <= self.evidence.max_gap_s
        )

    def _identity_changed(self, candidate):
        return candidate.map_id not in (None, self.map_id) or (
            self._anchor_tier_id is not None and candidate.tier_id not in (None, self._anchor_tier_id)
        )

    def _chain_within_error_budget(self, result):
        if (
            result is None
            or not result.valid
            or not result.connected_to_anchor
            or self._evidence_result is None
            or not self._evidence_result.trusted
        ):
            return False
        residual_limit = self.chain._max_residual_px
        return result.residual_median <= residual_limit and result.residual_max <= residual_limit * 2

    def _initialize_shape(self, shape):
        self.reset()
        self._shape = shape
        self._scale = self.locator._scale(shape[1])
        self.odometry = MinimapOdometry(
            self.task, commit_min_shift_px=self.commit_min_shift_px, scale_m_per_px=1 / (4 * self._scale)
        )
        self.chain = (
            MinimapVisualAnchorChainCorrector(
                scale_m_per_px=1 / (4 * self._scale),
                **self.chain_options,
            )
            if self.chain_enabled
            else None
        )
        self.keyframe_chain = (
            MinimapKeyframeVisualAnchorChain(scale_m_per_px=1 / (4 * self._scale), **self.keyframe_options)
            if self.chain_enabled
            else None
        )
        self.evidence = (
            MinimapVisualChainEvidence(
                scale_m_per_px=1 / (4 * self._scale),
                max_error_m=self.chain_options["max_correction_m"],
                min_response=max(self.chain_options["min_response"], self.keyframe_options["min_response"]),
                keyframe_interval=self.keyframe_interval,
                max_keyframes=self.keyframe_options["max_edges"] * 2,
                max_gap_s=self.tracker.stale_after_s,
            )
            if self.chain_enabled
            else None
        )

    def _confirm_motion(self, frame, match, clock, measured=None):
        if (
            not match.accepted
            and match.status == "pending"
            and self._origin is not None
            and self._last_absolute_t is not None
            and 0 < clock - self._last_absolute_t <= 4.0
        ):
            if measured is None:
                measured = self.odometry.sample(frame, now=clock)
            response = measured.get("response", 0.0)
            if (
                measured.get("sampled")
                and measured.get("ok")
                and not measured.get("reanchored")
                and math.isfinite(response)
                and response >= 0.4
            ):
                predicted = self._origin + np.asarray(self.odometry.position_px())
                match = self.tracker.confirm_visual_continuity(
                    match, self.locator.calibration.map_to_world(*(predicted / self._scale)), now=clock
                )
        return match, measured

    def _lose_tracking(self, match):
        if match.locate is None:
            self._topology_anchor = None
            self._set_navigation_scope(None)
            reset_locator = getattr(self.locator, "reset_tracking", None)
            if callable(reset_locator) and match.reason != "visual_global_search_pending":
                reset_locator()
        # Never let a chain propagate through loading, teleportation or loss.
        self._origin = None
        self._last_absolute_t = None
        self._anchor_tier_id = None
        self.odometry.reset()
        if self.chain is not None:
            self.chain.reset()
            self.keyframe_chain.reset()
            self.evidence.reset()
            self._keyframe_tick = self._keyframe_uid = 0
        self.unavailable(match.reason, locate=match.locate)
        # Pending is emitted only after the tracker's strict acquisition gates.
        self._last_state["visual_confirmation_pending"] = match.status == "pending"
        self._last_state["visual_tracking_status"] = match.status
        self._last_state["visual_confirmation_hits"] = match.pending_hits
        return dict(self._last_state)

    def _correct_visual_chains(self, clock, raw, anchor, *, sampled):
        corrected = raw
        chain_result = None
        self._evidence_result = None
        if not sampled:
            if anchor is not None and self.evidence is not None:
                self.evidence.reset()
            # raw already includes this observation's new absolute anchor.
            if anchor is None and self._last_state.get("position_trusted"):
                corrected = (
                    np.asarray(self.locator.calibration.world_to_map(self._last_state["x"], self._last_state["z"]))
                    * self._scale
                )
            return corrected, chain_result
        gray, alpha = self.odometry.last_ring_patch()
        keyframe_result = None
        if self.chain is not None and gray is not None and alpha is not None:
            self._keyframe_tick += 1
            if self._keyframe_uid == 0 or self._keyframe_tick >= self.keyframe_interval:
                self._keyframe_tick = 0
                keyframe_result = self.keyframe_chain.add(
                    uid=self._keyframe_uid, time=clock, gray=gray, alpha=alpha, initial_pos=raw, anchor=anchor
                )
                self._keyframe_uid += 1
            keyframe_anchor = (
                keyframe_result.position if keyframe_result is not None and keyframe_result.valid else None
            )
            chain_result = self.chain.add(
                time=clock,
                gray=gray,
                alpha=alpha,
                initial_pos=raw,
                anchor=anchor,
                keyframe_anchor=keyframe_anchor,
                keyframe_anchor_weight=self.keyframe_weight,
            )
            if chain_result.valid:
                corrected = chain_result.position
            self._evidence_result = self.evidence.observe(
                time=clock, gray=gray, alpha=alpha, position=corrected, anchor=anchor
            )
            if self._evidence_result.trusted:
                direct_position = self._evidence_result.position
                correction_m = float(np.linalg.norm(direct_position - raw)) / (4 * self._scale)
                if correction_m <= self.chain_options["max_correction_m"]:
                    corrected = direct_position
                else:
                    self._evidence_result.trusted = False
                    self._evidence_result.reason = "correction_too_large"
        return corrected, chain_result

    def _publish_visual_state(self, clock, match, measured, delta, corrected, chain_result, strong):
        absolute = match.position or (match.locate.best if match.locate is not None else None)
        if absolute is None:
            return self._lose_tracking(match)
        x, z = self.locator.calibration.map_to_world(*(corrected / self._scale))
        tier_trusted = absolute.tier_id is not None and (strong or self._anchor_tier_id == absolute.tier_id)
        speed = None
        dt = measured.get("dt", 0)
        if measured.get("sampled") and dt > 0:
            movement = np.asarray(self.map_to_world_px) @ np.asarray(measured.get("dmap_px", (0, 0)))
            speed = float(np.linalg.norm(movement) / dt)
        self._last_state = {
            "x": x,
            "y": None,
            "z": z,
            "map_id": self.map_id,
            "position_trusted": True,
            # A confirmed absolute fix does not need an odometry baseline to be
            # usable. Keep odom_ok truthful for consumers of relative motion.
            "absolute_position_valid": strong,
            "trust_reason": "visual_map" if strong else "visual_anchor_chain",
            "anchor_set": True,
            "sync_supported": False,
            "sync_needed": False,
            "just_synced": False,
            "sync_seq": self._sequence,
            "rest": speed is not None and speed < 0.2,
            "speed": speed,
            "odom_ok": bool(measured.get("ok") and measured.get("sampled")),
            "odom_reason": measured.get("reason"),
            "dmap_px": tuple(delta),
            "frame_t": clock,
            "visual_confidence": absolute.ncc,
            "visual_full_ncc": absolute.full_ncc,
            "visual_margin": match.locate.margin,
            "visual_tracking_reason": match.reason,
            "visual_locate_status": match.locate.status,
            "visual_locate_reason": match.locate.reason,
            "visual_candidate_tier_id": match.locate.best.tier_id,
            "visual_anchor_chain_correction_m": chain_result.correction_m if chain_result else None,
            "visual_anchor_chain_reason": chain_result.reason if chain_result else "disabled",
            "visual_anchor_chain_connected": bool(chain_result and chain_result.connected_to_anchor),
            "visual_absolute_anchor_age_s": clock - self._last_absolute_t,
            "visual_chain_error_budget_m": self._evidence_result.error_m if self._evidence_result else None,
            "visual_chain_evidence_reason": self._evidence_result.reason if self._evidence_result else "unsampled",
            "visual_chain_keyframe_checks": self._evidence_result.keyframe_checks if self._evidence_result else 0,
            "visual_anchor_chain_keyframe_nodes": len(self.keyframe_chain._nodes) if self.keyframe_chain else 0,
            "source": "visual_map",
        }
        self._last_state["tier_margin"] = match.locate.layer_margin
        self._last_state.update(tier_id=absolute.tier_id, tier_trusted=tier_trusted, tier_evidence_required=True)
        if strong and tier_trusted and self._navigation_topology is not None:
            self._remember_topology_anchor(self._last_state)
        return dict(self._last_state)

    def _update_absolute_anchor(self, match, clock):
        absolute = match.position
        if absolute is None:
            return np.asarray(self.odometry.position_px()), None, False
        self.map_id = absolute.map_id or self.map_id
        strong = (
            absolute.ncc >= self.tracker.initial_min_ncc
            and absolute.full_ncc >= self.tracker.initial_min_full_ncc
            and match.locate.margin >= self.tracker.initial_min_margin
            and (absolute.tier_id is not None or match.locate.layer_margin is None)
            and (self._anchor_tier_id is None or absolute.tier_id == self._anchor_tier_id)
        )
        delta = np.asarray(self.odometry.position_px())
        anchor = np.array([absolute.map_x, absolute.map_y]) * self._scale if strong else None
        if anchor is not None:
            if self._origin is None:
                self._sequence += 1
            self._origin = anchor - delta
            self._last_absolute_t = clock
            self._anchor_tier_id = absolute.tier_id
        return delta, anchor, strong
