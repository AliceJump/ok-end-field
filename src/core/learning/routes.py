"""Route priors share verified prefixes while retaining independent source histories.

This layer performs no detection, movement, yaw calibration or success inference.
Callers may record only a clean, freshly verified initial alignment. A nearest-yaw
result is a calibration candidate, never permission to replay at the current yaw.
"""

import json
import math
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import combinations

from src.core.learning.environment import environment_for_context
from src.core.learning.legacy import legacy_hint, ready_hint
from src.core.learning.lookup import combine_records, matching_profile, projected_record
from src.core.learning.models import (
    CONTEXT_VERSION,
    CaptureProfile,
    ContextConfidence,
    LearningHint,
    LearningRecord,
    MotionStatistics,
    RouteContext,
)
from src.core.learning.policy import choose_hint, learning_allowed


def route_context_from_key(key):
    """Decode only complete, canonical route identities belonging to this version."""
    try:
        values = json.loads(key)
        if values.pop("kind") != "route_private" or values.pop("context_version") != CONTEXT_VERSION:
            return None
        values["capture_profile"] = CaptureProfile(**values["capture_profile"])
        values["distances"] = tuple(values["distances"])
        values["family"] = tuple(values["family"])
        context = RouteContext(**values)
        return context if context.fingerprint == key else None
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return None


def _route_identity(context):
    # One configured route owns its segments, yaw buckets and capture profiles.
    return context.config_key, context.distances


def trim_routes(records, *, max_routes=128):
    """Bound source routes together, preserving unrelated workflow records."""
    identities = {}
    contexts = {}
    for key, record in records.items():
        context = route_context_from_key(key)
        if context is not None:
            identity = _route_identity(context)
            contexts[key] = identity
            identities[identity] = max(identities.get(identity, 0), record.updated_at)
    keep = set(sorted(identities, key=lambda item: (identities[item], item), reverse=True)[:max_routes])
    return {key: record for key, record in records.items() if key not in contexts or contexts[key] in keep}


def _conflict_marker(context):
    shared = context.shared_fingerprint
    if shared:
        values = json.loads(shared)
        values["capture_profile"].pop("width")
        values["capture_profile"].pop("height")
        shared = json.dumps(values, sort_keys=True, ensure_ascii=False)
    return "reject-shared:" + sha256(shared.encode("utf-8")).hexdigest() if shared else None


def _conflicts(left, right):
    """Separate source means before pooling; never average opposing turns to zero."""
    a, b = left.motion, right.motion
    if not a.samples or not b.samples:
        return False
    distance = math.hypot(a.mean_dx - b.mean_dx, a.mean_dy - b.mean_dy)
    scale = max(math.hypot(a.mean_dx, a.mean_dy), math.hypot(b.mean_dx, b.mean_dy))
    # Conservative initial policy; physical calibration remains an integration task.
    return distance > max(48.0, scale * 0.5)


def _blocked(context, record):
    return record is not None and (record.consecutive_failures > 0 or _conflict_marker(context) in record.provenance)


@dataclass(frozen=True)
class RouteYawCandidate:
    yaw: float
    hint: LearningHint


