"""Initial alignment learns from its own fresh verification, before navigation begins."""

from dataclasses import dataclass, field, replace

from src.core.learning.environment import LearningEnvironment, environment_for_context
from src.core.learning.legacy import ready_hint
from src.core.learning.lookup import workflow_hint, workflow_record
from src.core.learning.models import (
    ContextConfidence,
    LearningHint,
    RouteContext,
    TargetPrior,
    VerificationEvidence,
    WorkflowContext,
    _finite,
)
from src.core.learning.policy import choose_hint, learning_allowed, learning_epoch
from src.core.learning.routes import RouteLearning
from src.core.learning.samples import MouseInput


@dataclass
class LearningSession:
    context: WorkflowContext | RouteContext
    store: object
    started_at: float
    confidence: ContextConfidence = ContextConfidence.HIGH
    dx: float = 0.0
    dy: float = 0.0
    replay_used: bool = False
    contaminated: bool = False
    closed: bool = False
    reasons: list[str] = field(default_factory=list)
    environment: LearningEnvironment | None = None
    inputs: list[MouseInput] = field(default_factory=list)
    verification: str = "aligned"
    _last_action_at: float = field(init=False)
    _epoch: int = field(init=False)
    _last_action_frame: int = -1
    _action_attempted: bool = False
    _prior_context: object = field(default=None, init=False)
    _prior_source: str | None = field(default=None, init=False)
    _target_hint: LearningHint | None = field(default=None, init=False)

    def __post_init__(self):
        if not isinstance(self.context, (WorkflowContext, RouteContext)):
            raise ValueError("Alignment learning requires a semantic context")
        _finite(self.started_at)
        self.confidence = ContextConfidence(self.confidence)
        self._last_action_at = self.started_at
        self._epoch = learning_epoch()

    def _allowed(self):
        if self.closed or not self.context.allows_learning or not learning_allowed() or self._epoch != learning_epoch():
            self.confidence = ContextConfidence.INVALID
            return False
        return self.confidence != ContextConfidence.INVALID

    def hint(self, lookup_context=None):
        if not self._allowed() or self.replay_used:
            return None
        if isinstance(self.context, RouteContext):
            context = lookup_context or self.context
            if (
                not isinstance(context, RouteContext)
                or replace(context, start_yaw_bucket=self.context.start_yaw_bucket) != self.context
            ):
                raise ValueError("Only the verified yaw may change during route lookup")
            return RouteLearning(self.store).lookup(context, self.confidence, environment=self.environment)
        if lookup_context is not None:
            raise ValueError("Workflow alignment cannot borrow another context")
        if self.environment is not None:
            return workflow_hint(self.context, self.store.snapshot(), self.environment, self.confidence)
        return choose_hint(self.store.get(self.context.fingerprint), self.confidence)

    def take_action_prior(self, lookup_context=None):
        if self.replay_used:
            return None
        hint = self.hint(lookup_context)
        self._target_hint = hint
        # Claim before returning, so retries cannot accidentally replay twice.
        self.replay_used = True
        action = hint.action if hint else None
        if action is not None:
            axes = self.context.capture_profile.axes
            action = (0 if axes == "y" else action[0], 0 if axes == "x" else action[1])
            if not any(action):
                action = None
        self._action_attempted = action is not None
        self._prior_context = lookup_context or self.context
        self._prior_source = hint.source if hint is not None else None
        return action

    def target_hint(self):
        """The claimed hint remains usable for detection, subject to the same epoch/deny."""
        if not self._allowed() or self.contaminated:
            return None
        return self._target_hint if self.replay_used else self.hint()

    def nearest_yaw(self):
        if not self._allowed() or self.replay_used or not isinstance(self.context, RouteContext):
            return None
        return RouteLearning(self.store).nearest_yaw(self.context, self.confidence, environment=self.environment)

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
        self.inputs.append(MouseInput(dx, dy, observed_at - self.started_at, after_frame_id))
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
        if (
            not allowed
            or self.contaminated
            or self.confidence not in (ContextConfidence.HIGH, ContextConfidence.MEDIUM)
            or not isinstance(evidence, VerificationEvidence)
        ):
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
        if isinstance(self.context, RouteContext):
            return RouteLearning(self.store).record_success(
                self.context,
                self.dx,
                self.dy,
                target=target,
                now=now,
                environment=self.environment,
                inputs=tuple(self.inputs),
                verification=self.verification,
            )

        return self.store.update(
            self.context.fingerprint, lambda record: self._successful_workflow_record(record, now=now, target=target)
        )

    def _successful_workflow_record(self, record, *, now, target):
        updated = record.successful(
            self.dx,
            self.dy,
            target=target,
            now=now,
            provenance=(self.context.task, self.context.phase, "aligned"),
            environment=self.environment or environment_for_context(self.context),
            inputs=tuple(self.inputs),
            verification=self.verification,
        )
        if self.environment is None:
            return updated
        records = self.store.snapshot()
        if not isinstance(records, dict):
            return updated
        records[self.context.fingerprint] = updated
        hint = choose_hint(workflow_record(self.context, records, self.environment), ContextConfidence.HIGH)
        return replace(updated, legacy_retired=True) if ready_hint(hint) else updated

    def fail(self, *, now):
        allowed = self._allowed()
        self.closed = True
        if not allowed or self.contaminated or not self._action_attempted:
            return False
        context = self._prior_context or self.context
        if isinstance(context, RouteContext) and self._prior_source == "shared-prefix":
            return RouteLearning(self.store).reject_shared(context, now=now)
        return self.store.update(context.fingerprint, lambda record: record.failed(now=now))

    def close(self):
        self.closed = True


def create_learning_session(context, store, *, started_at, confidence=ContextConfidence.HIGH, environment=None):
    """Hard-invalid entries must not create a session or context record."""
    if not learning_allowed():
        return None
    if not isinstance(context, (WorkflowContext, RouteContext)):
        raise ValueError("Alignment learning requires a semantic context")
    if not context.allows_learning or ContextConfidence(confidence) == ContextConfidence.INVALID:
        return None
    return LearningSession(context, store, started_at, confidence, environment=environment)
