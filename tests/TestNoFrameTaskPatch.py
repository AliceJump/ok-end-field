"""no_frame_task_patch 的单元测试。

背景
----
`#353` 需要让纯 WS/键盘驱动的任务（如 ItemNavigatorTask）声明
`needs_frame=False` 后跳过整条截图管线。上游 TaskExecutor.execute() 里有：

    if cycled or self._frame is None:
        if self.next_frame(time_out=4) is None and is_trigger_task:
            # 取不到帧 -> 跳过剩余 trigger 任务
            continue

无帧任务永远不截图，`self._frame` 恒为 None，因此这句会命中并无限跳过任务。

核心回归点
----------
早期实现把整个 execute()（约 100 行）复制了一份，只为加一句
`and _task_wants_frame(self)`，导致与上游强耦合：ok-script 升级 execute()
时补丁静默失效。本测试锁定"最小插桩"方案的两条不变量：

1. `TaskExecutor.execute` 必须保持上游原对象——**不得**被复制替换；
2. 无帧任务经上游 execute() 的"无帧跳过"分支时，必须仍能执行 run()。

隔离策略
--------
用替身 TaskExecutor / BaseTask 注入 `ok.task.*` 模块，避免依赖真实 ok 库；
测试类结束后恢复原模块对象与全局安装标记。
"""

import sys
import types
import unittest

from src.patches import no_frame_task_patch as nf


class _FakeExecutor:
    """忠实复刻上游 TaskExecutor 的相关方法。"""

    def __init__(self):
        self._frame = None
        self.current_task = None
        self.capture_calls = 0
        self.skipped = False
        self.ran_tasks = []
        self.trigger_tasks = [object()]
        self.trigger_task_index = -1

    def execute(self):  # noqa: D102 - 契约占位
        return None

    def check_enabled(self, check_pause=True):
        pass

    def get_frame(self):
        self.capture_calls += 1
        return "REAL_FRAME"

    def next_frame(self, time_out=6):
        self.reset_scene()
        self._frame = self.get_frame()
        return self._frame

    @property
    def frame(self):
        """模拟上游 frame property（供检查哨兵是否会流入截图管线）。"""
        return self._frame

    def reset_scene(self, check_enabled=True):
        self._frame = None

    def execute_one_trigger_cycle(self, task):
        """复刻 execute() 内 trigger 任务的处理路径（含无帧跳过分支）。"""
        self.current_task = task
        cycled = True
        is_trigger_task = True
        if cycled or self._frame is None:
            if self.next_frame(time_out=4) is None and is_trigger_task:
                self.skipped = True
                self.current_task = None
                return "SKIPPED"
        if is_trigger_task:
            task.run()
            self.ran_tasks.append(task.name)
            return "RAN"
        return "?"


class _FakeTask:
    def __init__(self, needs_frame=True):
        self.name = "trigger"
        self.needs_frame = needs_frame
        self.run_calls = 0

    def run(self):
        self.run_calls += 1
        return False


class _BaseTask:
    def __init__(self):
        self.name = "base"


# 在导入期捕获"上游 execute"的原始函数对象。直接通过类属性访问会触发
# 描述符绑定变成 bound method，identity 比较必然失败，故从 __dict__ 取。
_ORIGINAL_EXECUTE_FUNC = _FakeExecutor.__dict__["execute"]


def _current_execute_func():
    return _FakeExecutor.__dict__["execute"]


