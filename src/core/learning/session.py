"""Initial alignment learns from its own fresh verification, before navigation begins."""

from dataclasses import dataclass, field

from src.core.learning.models import ContextConfidence, TargetPrior, VerificationEvidence, WorkflowContext, _finite
from src.core.learning.policy import choose_hint, learning_allowed, learning_epoch


@dataclass
class LearningSession:
    context: WorkflowContext
    store: object
    started_at: float
    confidence: ContextConfidence = ContextConfidence.HIGH
    dx: float = 0.0
    dy: float = 0.0
    replay_used: bool = False
    contaminated: bool = False
    closed: bool = False
    reasons: list[str] = field(default_factory=list)
    _last_action_at: float = field(init=False)
    _epoch: int = field(init=False)
    _last_action_frame: int = -1
    _action_attempted: bool = False

    def __post_init__(self):
        if not isinstance(self.context, WorkflowContext):
            raise ValueError("Workflow learning requires a semantic WorkflowContext")
        _finite(self.started_at)
        self.confidence = ContextConfidence(self.confidence)
        self._last_action_at = self.started_at
        self._epoch = learning_epoch()

    def _allowed(self):
        if self.closed or not self.context.allows_learning or not learning_allowed() or self._epoch != learning_epoch():
            self.confidence = ContextConfidence.INVALID
            return False
        return self.confidence != ContextConfidence.INVALID

    def hint(self):
        if not self._allowed() or self.replay_used:
            return None
        return choose_hint(self.store.get(self.context.fingerprint), self.confidence)

    def take_action_prior(self):
        if self.replay_used:
            return None
        hint = self.hint()
        # Claim before returning, so retries cannot accidentally replay twice.
        self.replay_used = True
        action = hint.action if hint else None
        if action is not None:
            axes = self.context.capture_profile.axes
            action = (0 if axes == "y" else action[0], 0 if axes == "x" else action[1])
        self._action_attempted = action is not None
        return action

    def observe_input(self, dx, dy, *, accepted, observed_at, after_frame_id):
        if not self._allowed():
            return
        _finite(dx, dy, observed_at)
        axes = self.context.capture_profile.axes
        if (axes == "x" and dy) or (axes == "y" and dx):
            self.disturb("unexpected_axis_input")
            return
        if type(after_frame_id) is not int or after_frame_id < 0:
            raise ValueError("Input needs its preceding frame identity")
        if not accepted or observed_at < self._last_action_at or after_frame_id < self._last_action_frame:
            self.disturb("incomplete_input")
            return
        self.dx += dx
        self.dy += dy
        self._last_action_at = observed_at
        self._last_action_frame = after_frame_id

    def disturb(self, reason, *, position_changed=False):
        self.reasons.append(reason)
        self.contaminated = True
        levels = list(ContextConfidence)
        downgrade = ContextConfidence.LOW if position_changed else ContextConfidence.MEDIUM
        self.confidence = levels[max(levels.index(self.confidence), levels.index(downgrade))]

    def confirm_alignment(self, evidence, *, now, target=None):
        """Finish this sample at a fresh centered detection; arrival is a separate concern."""
        allowed = self._allowed()
        self.closed = True
        if not allowed or self.contaminated or not isinstance(evidence, VerificationEvidence):
            return False
        if (
            evidence.target_id != self.context.target_id
            or evidence.kind != "aligned"
            or evidence.observed_at < self._last_action_at
        ):
            return False
        if evidence.frame_id <= self._last_action_frame:
            return False
        if target is not None and not isinstance(target, TargetPrior):
            return False
        return self.store.update(
            self.context.fingerprint,
            lambda record: record.successful(
                self.dx,
                self.dy,
                target=target,
                now=now,
                provenance=(self.context.task, self.context.phase, evidence.kind),
            ),
        )

    def fail(self, *, now):
        allowed = self._allowed()
        self.closed = True
        if not allowed or not self._action_attempted:
            return False
        return self.store.update(self.context.fingerprint, lambda record: record.failed(now=now))

    def close(self):
        self.closed = True


def create_learning_session(context, store, *, started_at, confidence=ContextConfidence.HIGH):
    """Hard-invalid entries must not create a session or context record."""
    if not learning_allowed():
        return None
    if not isinstance(context, WorkflowContext):
        raise ValueError("Workflow learning requires a semantic WorkflowContext")
    if not context.allows_learning or ContextConfidence(confidence) == ContextConfidence.INVALID:
        return None
    return LearningSession(context, store, started_at, confidence)
