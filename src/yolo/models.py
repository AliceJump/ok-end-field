from __future__ import annotations

DEFAULT_MODEL_KEY = "battle_end_default"

# 统一维护 YOLO 模型路径与 labels(dict) 映射
YOLO_MODELS = {
    DEFAULT_MODEL_KEY: {
        "model_path": "assets/models/yolo/best.onnx",
        "labels": {
            0: "battle_end",
        },
    },
}
