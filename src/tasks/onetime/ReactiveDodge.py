"""Opt-in MaaEnd visual dodge; synchronous, battle-local and fail-closed.

Decision adapted from MaaEnd (AGPL-3.0); see assets/models/maaend/README.md.
"""

from contextlib import suppress
from pathlib import Path

from src.core.BattleConfig import KEY_REACTIVE_DODGE, KEY_REACTIVE_DODGE_COMPAT

REACTIVE_DODGE_COOLDOWN = 0.6


class MaaEndDodgeDetector:
    """One inference, class-local NMS, and errors propagated to the caller."""

    def __init__(self):
        from src.yolo.openvino_detector import OpenVinoYolo8Detect

        path = Path(__file__).resolve().parents[3] / "assets/models/maaend/autofightv13.onnx"
        self.detector = OpenVinoYolo8Detect(weights=str(path), labels={12: "EnemyAttackGroundDodge", 14: "EnemyDodge"})
        if list(self.detector.input_layer.shape) != [1, 3, 640, 640]:
            self.release()
            raise ValueError("Unexpected MaaEnd model input shape")
        if list(self.detector.output_layer.shape) != [1, 26, 8400]:
            self.release()
            raise ValueError("Unexpected MaaEnd model output shape")

    def detect(self, frame, compatibility=True):
        detector = self.detector
        data, padding = detector._preprocess(frame)
        output = detector.compiled_model({detector.input_layer: data})[detector.output_layer]
        height, width = frame.shape[:2]
        # _postprocess mutates coordinates, so each class needs its own copy.
        ground = detector._postprocess(output.copy(), padding, (height, width), 0.7, 12)
        dodge_output = output.copy()
        if compatibility:
            # Discard compatibility-region warnings BEFORE NMS so an ignored
            # high-score box cannot suppress a nearby warning outside it.
            gain = min(detector.input_height / height, detector.input_width / width)
            cx = (dodge_output[0, 0] - padding[1]) * (1280 / (gain * width))
            cy = (dodge_output[0, 1] - padding[0]) * (720 / (gain * height))
            ignored = (cx >= 500) & (cx < 780) & (cy >= 265) & (cy < 590)
            dodge_output[0, 4 + 14, ignored] = 0
        return ground + detector._postprocess(dodge_output, padding, (height, width), 0.7, 14)

    def release(self):
        self.detector.release()


def needs_dodge(boxes, width, height, compatibility=True):
    if width <= 0 or height <= 0:
        return False
    for box in boxes:
        if box.name == "EnemyAttackGroundDodge":
            return True
        if box.name != "EnemyDodge":
            continue
        x = (box.x + box.width / 2) * 1280 / width
        y = (box.y + box.height / 2) * 720 / height
        if not compatibility or not (500 <= x < 780 and 265 <= y < 590):
            return True
    return False


class ReactiveDodge:
    COOLDOWN = REACTIVE_DODGE_COOLDOWN
    MAX_FRAME_AGE = 0.5
    RECOVERY_SECONDS = 0.15

    def __init__(self, task, detector_factory=MaaEndDodgeDetector):
        self.task = task
        self.factory = detector_factory
        self.detector = None
        self.failed = False
        self.next_at = 0.0
        self.slow_frames = 0
        self.rotation_pause_seconds = 0.0

    @staticmethod
    def _valid_frame(frame):
        shape = getattr(frame, "shape", None)
        return shape is not None and len(shape) >= 2 and shape[0] > 0 and shape[1] > 0

    def _inference_too_slow(self, duration):
        if duration <= self.MAX_FRAME_AGE:
            self.slow_frames = 0
            return False
        self.slow_frames += 1
        if self.slow_frames >= 3:
            self.failed = True
            self.task.log_warning(
                "MaaEnd auto dodge disabled for this battle: inference repeatedly exceeded 0.5 seconds"
            )
            self.close()
        return True

    def _ready_inference_time(self, allowed, deadline):
        """Return the start time if enabled, allowed and outside both cooldowns."""
        task = self.task
        if self.failed or not task.get_battle_config(KEY_REACTIVE_DODGE, False) or not allowed():
            return None
        now = task.active_time()
        if now < max(
            self.next_at,
            getattr(task, "_enemy_direction_next_dodge_at", 0.0),
            getattr(task, "_reactive_dodge_next_at", 0.0),
        ):
            return None
        if deadline is not None and now >= deadline:
            return None
        return now

    def step(self, release_attack, allowed=lambda: True, deadline=None, frame_captured_at=None):
        """Return True when this frame is consumed, not necessarily a dodge.

        Model initialization or stale inference also consume the frame so the
        caller rechecks deadlines and obtains a new frame before skill decisions.
        rotation_pause_seconds counts only loading and actual dodge handling,
        so routine inference does not extend a failed manual token's watchdog.
        """
        self.rotation_pause_seconds = 0.0
        now = self._ready_inference_time(allowed, deadline)
        if now is None:
            return False
        task = self.task
        frame = task.frame
        # A missing frame is a capture hiccup, not a permanent model failure.
        if not self._valid_frame(frame):
            return True
        captured_at = now if frame_captured_at is None else frame_captured_at
        if now - captured_at > self.MAX_FRAME_AGE:
            return True
        compatibility = task.get_battle_config(KEY_REACTIVE_DODGE_COMPAT, True)
        try:
            if self.detector is None:
                load_started = task.active_time()
                try:
                    self.detector = self.factory()
                finally:
                    self.rotation_pause_seconds = task.active_time() - load_started
                # Compilation can take seconds. Never act on the pre-load frame.
                return True
            boxes = self.detector.detect(frame, compatibility=compatibility)
        except Exception as exc:
            self.failed = True
            task.log_warning(f"MaaEnd auto dodge disabled for this battle: {exc}")
            self.close()
            return True
        after = task.active_time()
        if self._inference_too_slow(after - now):
            return True
        # A fast inference on an already old frame is still unsafe to act on.
        if after - captured_at > self.MAX_FRAME_AGE:
            return True
        if deadline is not None and after >= deadline:
            return True
        if not allowed():
            return False
        if not needs_dodge(boxes, frame.shape[1], frame.shape[0], compatibility):
            return False
        self.next_at = after + self.COOLDOWN
        task._reactive_dodge_next_at = self.next_at
        # Share the pursuit recovery guard so the next loop cannot dash
        # toward an enemy immediately after dodging its attack.
        task._enemy_direction_next_dodge_at = max(getattr(task, "_enemy_direction_next_dodge_at", 0.0), self.next_at)
        task._enemy_direction_no_center_until = max(
            getattr(task, "_enemy_direction_no_center_until", 0.0), self.next_at
        )
        # Task/input cancellation must propagate, not be mistaken for a model error.
        release_attack()
        task.press_key("lshift", down_time=0.02)
        remaining = (
            self.RECOVERY_SECONDS if deadline is None else min(self.RECOVERY_SECONDS, deadline - task.active_time())
        )
        if remaining > 0:
            task.sleep(remaining)
        self.rotation_pause_seconds = task.active_time() - after
        return True

    def close(self):
        if self.detector is not None:
            with suppress(Exception):
                self.detector.release()
            self.detector = None
