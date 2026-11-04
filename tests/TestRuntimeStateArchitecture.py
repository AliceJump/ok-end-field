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
    def test_quiet_info_preserves_account_labels_and_default_logging(self):
        from src.core.base_mixin.runtime_mixin import RuntimeMixin

        class Base:
            def info_set(self, key, value):
                self.logged.append((key, value))
                self.info[key] = value

        class Task(RuntimeMixin, Base):
            current_user = "account1234"

        task = Task()
        task.info, task.logged = {}, []
        task.info_set("定位", "⭐1,2", log=False)
        self.assertEqual(task.info, {"定位(1234)": "1,2"})
        self.assertEqual(task.logged, [])
        task.info_set("定位", "3,4")
        self.assertEqual(task.logged, [("定位(1234)", "3,4")])

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

    def test_item_navigator_always_uses_unified_position_service(self):
        task = object.__new__(ItemNavigatorTask)
        task.config = {}
        self.assertTrue(task._use_unified_navigation())

        task.config = {POSITION_IMPLEMENTATION_KEY: POSITION_IMPLEMENTATION_UNIFIED}
        self.assertTrue(task._use_unified_navigation())

    def test_item_navigator_does_not_dispatch_to_retired_ws_reader(self):
        task = object.__new__(ItemNavigatorTask)
        task.config = {POSITION_IMPLEMENTATION_KEY: "旧版兼容"}
        task._read_item_navigator_legacy_position = lambda: self.fail("Retired WS reader must not be called")
        task.world_pose = lambda **kwargs: {"map_id": "map02", "x": 1.0, "y": None, "z": 3.0, "position_trusted": True}
        self.assertEqual(task._read_navigation_position()[0], ("map02", 1.0, None, 3.0))
        task.world_pose = lambda **kwargs: {"map_id": "map02", "x": 1.0, "z": 3.0, "position_trusted": False}
        self.assertIsNone(task._read_navigation_position()[0])

    def test_item_navigator_migrates_published_config_before_base_load(self):
        task = object.__new__(ItemNavigatorTask)
        calls = []

        with (
            mock.patch("src.tasks.trigger.ItemNavigatorTask.update_overrides"),
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
