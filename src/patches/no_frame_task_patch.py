"""允许 TriggerTask / BaseTask 声明 needs_frame=False 以跳过整个截图管线。

ok-script TaskExecutor 在每轮 trigger cycle 里无条件走截图流程：execute()
调用 reset_scene() 清帧、next_frame() → method.get_frame() 截图。对于纯
WS/键盘驱动、不需要画面识别的任务（如 ItemNavigatorTask），这会产生不必要
的 GPU 截图开销，甚至触发 "latency too large return None frame" 警告。

为什么必须处理 execute()
----
上游 execute() 中有一处判断：

    if cycled or self._frame is None:
        if self.next_frame(time_out=4) is None and is_trigger_task:
            # 把"取不到帧"一律当作截图失败，跳过剩余 trigger 任务
            continue

无帧任务的 next_frame() 永远不会截图，_frame 始终为 None，因此这句必然
命中，任务会被无限跳过——这正是 #353 需要修掉的点。所以仅包装
next_frame()/reset_scene() 不够，execute() 里的判断也必须被中和。

为什么不是复制 execute()
----
早期实现把整个 execute()（约 100 行，含异常处理与退出逻辑）复制了一份。
核对上游源码后可以确认：复制体相对上游**唯一的真实语义改动**就是给这句
加上 `and _task_wants_frame(self)`，其余全是逐行照抄。这种做法的代价是
与上游强耦合——ok-script 升级 execute()（改异常分支、加超时、调退出顺序）
时补丁不会报错，只会静默运行一份过期的执行循环，漂移极难发现。

本补丁改用最小插桩，不复制任何上游实现体：

1. BaseTask.__init__ 末尾注入 needs_frame=True（默认行为不变）
2. 包装 next_frame()：无帧任务不截图，返回一个"已就绪"的哨兵帧
3. 包装 reset_scene()：无帧任务跳过清帧，但仍执行 check_enabled()
4. execute() 保持上游原样，不做替换

关键在第 2 步的返回值：无帧任务返回哨兵帧而非 None，于是上游那句
`next_frame(...) is None and is_trigger_task` 自然不成立，任务不会被跳过；
而 execute() 后续走的是 `if is_trigger_task: task.run()`，直接执行任务，
不需要帧。这样"是否截图"由本补丁决定，而执行循环、异常处理、退出逻辑
全部由上游代码决定，升级不会产生静默语义漂移。

哨兵帧的约定
----
哨兵帧需要满足上游对返回值的 `is None` 判断，因此必须**非 None**；但
**不得写入 self._frame**，否则 execute() 异常路径的

    if self._frame is not None:
        communicate.screenshot.emit(self.frame, name, True, None)

会把哨兵送进截图管线（PIL/cv2）导致 worker 线程报错。所以 self._frame
保持 None，`frame` property 也不会把哨兵交给需要真实图像的调用方。
"""

from __future__ import annotations

import logging

_PATCH_INSTALLED = False

logger = logging.getLogger(__name__)


class _FrameReadySentinel:
    """无帧任务的占位"帧"：非 None，用于让上游 `is None` 判断失效。

    无帧任务的 run() 契约是不读取画面；若任务真的访问了帧内容，会立刻在
    本对象上抛 AttributeError，而不是悄悄拿到 None 后继续运行。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试可读性
        return "<needs_frame=False: 该任务不截图>"

    def __bool__(self) -> bool:
        return True

    def __getattr__(self, item):
        raise AttributeError(
            f"任务声明了 needs_frame=False，不应访问帧属性 {item!r}；"
            "请检查该任务是否真的不需要画面"
        )


_FRAME_READY = _FrameReadySentinel()


def _current_task(executor):
    return getattr(executor, "current_task", None)


def _task_wants_frame(executor) -> bool:
    """当前任务是否需要帧截图（保守默认 True：拿不到任务时按需要帧处理）。"""
    return getattr(_current_task(executor), "needs_frame", True)


def install_no_frame_task_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.task.TaskExecutor import TaskExecutor
    from ok.task.task import BaseTask

    _require_upstream_contract(TaskExecutor, BaseTask)

    # --- 1. 注入 needs_frame 属性 -------------------------------------------
    _original_base_init = BaseTask.__init__

    def _patched_base_init(self, *args, **kwargs):
        _original_base_init(self, *args, **kwargs)
        # 仅在 __init__ 末尾追加，不覆盖子类可能已设置的值
        if not hasattr(self, "needs_frame"):
            self.needs_frame = True

    _patched_base_init.__wrapped__ = _original_base_init
    BaseTask.__init__ = _patched_base_init

    # --- 2. next_frame()：无帧任务不截图，返回哨兵帧 --------------------------
    _original_next_frame = TaskExecutor.next_frame

    def _patched_next_frame(self, time_out=6):
        if not _task_wants_frame(self):
            # 不调用上游 next_frame()，因此不触发 get_frame()/截图。
            # 返回哨兵帧：让上游 execute() 的 `... is None and is_trigger_task`
            # 判断失效，任务得以继续执行 run()。
            #
            # 注意：**不写入 self._frame**。self._frame 仍保持 None，这样：
            #   - `frame` property 不会把哨兵交给需要真实图像的调用方；
            #   - execute() 异常路径的 `if self._frame is not None:
            #     communicate.screenshot.emit(self.frame, ...)` 保持不触发，
            #     避免把哨兵帧送进截图管线（PIL/cv2）导致 worker 线程报错。
            return _FRAME_READY
        return _original_next_frame(self, time_out)

    _patched_next_frame.__wrapped__ = _original_next_frame
    TaskExecutor.next_frame = _patched_next_frame

    # --- 3. reset_scene()：无帧任务跳过清帧，但保留 check_enabled ------------
    _original_reset_scene = TaskExecutor.reset_scene

    def _patched_reset_scene(self, check_enabled=True):
        if not _task_wants_frame(self):
            if check_enabled:
                self.check_enabled()
            return None
        return _original_reset_scene(self, check_enabled)

    _patched_reset_scene.__wrapped__ = _original_reset_scene
    TaskExecutor.reset_scene = _patched_reset_scene

    # --- 4. execute()：不替换，保持上游实现 ----------------------------------
    # 上游 execute() 会经过上面两个包装，行为已满足需求。此处显式记录
    # 该决定，防止后人再次复制整个 execute()。

    _PATCH_INSTALLED = True
    logger.info("no_frame_task_patch installed (minimal instrumentation)")


def _require_upstream_contract(TaskExecutor, BaseTask) -> None:
    """校验依赖的上游接口仍在，缺失时显式失败而不是静默降级。

    ok-script 升级若改动这些成员，应当启动即报错，而不是运行到一半才暴露。
    """
    required_executor_attrs = (
        "next_frame",
        "reset_scene",
        "execute",
        "check_enabled",
    )
    missing = [name for name in required_executor_attrs if not hasattr(TaskExecutor, name)]
    if missing:
        raise RuntimeError(
            "no_frame_task_patch 依赖的 TaskExecutor 成员缺失: "
            f"{missing}；请更新 src/patches/no_frame_task_patch.py 以适配新版 ok-script"
        )
    if not callable(getattr(BaseTask, "__init__", None)):
        raise RuntimeError("no_frame_task_patch 依赖 BaseTask.__init__，请检查 ok-script 版本")
