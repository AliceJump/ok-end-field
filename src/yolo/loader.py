from __future__ import annotations

from ok import Logger

from src.yolo.model_registry import build_yolo_model_settings, list_model_keys, list_target_names

logger = Logger.get_logger(__name__)


class YoloModelLoader:
    def __init__(self, yolo_config: dict | None = None):
        self.default_model_key, self.model_settings = build_yolo_model_settings(yolo_config)
        self._detector_cache: dict[str, object] = {}

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

    def get_detector(self, model_key: str | None = None):
        key = model_key or self.default_model_key
        if key in self._detector_cache:
            return self._detector_cache[key]

        model_info = self.get_model_info(key)
        model_path = model_info["model_path"]
        labels = model_info.get("labels", {})

        from src.yolo.openvino_detector import OpenVinoYolo8Detect

        logger.info(f"Loading YOLO model [{key}] from {model_path}")
        detector = OpenVinoYolo8Detect(weights=model_path, labels=labels)
        self._detector_cache[key] = detector
        return detector
