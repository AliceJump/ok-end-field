"""允许 TriggerTask / BaseTask 声明 needs_frame=False 以跳过整个截图管线。

ok-script TaskExecutor.execute() 在每轮 trigger cycle 开始时无条件调用
reset_scene() → self._frame = None，紧接着 next_frame() → method.get_frame()
→ WGC do_get_frame()。对于纯 WS/键盘驱动、不需要画面识别的任务（如
ItemNavigatorTask），这会产生不必要的 GPU 截图开销，甚至触发
"latency too large return None frame" 警告。

本补丁做两件事：
1. 在 BaseTask.__init__ 中注入 needs_frame=True（默认行为不变）
2. 在 TaskExecutor 的 next_frame() / reset_scene() 入口处检查当前任务的
   needs_frame；若为 False 则短路返回，既不触发截图也不清空已有帧。

任务只需在自己的 __init__ 中设置 self.needs_frame = False 即可生效。
"""

from __future__ import annotations

from functools import wraps

_PATCH_INSTALLED = False


def _task_wants_frame(executor) -> bool:
    """当前任务是否需要帧截图（保守默认 True）。"""
    task = getattr(executor, "current_task", None)
    return getattr(task, "needs_frame", True)


def install_no_frame_task_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.task.task import BaseTask
    from ok.task.TaskExecutor import TaskExecutor

    # --- 1. 注入 needs_frame 属性 -------------------------------------------
    _original_base_init = BaseTask.__init__

    @wraps(_original_base_init)
    def _patched_base_init(self, *args, **kwargs):
        _original_base_init(self, *args, **kwargs)
        # 仅在 __init__ 末尾追加，不覆盖子类可能已设置的值
        if not hasattr(self, "needs_frame"):
            self.needs_frame = True

    BaseTask.__init__ = _patched_base_init

    # --- 2. 让 reset_scene() 在不需要帧时跳过清帧 --------------------------
    _original_reset_scene = TaskExecutor.reset_scene

    @wraps(_original_reset_scene)
    def _patched_reset_scene(self, check_enabled=True):
        if not _task_wants_frame(self):
            # 任务声明不需要帧 → 保留已有帧，不触发 reset_scene 中的清帧逻辑
            # 仍需执行 check_enabled 以保证任务启停检测正常
            if check_enabled:
                self.check_enabled()
            return
        return _original_reset_scene(self, check_enabled)

    TaskExecutor.reset_scene = _patched_reset_scene

    # --- 3. 让 next_frame() 在不需要帧时短路 --------------------------------
    _original_next_frame = TaskExecutor.next_frame

    @wraps(_original_next_frame)
    def _patched_next_frame(self, time_out=6):
        if not _task_wants_frame(self):
            # 任务声明不需要帧 → 直接返回已缓存帧（可能为 None），不触发截图
            return self._frame
        return _original_next_frame(self, time_out)

    TaskExecutor.next_frame = _patched_next_frame

    _PATCH_INSTALLED = True
