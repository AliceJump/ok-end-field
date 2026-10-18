"""Zipline initial-alignment adapter; arrival and activation never commit samples."""

import json
from dataclasses import replace

from src.core.learning.models import CaptureProfile, ContextConfidence, RouteContext, yaw_bucket
from src.core.learning.policy import learning_allowed
from src.core.learning.route_migration import migrate_zip_line_cache
from src.tasks.mixin import zip_line_motion_cache
from src.tasks.mixin.navigation_learning import initial_alignment


def route_context_for(task, fingerprint, segment_index, start_yaw, *, need_scroll, family=()):
    if not learning_allowed() or fingerprint is None or start_yaw is None:
        return None
    try:
        config_key, distances = json.loads(fingerprint)
        return RouteContext(
            config_key,
            tuple(distances),
            segment_index,
            CaptureProfile(
                task.width,
                task.height,
                "zipline-scroll-max" if need_scroll else "zipline-scroll-off",
                "zipline-initial-v1",
            ),
            yaw_bucket(start_yaw),
            family,
        )
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def _migrate_once(store):
    if getattr(store, "_zip_line_migration_done", False):
        return
    path = zip_line_motion_cache._DEFAULT_CACHE_PATH
    try:
        if not path.exists():
            store._zip_line_migration_done = True
            return
    except OSError:
        return
    # v4 lacks a camera/scroll profile: import privately, never guess it from today's settings.
    if migrate_zip_line_cache(path, store).committed:
        store._zip_line_migration_done = True


def align_initial_zipline(task, context, distance, *, need_scroll=None):
    with initial_alignment(task, context) as learning:
        if learning is None:
            return task._align_zip_line_distance(distance, need_scroll=need_scroll, tolerance=50, initial_move=None)
        _migrate_once(learning.session.store)
        lookup_context = context
        hint = learning.session.hint()
        if hint is None or hint.confidence == ContextConfidence.LOW:
            nearest = learning.session.nearest_yaw()
            if nearest is not None:
                actual_yaw, ready = task._normalize_zip_line_segment_yaw(nearest.yaw)
                if ready:
                    lookup_context = replace(context, start_yaw_bucket=yaw_bucket(actual_yaw))
                else:
                    learning.disturb("yaw_calibration_failed")
        hint = learning.session.hint(lookup_context)
        start_reading = task._read_zip_line_arrow_angle() if hint is not None and hint.action is not None else None
        action = learning.replay(lookup_context)
        if action is not None:
            if action[0]:
                if not task._wait_zip_line_prefeed_settled(start_reading, action[0]):
                    learning.disturb("unsettled_replay")
            else:
                task.sleep(0.08)
        result = task._align_zip_line_distance(
            distance,
            need_scroll=False,
            tolerance=50,
            initial_move=None,
            learning_session=learning,
        )
        if not result:
            learning.fail()
        return result