class RouteLearning:
    """Private exact -> shared exact -> nearest yaw candidate -> visual fallback.

    Source records are the only persisted statistics. Shared pools are derived
    from them, so importing or querying cannot double count a source. New verified
    samples replace an unknown-variance legacy estimate for that source.
    """

    def __init__(self, store, *, max_routes=128, max_sources_per_prefix=32):
        for limit in (max_routes, max_sources_per_prefix):
            if type(limit) is not int or limit <= 0:
                raise ValueError("Route limits must be positive integers")
        self.store = store
        self.max_routes = max_routes
        self.max_sources_per_prefix = max_sources_per_prefix

    @staticmethod
    def _project_records(context, records, environment):
        if environment is None:
            return records
        result = {}
        for key, record in records.items():
            source = route_context_from_key(key)
            if source is None or not matching_profile(source.capture_profile, context.capture_profile):
                continue
            projected = replace(source, capture_profile=context.capture_profile)
            converted = projected_record(record, environment)
            if converted is not None:
                result[projected.fingerprint] = combine_records(
                    result.get(projected.fingerprint), converted, environment
                )
        return result

    @staticmethod
    def _source_index(records):
        index = {}
        for key, record in records.items():
            source = route_context_from_key(key)
            if (
                source is not None
                and source.shared_fingerprint
                and record.motion.samples
                and not _blocked(source, record)
            ):
                index.setdefault(source.shared_fingerprint, []).append((key, record))
        return index

    def _shared(self, context, records, *, exclude=None, index=None):
        shared = context.shared_fingerprint
        if shared is None:
            return None
        if index is None:
            index = self._source_index(records)
        sources = [(key, record) for key, record in index.get(shared, ()) if key != exclude]
        sources.sort(key=lambda item: (item[1].updated_at, item[0]), reverse=True)
        sources = [record for _, record in sources[: self.max_sources_per_prefix]]
        if not sources:
            return None
        if any(_conflicts(left, right) for left, right in combinations(sources, 2)):
            return None
        # Unknown legacy variance must not poison (or strengthen) a clean new pool.
        verified = [record for record in sources if record.motion.variance_known]
        pool = verified or sources
        motion = MotionStatistics()
        try:
            for record in pool:
                motion = motion.merge(record.motion)
        except (OverflowError, ValueError):
            return None
        return LearningRecord(
            motion=motion,
            success_count=sum(record.success_count for record in pool),
            updated_at=max(record.updated_at for record in pool),
            provenance=("shared-prefix", f"sources:{len(pool)}"),
        )

    def _lookup(self, context, confidence, records, *, index=None, allow_shared=True):
        private = records.get(context.fingerprint)
        hint = choose_hint(private, confidence, source="private")
        if hint is not None and hint.action is not None:
            return hint
        if not allow_shared or _blocked(context, private):
            return hint
        shared = self._shared(context, records, index=index)
        if private is not None and shared is not None and _conflicts(private, shared):
            return hint
        shared_hint = choose_hint(shared, confidence, source="shared-prefix")
        return shared_hint if shared_hint is not None and shared_hint.action is not None else hint or shared_hint

    def lookup(self, context, confidence=ContextConfidence.HIGH, *, environment=None):
        """No target, ROI, detector or current visibility is required to obtain an action."""
        if (
            not learning_allowed()
            or ContextConfidence(confidence) == ContextConfidence.INVALID
            or context.start_yaw_bucket is None
        ):
            return None
        raw_records = self.store.snapshot()
        records = self._project_records(context, raw_records, environment)
        hint = self._lookup(context, confidence, records)
        if environment is not None and not ready_hint(hint):
            pairs = self._legacy_sources(context, raw_records)
            exact = [
                record
                for source, record in pairs
                if (source.capture_profile.width, source.capture_profile.height)
                == (context.capture_profile.width, context.capture_profile.height)
            ]
            hint = legacy_hint([record for _, record in pairs], confidence, exact=exact) or hint
        return self._restrict_axes(hint, context)

    @staticmethod
    def _legacy_sources(context, records):
        """Only this exact route/segment/yaw; old unknown families never gain sharing."""
        sources = []
        for key, record in records.items():
            source = route_context_from_key(key)
            if source is None or (
                source.config_key,
                source.distances,
                source.segment_index,
                source.start_yaw_bucket,
            ) != (context.config_key, context.distances, context.segment_index, context.start_yaw_bucket):
                continue
            left, right = source.capture_profile, context.capture_profile
            compatible = matching_profile(left, right)
            if right.movement_version == "zipline-initial-v2-records":
                compatible = (
                    compatible
                    or (left.movement_version == "zipline-initial-v1" and left.camera == right.camera)
                    or (
                        left.movement_version == "zipline-v4"
                        and left.camera == "legacy-unknown"
                        and right.camera == "zipline-scroll-off"
                    )
                )
            if compatible and left.axes == right.axes:
                sources.append((source, record))
        return sources

    @staticmethod
    def _restrict_axes(hint, context):
        if hint is None or hint.action is None:
            return hint
        dx, dy = hint.action
        axes = context.capture_profile.axes
        action = (dx if "x" in axes else 0, dy if "y" in axes else 0)
        return replace(hint, action=action if any(action) else None)

    def nearest_yaw(self, context, confidence=ContextConfidence.HIGH, *, environment=None):
        """Return a candidate only; after turning, call lookup with the *actual* bucket."""
        if (
            not learning_allowed()
            or ContextConfidence(confidence) not in (ContextConfidence.HIGH, ContextConfidence.MEDIUM)
            or context.start_yaw_bucket is None
        ):
            return None
        records = self._project_records(context, self.store.snapshot(), environment)
        index = self._source_index(records)
        # A rejection survives private warmup; do not bypass it through another yaw.
        allow_shared = True
        for key, record in records.items():
            source = route_context_from_key(key)
            if (
                source is not None
                and _route_identity(source) == _route_identity(context)
                and source.family == context.family
                and source.route_prefix == context.route_prefix
                and source.capture_profile == context.capture_profile
                and _blocked(source, record)
            ):
                allow_shared = False
                break
        candidates = []
        for bucket in range(72):
            if bucket == context.start_yaw_bucket:
                continue
            candidate = replace(context, start_yaw_bucket=bucket)
            hint = self._restrict_axes(
                self._lookup(candidate, confidence, records, index=index, allow_shared=allow_shared), context
            )
            if hint is None or hint.action is None:
                continue
            distance = abs((bucket - context.start_yaw_bucket + 36) % 72 - 36)
            candidates.append((distance, 0 if hint.source == "shared-prefix" else 1, -hint.samples, bucket, hint))
        if not candidates:
            return None
        _, _, _, bucket, hint = min(candidates, key=lambda item: item[:4])
        return RouteYawCandidate(bucket * 5.0, hint)

    def record_success(self, context, dx, dy, *, now, target=None, environment=None, inputs=(), verification="aligned"):
        """Persist a caller-verified clean initial alignment, never a nav outcome."""
        if (
            not learning_allowed()
            or context.start_yaw_bucket is None
            or ("x" not in context.capture_profile.axes and dx != 0)
            or ("y" not in context.capture_profile.axes and dy != 0)
        ):
            return False

        def update(records):
            previous = records.get(context.fingerprint, LearningRecord())
            updated = previous.successful(
                dx,
                dy,
                target=target,
                now=now,
                provenance=("initial-alignment",),
                environment=environment or environment_for_context(context),
                inputs=inputs,
                verification=verification,
            )
            shared = self._shared(
                context, self._project_records(context, records, environment), exclude=context.fingerprint
            )
            if shared is not None and _conflicts(updated, shared):
                updated = replace(
                    updated,
                    provenance=tuple(dict.fromkeys((*updated.provenance, _conflict_marker(context))))[-64:],
                )
            records[context.fingerprint] = updated
            if environment is not None:
                projected = self._project_records(context, records, environment)
                if ready_hint(self._lookup(context, ContextConfidence.HIGH, projected)):
                    records[context.fingerprint] = replace(updated, legacy_retired=True)
            return trim_routes(records, max_routes=self.max_routes)

        return self.store.update_many(update)

    def reject_shared(self, context, *, now):
        """An invalidated initial replay collects private data without retrying that shared model."""
        if not learning_allowed():
            return False

        def update(records):
            record = records.get(context.fingerprint, LearningRecord()).failed(now=now)
            marker = _conflict_marker(context)
            if marker is not None:
                record = replace(record, provenance=tuple(dict.fromkeys((*record.provenance, marker)))[-64:])
            records[context.fingerprint] = record
            return trim_routes(records, max_routes=self.max_routes)

        return self.store.update_many(update)
