"""Project complete records to current settings without persisting converted input."""

import json
from dataclasses import replace

from src.core.learning.legacy import legacy_hint, ready_hint
from src.core.learning.models import CONTEXT_VERSION, CaptureProfile, LearningRecord, MotionStatistics, WorkflowContext
from src.core.learning.policy import choose_hint
from src.core.learning.samples import classify_samples


def matching_profile(left, right):
    return replace(left, width=right.width, height=right.height) == right


def projected_record(record, environment):
    if not record.observations:
        # Aggregates lack per-acquisition settings and cannot be converted safely.
        return replace(record, motion=MotionStatistics()) if record.failure_count else None
    return replace(record, motion=record.motion_for(environment))


def combine_records(left, right, environment):
    if left is None:
        return right
    combined = LearningRecord(
        target=(left if left.updated_at >= right.updated_at else right).target,
        observations=classify_samples((*left.observations, *right.observations)),
        success_count=left.success_count + right.success_count,
        failure_count=left.failure_count + right.failure_count,
        consecutive_failures=(left if left.updated_at >= right.updated_at else right).consecutive_failures,
        updated_at=max(left.updated_at, right.updated_at),
        provenance=tuple(dict.fromkeys((*left.provenance, *right.provenance)))[-64:],
        legacy_retired=left.legacy_retired or right.legacy_retired,
    )
    return replace(combined, motion=combined.motion_for(environment))


def _workflow_sources(context, records):
    if not isinstance(records, dict):
        return
    for key, record in records.items():
        try:
            values = json.loads(key)
            if values.pop("kind") != "workflow" or values.pop("context_version") != CONTEXT_VERSION:
                continue
            values["capture_profile"] = CaptureProfile(**values["capture_profile"])
            values["entry_route_signature"] = tuple(values["entry_route_signature"])
            source = WorkflowContext(**values)
            if replace(source, capture_profile=context.capture_profile) != context or not matching_profile(
                source.capture_profile, context.capture_profile
            ):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        yield source, record


def workflow_record(context, records, environment):
    result = None
    for _, record in _workflow_sources(context, records):
        converted = projected_record(record, environment)
        if converted is not None:
            result = combine_records(result, converted, environment)
    return result


def workflow_hint(context, records, environment, confidence):
    hint = choose_hint(workflow_record(context, records, environment), confidence)
    if ready_hint(hint):
        return hint
    pairs = list(_workflow_sources(context, records))
    exact = [record for source, record in pairs if source.capture_profile == context.capture_profile]
    return legacy_hint([record for _, record in pairs], confidence, exact=exact) or hint
