"""Bind initial alignment to accepted input and fresh frames; never wrap navigation."""

import time
from contextlib import contextmanager

from ok import TaskDisabledException

from src.core.learning.models import ContextConfidence, TargetPrior, VerificationEvidence
from src.core.learning.policy import learning_allowed
from src.core.learning.session import create_learning_session
from src.core.learning.store import LearningStore


def get_learning_store(task):
    """Lazy task-local handle; store instances coordinate through the common file lock."""
    if not learning_allowed():
        return None
    store = vars(task).get("_context_learning_store")
    if store is None:
        store = LearningStore()
        task._context_learning_store = store
    return store


class InitialAlignment:
    """A short-lived input observer whose only commit is a fresh centered detection."""

    def __init__(self, task, session):
        self.task = task
        self.session = session
        self.frame_id = 0
        self.observed_at = session.started_at
        self._has_frame = False

    def frame_acquired(self, frame):
        profile = self.session.context.capture_profile
        if (profile.width, profile.height) != (self.task.width, self.task.height):
            self.disturb("capture_changed")
        if frame is None:
            self.disturb("missing_frame")
            return
        self.frame_id += 1
        self.observed_at = self.task.active_time()
        self._has_frame = True

    def disturb(self, reason):
        if reason == "random_search" and not self.session.contaminated:
            # Recognition-driven recovery invalidates the attempted prior before
            # recovery motion contaminates the original-entry sample.
            self.fail()
        self.session.disturb(reason)

    def replay(self, lookup_context=None):
        if self.session.contaminated:
            return None
        action = self.session.take_action_prior(lookup_context)
        if action is None:
            return None
        dx, dy = action
        try:
            sent = self.task.active_and_send_mouse_delta(dx, dy, activate=True, steps=5, delay=0.005)
        except TaskDisabledException:
            raise
        except Exception as error:
            self.task.log_info(f"[initial-align-learning] replay failed: {error}")
            return None
        self.task.log_info(
            f"[initial-align-learning] replay target={self.session.context.target_id} move={action} accepted={bool(sent)}"
        )
        return action if sent else None

    def confirm_alignment(self, center):
        if not self._has_frame:
            return False
        width, height = self.task.width, self.task.height
        position = center[0] / width, center[1] / height
        target = TargetPrior(aligned_position=position) if all(0 <= value <= 1 for value in position) else None
        saved = self.session.confirm_alignment(
            VerificationEvidence(self.session.context.target_id, "aligned", self.observed_at, self.frame_id),
            now=time.time(),
            target=target,
        )
        self.task.log_info(
            f"[initial-align-learning] verified target={self.session.context.target_id} saved={saved} move=({self.session.dx},{self.session.dy}) reasons={self.session.reasons}"
        )
        return saved

    def fail(self):
        saved = self.session.fail(now=time.time())
        if saved:
            self.task.log_info(f"[initial-align-learning] replay_failed target={self.session.context.target_id}")
        return saved


@contextmanager
def initial_alignment(task, context, *, confidence=ContextConfidence.HIGH):
    """Restore the exact instance sender on success, stop or any partial-input exception."""
    if (
        context is None
        or not learning_allowed()
        or not context.allows_learning
        or ContextConfidence(confidence) == ContextConfidence.INVALID
    ):
        yield None
        return
    if (context.capture_profile.width, context.capture_profile.height) != (task.width, task.height):
        yield None
        return
    parent = vars(task).get("_initial_alignment_scope")
    if parent is not None:
        parent.disturb("nested_alignment")
        yield None
        return
    store = get_learning_store(task)
    session = create_learning_session(context, store, started_at=task.active_time(), confidence=confidence)
    if session is None:
        yield None
        return
    learning = InitialAlignment(task, session)
    name = "active_and_send_mouse_delta"
    original = getattr(task, name)
    had_override = name in vars(task)
    disturbances = {}
    for method_name in (
        "do_scroll",
        "move_keys",
        "rotate_search",
        "strafe_search",
        "send_key",
        "send_key_down",
        "press_key",
        "click",
        "click_with_alt",
    ):
        method = getattr(task, method_name, None)
        if not callable(method):
            continue
        disturbances[method_name] = (method, method_name in vars(task))

        def disturb_and_call(*args, _method=method, _name=method_name, **kwargs):
            session.disturb(
                _name,
                position_changed=_name in ("move_keys", "strafe_search", "send_key", "send_key_down", "press_key"),
            )
            return _method(*args, **kwargs)

        setattr(task, method_name, disturb_and_call)

    def send(*args, **kwargs):
        try:
            accepted = original(*args, **kwargs)
        except BaseException:
            session.disturb("partial_input")
            raise
        only_activate = kwargs.get("only_activate", args[3] if len(args) > 3 else False)
        if not only_activate:
            session.observe_input(
                kwargs.get("dx", args[0] if args else 1),
                kwargs.get("dy", args[1] if len(args) > 1 else 1),
                accepted=bool(accepted),
                observed_at=task.active_time(),
                after_frame_id=learning.frame_id,
            )
            learning._has_frame = False
        return accepted

    setattr(task, name, send)
    task._initial_alignment_scope = learning
    try:
        yield learning
    except TaskDisabledException:
        raise
    except Exception:
        learning.fail()
        raise
    finally:
        session.close()
        if had_override:
            setattr(task, name, original)
        else:
            delattr(task, name)
        delattr(task, "_initial_alignment_scope")
        for method_name, (method, had_override) in disturbances.items():
            if had_override:
                setattr(task, method_name, method)
            else:
                delattr(task, method_name)


def align_initial_target(task, context, target, *, confidence=ContextConfidence.HIGH, **kwargs):
    """Replay before detection, then delegate success evidence to the ordinary align loop."""
    axes = "y" if kwargs.get("only_y") else "x" if kwargs.get("only_x") else "xy"
    if context is not None and (
        context.capture_profile.axes != axes
        or kwargs.get("need_scroll")
        or (kwargs.get("only_x") and kwargs.get("only_y"))
        or kwargs.get("max_time", 50) <= 0
        or kwargs.get("max_iterations", 1) == 0
        or (kwargs.get("deadline") is not None and task.active_time() >= kwargs["deadline"])
    ):
        context = None
    with initial_alignment(task, context, confidence=confidence) as learning:
        if learning is None:
            return task.align_ocr_or_find_target_to_center(target, **kwargs)
        learning.replay()
        task.sleep(0.08)
        result = task.align_ocr_or_find_target_to_center(target, learning_session=learning, **kwargs)
        if not result:
            learning.fail()
        return result
