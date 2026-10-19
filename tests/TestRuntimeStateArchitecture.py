"""运行时状态架构边界测试。"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ok import TriggerTask

from src.config import config as app_config
from src.core.BaseEfTask import BaseEfTask
from src.localization.position_service import (
    WorldPoseService,
    get_world_pose_service,
    shutdown_world_pose_service,
)
from src.tasks.trigger.ItemNavigatorTask import (
    POSITION_IMPLEMENTATION_KEY,
    POSITION_IMPLEMENTATION_UNIFIED,
    ItemNavigatorTask,
)


class TestRuntimeStateArchitecture(unittest.TestCase):
    def test_runtime_state_has_a_purpose_specific_package_name(self):
        root = Path(__file__).parents[1] / "src"

        self.assertTrue((root / "runtime_state" / "__init__.py").is_file())
        self.assertFalse((root / "runtime" / "__init__.py").exists())

    def test_localization_tasks_and_services_live_in_the_localization_domain_package(self):
        root = Path(__file__).parents[1] / "src" / "tasks"
        names = (
            "MinimapRealtimePosition.py",
            "MinimapRegionCheck.py",
            "MinimapScaleCapture.py",
            "MinimapTurnToHeading.py",
        )

        for name in names:
            self.assertTrue((root / "localization" / name).is_file())
            self.assertFalse((root / "navigation" / name).exists())
            self.assertFalse((root / "test" / name).exists())
            self.assertFalse((root / "trigger" / name).exists())
        self.assertTrue((root.parent / "localization" / "position_service.py").is_file())

    def test_world_position_service_is_not_registered_as_a_trigger_task(self):
        registered = {class_name for _module, class_name in app_config["trigger_tasks"]}
        self.assertNotIn("MinimapPositionTask", registered)
        self.assertFalse(issubclass(WorldPoseService, TriggerTask))

    def test_world_position_service_is_lazy_and_executor_scoped(self):
        created = []

        class _Service:
            def __init__(self, **kwargs):
                created.append(kwargs)
                self.destroyed = False

            def after_init(self, **kwargs):
                self.init = kwargs

            def on_destroy(self):
                self.destroyed = True

        executor = SimpleNamespace(scene=None, app=SimpleNamespace())
        with mock.patch("src.localization.position_service.WorldPoseService", _Service):
            first = get_world_pose_service(executor)
            second = get_world_pose_service(executor)

        self.assertIs(first, second)
        self.assertEqual(len(created), 1)
        shutdown_world_pose_service(executor)
        self.assertTrue(first.destroyed)
        self.assertIsNone(getattr(executor, "_world_pose_service", None))

    def test_world_position_service_uses_translation_capable_app(self):
        created = []

        class _Service:
            def __init__(self, **kwargs):
                created.append(kwargs)

            def after_init(self, **_kwargs):
                pass

        translation_app = SimpleNamespace(tr=lambda text: text)
        executor = SimpleNamespace(scene=None)
        with (
            mock.patch("src.localization.position_service.WorldPoseService", _Service),
            mock.patch("ok.og", SimpleNamespace(app=translation_app)),
        ):
            get_world_pose_service(executor)

        self.assertIs(created[0]["app"], translation_app)

    def test_navigation_mixins_live_in_the_navigation_domain_package(self):
        root = Path(__file__).parents[1] / "src" / "tasks"
        names = (
            "grid_navigation_mixin.py",
            "map_mixin.py",
            "navigation_mixin.py",
            "zip_line_mixin.py",
        )

        for name in names:
            self.assertTrue((root / "navigation" / "mixin" / name).is_file())
            self.assertFalse((root / "mixin" / name).exists())

    def test_navigation_depends_on_pose_interface_not_localization_impl(self):
        root = Path(__file__).parents[1] / "src"
        navigation_root = root / "tasks" / "navigation"
        for path in navigation_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("src.localization", source, str(path))
            self.assertNotIn("MinimapHeadingMixin", source, str(path))

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

    def test_item_navigator_keeps_legacy_position_source_unless_explicitly_enabled(self):
        task = object.__new__(ItemNavigatorTask)
        task.config = {}
        self.assertFalse(task._use_unified_navigation())

        task.config = {POSITION_IMPLEMENTATION_KEY: POSITION_IMPLEMENTATION_UNIFIED}
        self.assertTrue(task._use_unified_navigation())

    def test_item_navigator_dispatches_position_reader_by_compatibility_mode(self):
        task = object.__new__(ItemNavigatorTask)
        task.config = {}
        task._read_item_navigator_legacy_position = lambda: (("legacy", 1.0, 2.0, 3.0), "")
        task.world_pose = lambda **_kwargs: self.fail("旧版兼容不得读取统一定位")
        self.assertEqual(task._read_navigation_position()[0][0], "legacy")

        task.config = {POSITION_IMPLEMENTATION_KEY: POSITION_IMPLEMENTATION_UNIFIED}
        task.world_pose = lambda **_kwargs: {"map_id": "unified", "x": 1.0, "y": 2.0, "z": 3.0}
        self.assertEqual(task._read_navigation_position()[0][0], "unified")

    def test_item_navigator_migrates_published_config_before_base_load(self):
        task = object.__new__(ItemNavigatorTask)
        calls = []

        with (
            mock.patch(
                "src.tasks.trigger.ItemNavigatorTask.migrate_task_nav_values_to_global",
                side_effect=lambda class_name: calls.append(("migration", class_name)),
            ),
            mock.patch.object(
                BaseEfTask,
                "load_config",
                autospec=True,
                side_effect=lambda _task: calls.append(("base", None)),
            ),
        ):
            ItemNavigatorTask.load_config(task)

        self.assertEqual(calls, [("migration", "ItemNavigatorTask"), ("base", None)])


if __name__ == "__main__":
    unittest.main()
