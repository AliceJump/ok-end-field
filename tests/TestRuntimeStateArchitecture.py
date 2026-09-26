"""运行时状态架构边界测试。"""

import ast
import unittest
from pathlib import Path


class TestRuntimeStateArchitecture(unittest.TestCase):
    def test_runtime_state_has_a_purpose_specific_package_name(self):
        root = Path(__file__).parents[1] / "src"

        self.assertTrue((root / "runtime_state" / "__init__.py").is_file())
        self.assertFalse((root / "runtime" / "__init__.py").exists())

    def test_navigation_tasks_live_in_the_navigation_domain_package(self):
        root = Path(__file__).parents[1] / "src" / "tasks"
        names = (
            "MinimapNavigateToPoint.py",
            "MinimapPositionTask.py",
            "MinimapRealtimePosition.py",
            "MinimapRegionCheck.py",
            "MinimapScaleCapture.py",
            "MinimapTurnToHeading.py",
        )

        for name in names:
            self.assertTrue((root / "navigation" / name).is_file())
            self.assertFalse((root / "test" / name).exists())
            self.assertFalse((root / "trigger" / name).exists())

    def test_navigation_mixins_live_in_the_navigation_domain_package(self):
        root = Path(__file__).parents[1] / "src" / "tasks"
        names = (
            "grid_navigation_mixin.py",
            "map_mixin.py",
            "minimap_heading_mixin.py",
            "minimap_odometry.py",
            "minimap_position_fusion.py",
            "minimap_position_mixin.py",
            "navigation_mixin.py",
            "ws_position_mixin.py",
            "zip_line_mixin.py",
        )

        for name in names:
            self.assertTrue((root / "navigation" / "mixin" / name).is_file())
            self.assertFalse((root / "mixin" / name).exists())

    def test_business_modules_do_not_call_owner_sampling_method(self):
        root = Path(__file__).parents[1] / "src" / "tasks"
        allowed = {root / "navigation" / "mixin" / "minimap_position_mixin.py"}
        violations = []

        for path in root.rglob("*.py"):
            if path in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "minimap_position":
                    violations.append(f"{path.relative_to(root.parent.parent)}:{node.lineno}")

        self.assertEqual(violations, [], f"业务模块必须通过 RuntimeStateMixin 读取位置: {violations}")

    def test_item_navigator_uses_runtime_state_contract(self):
        path = Path(__file__).parents[1] / "src" / "tasks" / "trigger" / "ItemNavigatorTask.py"
        source = path.read_text(encoding="utf-8")

        self.assertIn("RuntimeStateMixin", source)
        self.assertNotIn("WsPositionMixin", source)
        self.assertNotIn("_recv_ws_position_payload", source)


if __name__ == "__main__":
    unittest.main()
