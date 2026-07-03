from __future__ import annotations

DEFAULT_MODEL_KEY = "battle_end_default"

# 统一维护 YOLO 模型路径与 labels(dict) 映射(labels不建议重复, 但不强制), 方便自动切换模型
YOLO_MODELS = {
    DEFAULT_MODEL_KEY: {
        "model_path": "assets/models/yolo/best.onnx",
        "labels": {
            0: "battle_end",
        },
    },
}

def get_all_label_names() -> list[str]:
    """
    获取所有模型中的 labels 名称
    """
    labels: list[str] = []

    for model_info in YOLO_MODELS.values():
        labels.extend(model_info.get("labels", {}).values())

    return labels
