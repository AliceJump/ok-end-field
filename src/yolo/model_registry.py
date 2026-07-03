from __future__ import annotations

from copy import deepcopy

from src.yolo.models import DEFAULT_MODEL_KEY, YOLO_MODELS


def _normalize_labels(labels) -> dict[int, str]:
    normalized: dict[int, str] = {}
    for key, value in (labels or {}).items():
        try:
            class_id = int(key)
        except (TypeError, ValueError):
            continue
        normalized[class_id] = str(value)
    return normalized


def build_yolo_model_settings(yolo_config: dict | None) -> tuple[str, dict[str, dict]]:
    config = yolo_config or {}
    settings = deepcopy(YOLO_MODELS)

    custom_models = config.get("models", {})
    if isinstance(custom_models, dict):
        for model_key, model_info in custom_models.items():
            if not isinstance(model_info, dict):
                continue
            model_path = str(model_info.get("model_path") or "").strip()
            if not model_path:
                continue
            settings[str(model_key)] = {
                "model_path": model_path,
                "labels": _normalize_labels(model_info.get("labels")),
            }

    legacy_model_path = str(config.get("model_path", "")).strip()
    if legacy_model_path:
        settings[DEFAULT_MODEL_KEY]["model_path"] = legacy_model_path

    for model_info in settings.values():
        labels = model_info.get("labels")
        if not labels:
            model_info["labels"] = deepcopy(YOLO_MODELS[DEFAULT_MODEL_KEY]["labels"])

    default_model = str(config.get("default_model", DEFAULT_MODEL_KEY)).strip() or DEFAULT_MODEL_KEY
    if default_model not in settings:
        default_model = DEFAULT_MODEL_KEY
    return default_model, settings


def list_model_keys(settings: dict[str, dict]) -> list[str]:
    return list(settings.keys())


def list_target_names(settings: dict[str, dict], model_key: str) -> list[str]:
    model_info = settings.get(model_key, {})
    labels = model_info.get("labels", {})
    return sorted({str(name) for name in labels.values() if name is not None})


def build_name_to_model_map(settings: dict[str, dict]) -> dict[str, str]:
    name_map: dict[str, str] = {}
    for model_key, model_info in settings.items():
        labels = model_info.get("labels", {})
        for label_name in labels.values():
            if label_name is None:
                continue
            name_map.setdefault(str(label_name), model_key)
    return name_map
