"""Latest-frame detection workers; no capture, task lifecycle or input on workers."""

import contextlib
from copy import copy, deepcopy
from dataclasses import dataclass
from threading import Condition, Thread
from types import SimpleNamespace


@dataclass(frozen=True)
class NavigationFrame:
    sequence: int
    captured_at: float
    image: object


@dataclass(frozen=True)
class NavigationDetection:
    sequence: int
    captured_at: float
    boxes: tuple


class LatestFrameDetector:
    """One in-flight frame plus one replaceable pending frame, with latched hits."""

    def __init__(self, detect, *, name, cancel=None):
        self._detect = detect
        self._cancel = cancel
        self._condition = Condition()
        self._pending = None
        self._latest = None
        self._hit = None
        self._error = None
        self._closed = False
        self._thread = Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def submit(self, frame):
        with self._condition:
            if not self._closed:
                self._pending = frame
                self._condition.notify()

    def snapshot(self):
        with self._condition:
            if self._error is not None:
                raise RuntimeError(f"{self._thread.name} failed") from self._error
            return self._latest, self._hit

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                frame, self._pending = self._pending, None
            try:
                boxes = tuple(copy(box) for box in (self._detect(frame.image) or ()))
            except Exception as error:
                with self._condition:
                    if not self._closed:
                        self._error = error
                return
            with self._condition:
                if self._closed:
                    return
                self._latest = NavigationDetection(frame.sequence, frame.captured_at, boxes)
                if boxes:
                    self._hit = self._latest

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()
        if self._thread.ident is None:
            return
        self._thread.join(timeout=1)
        if self._thread.is_alive() and self._cancel is not None:
            self._cancel()
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise RuntimeError(f"{self._thread.name} did not stop")


class _DedicatedRequest:
    """Keep the compiled model, but never reuse its implicit synchronous request."""

    def __init__(self, compiled):
        self.outputs = compiled.outputs
        self.request = compiled.create_infer_request()

    def __call__(self, inputs):
        return self.request.infer(inputs, share_inputs=False, share_outputs=False)

    def cancel(self):
        # The request may finish between join() timing out and cancellation.
        with contextlib.suppress(RuntimeError):
            self.request.cancel()


def _clone_ocr(source):
    """Give each OpenVINO predictor an independent request and preprocessing state."""
    model = copy(source)
    sessions = []
    for name in ("text_detector", "text_recognizer", "text_classifier"):
        original = getattr(source, name, None)
        if original is None:
            continue
        predictor = copy(original)
        if not getattr(predictor, "is_openvino", False):
            raise RuntimeError("Parallel navigation requires the configured OpenVINO OCR backend")
        session = _DedicatedRequest(original.session)
        sessions.append(session)
        predictor.session = session
        for attribute in ("preprocess_op", "postprocess_op", "rec_image_shape", "args"):
            if hasattr(original, attribute):
                setattr(predictor, attribute, deepcopy(getattr(original, attribute)))
        setattr(model, name, predictor)
    if not sessions:
        raise RuntimeError("Parallel navigation requires ONNXPaddleOcr predictors")
    return model, sessions


def create_navigation_detectors(task, target, nav, target_box, nav_box):
    """Prepare independent engines on the owner thread, before movement starts."""
    from ok.feature.Box import Box, find_boxes_by_name
    from ok.feature.FeatureSet import FeatureSet
    from ok.task.task import OCR

    executor = task.executor
    threshold = task.ocr_default_threshold
    model, sessions = _clone_ocr(executor.ocr_lib())
    matcher = task.fix_match_regex(target)
    # Reuse framework text corrections without sharing task/executor mutations.
    fixer = SimpleNamespace(
        executor=SimpleNamespace(
            config={"ocr": deepcopy(executor.config.get("ocr", {}))},
            locale=executor.locale,
            ocr_po_translation=executor.ocr_po_translation,
            text_fix=dict(executor.text_fix),
        )
    )

    def detect_target(frame):
        crop = target_box.crop_frame(frame).copy()
        result = model.ocr(crop)
        boxes = []
        for points, (text, confidence) in result[0] or ():
            if confidence < threshold:
                continue
            left, top = points[0]
            right, bottom = points[2]
            if right > left and bottom > top:
                boxes.append(
                    Box(
                        round(left) + target_box.x,
                        round(top) + target_box.y,
                        round(right - left),
                        round(bottom - top),
                        confidence=confidence,
                        name=text,
                    )
                )
        OCR.fix_texts(fixer, boxes)
        return find_boxes_by_name(boxes, matcher)

    original = executor.feature_set
    features = FeatureSet(
        False,
        original.coco_json,
        original.default_horizontal_variance,
        original.default_vertical_variance,
        original.default_threshold,
        feature_processor=deepcopy(original.feature_processor),
        hcenter_features=list(original.hcenter_features),
        vcenter_features=list(original.vcenter_features),
    )
    names = [task.get_feature_by_resolution(name) for name in (nav if isinstance(nav, (list, tuple)) else [nav])]

    def detect_nav(frame):
        return features.find_feature(frame, names, box=nav_box, threshold=0.7, limit=1)

    def cancel_ocr():
        for session in sessions:
            session.cancel()

    return (
        LatestFrameDetector(detect_target, name="NavigationArrivalOCR", cancel=cancel_ocr),
        LatestFrameDetector(detect_nav, name="NavigationMarker"),
    )