class TestNoFrameTaskPatch(unittest.TestCase):
    _saved_modules = None
    _original_flag = None
    _original_execute = None
    _cleanup_completed = False

    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {
            name: sys.modules.get(name)
            for name in ("ok", "ok.task", "ok.task.TaskExecutor", "ok.task.task")
        }

        fake_executor_mod = types.ModuleType("ok.task.TaskExecutor")
        fake_executor_mod.TaskExecutor = _FakeExecutor
        fake_task_mod = types.ModuleType("ok.task.task")
        fake_task_mod.BaseTask = _BaseTask

        fake_ok = types.ModuleType("ok")
        fake_task_pkg = types.ModuleType("ok.task")
        fake_task_pkg.TaskExecutor = fake_executor_mod
        fake_task_pkg.task = fake_task_mod

        sys.modules["ok"] = fake_ok
        sys.modules["ok.task"] = fake_task_pkg
        sys.modules["ok.task.TaskExecutor"] = fake_executor_mod
        sys.modules["ok.task.task"] = fake_task_mod

        cls._original_flag = nf._PATCH_INSTALLED
        cls._original_execute = _ORIGINAL_EXECUTE_FUNC
        cls.addClassCleanup(cls._restore_state)

        nf._PATCH_INSTALLED = False
        nf.install_no_frame_task_patch()

    @classmethod
    def _restore_state(cls):
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        nf._PATCH_INSTALLED = cls._original_flag
        cls._cleanup_completed = True

    def test_execute_not_replaced(self):
        """核心不变量：execute() 必须保持上游原实现，不得复制替换。"""
        self.assertIs(_current_execute_func(), _ORIGINAL_EXECUTE_FUNC)

    def test_next_frame_and_reset_scene_wrapped(self):
        self.assertTrue(hasattr(_FakeExecutor.next_frame, "__wrapped__"))
        self.assertTrue(hasattr(_FakeExecutor.reset_scene, "__wrapped__"))

    def test_no_frame_task_does_not_capture(self):
        ex = _FakeExecutor()
        ex.current_task = _FakeTask(needs_frame=False)
        result = ex.next_frame(time_out=4)
        self.assertEqual(ex.capture_calls, 0)
        self.assertIsNotNone(result, "哨兵帧必须非 None 才能让上游 is None 判断失效")

    def test_sentinel_not_stored_in_frame(self):
        """哨兵帧不得写入 self._frame，否则异常路径会把它送进截图管线。"""
        ex = _FakeExecutor()
        ex.current_task = _FakeTask(needs_frame=False)
        ex.next_frame(time_out=4)
        self.assertIsNone(
            ex._frame,
            "self._frame 必须保持 None，避免 execute() 异常路径 "
            "communicate.screenshot.emit(self.frame, ...) 处理哨兵对象",
        )
        self.assertIsNone(ex.frame)

    def test_no_frame_trigger_task_not_skipped(self):
        """核心回归：无帧 trigger 任务不被 'no frame available' 分支跳过。"""
        ex = _FakeExecutor()
        task = _FakeTask(needs_frame=False)
        outcome = ex.execute_one_trigger_cycle(task)
        self.assertEqual(outcome, "RAN")
        self.assertFalse(ex.skipped)
        self.assertEqual(task.run_calls, 1)
        self.assertEqual(ex.capture_calls, 0, "无帧任务全流程不应截图")

    def test_frame_task_behavior_unchanged(self):
        ex = _FakeExecutor()
        task = _FakeTask(needs_frame=True)
        outcome = ex.execute_one_trigger_cycle(task)
        self.assertEqual(outcome, "RAN")
        self.assertEqual(ex.capture_calls, 1, "有帧任务应正常截图")
        self.assertEqual(task.run_calls, 1)

    def test_reset_scene_keeps_frame_for_no_frame_task(self):
        ex = _FakeExecutor()
        ex.current_task = _FakeTask(needs_frame=False)
        ex._frame = "KEEP"
        ex.reset_scene()
        self.assertEqual(ex._frame, "KEEP")

    def test_reset_scene_clears_frame_for_frame_task(self):
        ex = _FakeExecutor()
        ex.current_task = _FakeTask(needs_frame=True)
        ex._frame = "DROP"
        ex.reset_scene()
        self.assertIsNone(ex._frame)

    def test_base_task_defaults_needs_frame_true(self):
        self.assertTrue(_BaseTask().needs_frame)

    def test_install_idempotent(self):
        before_next = _FakeExecutor.next_frame
        nf.install_no_frame_task_patch()
        self.assertIs(_FakeExecutor.next_frame, before_next)
        self.assertTrue(nf._PATCH_INSTALLED)

    def test_missing_upstream_contract_raises(self):
        class _Broken:
            pass

        with self.assertRaises(RuntimeError) as ctx:
            nf._require_upstream_contract(_Broken, _BaseTask)
        self.assertIn("TaskExecutor 成员缺失", str(ctx.exception))

    def test_sentinel_frame_access_raises_clear_error(self):
        with self.assertRaises(AttributeError) as ctx:
            nf._FRAME_READY.shape
        self.assertIn("needs_frame=False", str(ctx.exception))


class TestZNoFrameTaskPatchStateRestored(unittest.TestCase):
    def test_global_state_restored_after_patch_tests(self):
        self.assertTrue(TestNoFrameTaskPatch._cleanup_completed)
        self.assertEqual(nf._PATCH_INSTALLED, TestNoFrameTaskPatch._original_flag)
        self.assertIs(_current_execute_func(), _ORIGINAL_EXECUTE_FUNC)


if __name__ == "__main__":
    unittest.main()
