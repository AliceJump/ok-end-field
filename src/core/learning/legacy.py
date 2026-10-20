"""Old aggregates replay raw counts unchanged, only while new records warm up."""

import math
from dataclasses import replace

from src.core.learning.models import ContextConfidence, LearningHint
from src.core.learning.policy import choose_hint, learning_allowed


def legacy_hint(records, confidence, *, exact=()):
    """Do not invent settings, raw acquisitions or variance for old estimates."""
    confidence = ContextConfidence(confidence)
    if not learning_allowed() or confidence not in (ContextConfidence.HIGH, ContextConfidence.MEDIUM) or not records:
        return None
    if any(record.legacy_retired for record in records):
        return None
    if max(records, key=lambda record: record.updated_at).consecutive_failures:
        return None
    exact_ids = {id(record) for record in exact}
    for record in sorted(records, key=lambda record: (id(record) in exact_ids, record.updated_at), reverse=True):
        hint = _aggregate_hint(record, confidence)
        if hint is not None:
            return hint
    return None


def _aggregate_hint(record, confidence):
    motion = record.legacy_motion if record.observations else record.motion
    if motion is None or not motion.samples or record.consecutive_failures:
        return None
    if motion.variance_known:
        hint = choose_hint(replace(record, motion=motion), confidence, source="legacy-raw")
        return hint if ready_hint(hint) else None
    # A migrated v4 mean may be EMA; preserve its unknown variance label.
    if motion.samples < 3:
        return None
    factor = 1.0 if confidence == ContextConfidence.HIGH else 0.5
    action = round(motion.mean_dx * factor), round(motion.mean_dy * factor)
    if math.hypot(*action) > 6000:
        return None
    return LearningHint(action if any(action) else None, record.target, confidence, "legacy-raw", motion.samples)


def ready_hint(hint):
    # A verified zero action also retires old nonzero movement.
    return hint is not None and hint.confidence in (ContextConfidence.HIGH, ContextConfidence.MEDIUM)
