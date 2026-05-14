import unittest

from src.yolo.model_registry import build_name_to_model_map, build_yolo_model_settings


class TestYoloModelRegistry(unittest.TestCase):
    def test_build_yolo_model_settings_merges_custom_models(self):
        default_key, settings = build_yolo_model_settings(
            {
                "default_model": "my_model",
                "models": {
                    "my_model": {
                        "model_path": "assets/models/yolo/my.onnx",
                        "labels": {"0": "target_a", 1: "target_b"},
                    }
                },
            }
        )
        self.assertEqual("my_model", default_key)
        self.assertIn("my_model", settings)
        self.assertEqual("assets/models/yolo/my.onnx", settings["my_model"]["model_path"])
        self.assertEqual({0: "target_a", 1: "target_b"}, settings["my_model"]["labels"])

    def test_build_yolo_model_settings_supports_legacy_model_path(self):
        default_key, settings = build_yolo_model_settings({"model_path": "assets/models/yolo/legacy.onnx"})
        self.assertEqual("battle_end_default", default_key)
        self.assertEqual("assets/models/yolo/legacy.onnx", settings["battle_end_default"]["model_path"])

    def test_build_name_to_model_map_routes_name_to_model(self):
        _, settings = build_yolo_model_settings(
            {
                "models": {
                    "model_a": {"model_path": "a.onnx", "labels": {0: "target_a"}},
                    "model_b": {"model_path": "b.onnx", "labels": {0: "target_b"}},
                }
            }
        )
        name_map = build_name_to_model_map(settings)
        self.assertEqual("model_a", name_map["target_a"])
        self.assertEqual("model_b", name_map["target_b"])


if __name__ == "__main__":
    unittest.main()
