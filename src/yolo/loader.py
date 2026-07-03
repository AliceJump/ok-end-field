from __future__ import annotations

from ok import Logger

from src.yolo.model_registry import (
    build_name_to_model_map,
    build_yolo_model_settings,
    list_model_keys,
    list_target_names,
)

logger = Logger.get_logger(__name__)


class YoloModelLoader:
    def __init__(self, yolo_config: dict | None = None):
        self.default_model_key, self.model_settings = build_yolo_model_settings(yolo_config)
        self._name_to_model_key = build_name_to_model_map(self.model_settings)
        self._active_model_key: str | None = None
        self._active_detector = None

    def available_models(self) -> list[str]:
        return list_model_keys(self.model_settings)

    def target_names(self, model_key: str | None = None) -> list[str]:
        key = model_key or self.default_model_key
        return list_target_names(self.model_settings, key)

    def get_model_info(self, model_key: str | None = None) -> dict:
        key = model_key or self.default_model_key
        if key not in self.model_settings:
            raise ValueError(f"未知 YOLO 模型: {key}")
        return self.model_settings[key]

    def resolve_model_by_name(self, name: str) -> str:
        target = str(name)
        model_key = self._name_to_model_key.get(target)
        if model_key:
            return model_key
        raise ValueError(f"未找到目标[{target}]对应的YOLO模型，请检查 src/yolo/models.py 中的 labels")

    def get_detector(self, model_key: str | None = None):
        key = model_key or self.default_model_key
        if self._active_model_key == key and self._active_detector is not None:
            return self._active_detector

        model_info = self.get_model_info(key)
        model_path = model_info["model_path"]
        labels = model_info.get("labels", {})

        from src.yolo.openvino_detector import OpenVinoYolo8Detect

        logger.info(f"Loading YOLO model [{key}] from {model_path}")
        detector = OpenVinoYolo8Detect(weights=model_path, labels=labels)
        self._active_model_key = key
        self._active_detector = detector
        return detector

    def get_detector_for_name(self, name: str):
        model_key = self.resolve_model_by_name(name)
        detector = self.get_detector(model_key)
        return model_key, detector

    def release(self):
        detector = self._active_detector
        if detector is not None and hasattr(detector, "release"):
            detector.release()
        self._active_detector = None
        self._active_model_key = None
