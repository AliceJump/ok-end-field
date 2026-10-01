"""按任务实例启停的导航检测记录；作用域外恢复所有原始方法。"""

import inspect
import re
from copy import copy
from dataclasses import dataclass
from functools import wraps
from typing import ClassVar

_SCOPE_ATTRIBUTE = "_navigation_detection_scope"
_MISSING = object()


def get_navigation_detection_scope(task):
    return vars(task).get(_SCOPE_ATTRIBUTE)


def _matchers(value):
    values = value if isinstance(value, (list, tuple)) else [value]
    return tuple(
        sorted(
            ("regex", item.pattern, item.flags)
            if isinstance(item, re.Pattern)
            else ("name", str(getattr(item, "value", item)))
            for item in values
        )
    )


@dataclass(frozen=True)
class DetectionObservation:
    fingerprint: tuple
    observed_at: float
    boxes: tuple


class NavigationDetectionScope:
    """用 with 自动关闭，也可显式调用 start()/close()；支持嵌套。"""

    _DETECTORS: ClassVar[dict] = {
        "yolo_detect": ("yolo", "name", "conf", 0.7),
        "find_feature": ("feature", "feature", "threshold", 0.7),
        "ocr": ("ocr", "match", None, None),
        "wait_ocr": ("ocr", "match", None, None),
    }

    def __init__(self, task, max_age=2.0):
        self.task = task
        self.max_age = max_age
        self._observations = {}
        self._tracked_targets = set()
        self._saved_attributes = {}
        self._active = False

    def track(self, kind, target):
        """只有本次导航目标的近期检测才能阻止中键重置。"""
        self._tracked_targets.add((kind, _matchers(target)))

    def latest(self, kind, target, threshold=0.7, model_key=None, frame_processor=None):
        matchers = _matchers(target)
        options = (threshold if kind != "ocr" else None, model_key, id(frame_processor) if frame_processor else None)
        now = self.task.active_time()
        candidates = [
            observation
            for (family, names, *settings), observation in self._observations.items()
            if family == kind
            and (names == matchers or (kind == "feature" and set(names).issubset(matchers)))
            and tuple(settings) == options
            and 0 <= now - observation.observed_at <= self.max_age
        ]
        if not candidates:
            return None
        observation = max(candidates, key=lambda item: item.observed_at)
        return DetectionObservation(
            observation.fingerprint, observation.observed_at, tuple(copy(box) for box in observation.boxes)
        )

    def has_recent_target(self):
        now = self.task.active_time()
        for (kind, names, *_settings), observation in self._observations.items():
            if 0 <= now - observation.observed_at <= self.max_age and any(
                kind == tracked_kind and set(names).issubset(tracked_names)
                for tracked_kind, tracked_names in self._tracked_targets
            ):
                return True
        return False

    def clear(self):
        self._observations.clear()

    @staticmethod
    def _arguments(signature, args, kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        for name, parameter in signature.parameters.items():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                arguments.update(arguments.pop(name, {}))
            elif parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                positional = arguments.pop(name, ())
                if positional:
                    arguments.setdefault("_first_argument", positional[0])
        return arguments

    def _wrap(self, name, original):
        signature = inspect.signature(original)

        @wraps(original)
        def wrapped(*args, **kwargs):
            if get_navigation_detection_scope(self.task) is not self:
                return original(*args, **kwargs)
            arguments = self._arguments(signature, args, kwargs)
            if name == "click":
                if arguments.get("key") == "middle":
                    if self.has_recent_target():
                        self.task.log_info("近期已检测到终点目标，跳过中键重置视野")
                        return None
                    # 中键改变视野，旧坐标和残影立即失效。
                    self.clear()
                return original(*args, **kwargs)

            kind, target_argument, threshold_argument, default_threshold = self._DETECTORS[name]
            target = arguments.get(target_argument)
            if target is None:
                target = arguments.get("feature_name") if name == "find_feature" else None
            if target is None:
                target = arguments.get("_first_argument")
            fingerprint = (
                kind,
                _matchers(target),
                arguments.get(threshold_argument, default_threshold) if threshold_argument else None,
                arguments.get("model_key"),
                id(arguments["frame_processor"]) if arguments.get("frame_processor") else None,
            )
            # 按检测开始时间记录，检测耗时不能把旧画面变成新观测。
            observed_at = self.task.active_time()
            result = original(*args, **kwargs)
            boxes = result if isinstance(result, (list, tuple)) else [result]
            if result and all(all(hasattr(box, attr) for attr in ("x", "y", "width", "height")) for box in boxes):
                previous = self._observations.get(fingerprint)
                if previous is None or observed_at >= previous.observed_at:
                    self._observations[fingerprint] = DetectionObservation(
                        fingerprint, observed_at, tuple(copy(box) for box in boxes)
                    )
            return result

        return wrapped

    def __enter__(self):
        if self._active:
            raise RuntimeError("导航检测作用域已启用")
        self._active = True
        self._saved_attributes[_SCOPE_ATTRIBUTE] = vars(self.task).get(_SCOPE_ATTRIBUTE, _MISSING)
        setattr(self.task, _SCOPE_ATTRIBUTE, self)
        try:
            for name in (*self._DETECTORS, "click"):
                original = getattr(self.task, name, None)
                if callable(original):
                    self._saved_attributes[name] = vars(self.task).get(name, _MISSING)
                    setattr(self.task, name, self._wrap(name, original))
        except BaseException:
            self.close()
            raise
        return self

    def start(self):
        """显式启用；与 close 配对。优先使用 with 保证异常时也关闭。"""
        return self.__enter__()

    def close(self):
        if not self._active:
            return
        for name, previous in self._saved_attributes.items():
            if previous is _MISSING:
                vars(self.task).pop(name, None)
            else:
                setattr(self.task, name, previous)
        self._saved_attributes.clear()
        self.clear()
        self._active = False

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
