"""Learning restrictions never select or change the realtime movement profile."""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from src.core.learning.models import ContextConfidence, LearningHint

_DENIALS = ContextVar("learning_denials", default=())
_EPOCH = ContextVar("learning_epoch", default=0)


def learning_allowed():
    return not _DENIALS.get()


def learning_epoch():
    return _EPOCH.get()


@contextmanager
def deny_learning(reason):
    """Ordinary nested entries cannot undo a deny; restore on every exit."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A learning denial needs a reason")
    _EPOCH.set(_EPOCH.get() + 1)
    token = _DENIALS.set((*_DENIALS.get(), reason))
    try:
        yield
    finally:
        _DENIALS.reset(token)


@contextmanager
def learning_after_verified_transfer(*, verified):
    """A verified world reset may replace post-battle state, never an active battle."""
    denials = _DENIALS.get()
    if verified is not True or any(reason not in {"post_battle", "post_battle_retry"} for reason in denials):
        yield False
        return
    _EPOCH.set(_EPOCH.get() + 1)
    token = _DENIALS.set(())
    try:
        yield True
    finally:
        # Neither pre-transfer evidence nor a leaked reset session can be reused.
        _EPOCH.set(_EPOCH.get() + 1)
        _DENIALS.reset(token)


def without_learning(reason):
    def decorate(method):
        @wraps(method)
        def wrapped(*args, **kwargs):
            with deny_learning(reason):
                return method(*args, **kwargs)

        return wrapped

    return decorate


def choose_hint(record, confidence, *, source="private", min_samples=3, max_motion=6000):
    """Use sample reliability to cap context confidence, independent of detection."""
    confidence = ContextConfidence(confidence)
    if not learning_allowed() or confidence == ContextConfidence.INVALID or record is None:
        return None
    motion = record.motion
    variance = motion.variance
    stable = (
        motion.samples >= min_samples
        and variance is not None
        and record.success_count >= motion.samples
        and math.sqrt(sum(variance)) <= max(12.0, math.hypot(motion.mean_dx, motion.mean_dy) * 0.25)
        and record.consecutive_failures == 0
    )
    if not stable:
        confidence = ContextConfidence.LOW
    action = None
    if confidence in (ContextConfidence.HIGH, ContextConfidence.MEDIUM):
        factor = 1.0 if confidence == ContextConfidence.HIGH else 0.5
        dx, dy = round(motion.mean_dx * factor), round(motion.mean_dy * factor)
        if math.hypot(dx, dy) <= max_motion and (dx or dy):
            action = dx, dy
        elif dx or dy:
            confidence = ContextConfidence.LOW
    return LearningHint(action, record.target, confidence, source, motion.samples)
