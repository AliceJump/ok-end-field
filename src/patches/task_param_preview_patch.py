"""任务卡「小眼睛」参数概要弹层注入。

把 ok-script-toolkit 控制台的悬停参数概要弹层（分组吸收显隐版）移植到
ok-end-field 的 Qt GUI：每张任务卡标题行最右侧加小眼睛（FluentIcon.VIEW），
悬停 500ms 或点击弹出与扩展一致的参数概要浮层，向右展开。

设计文档：ok-script-toolkit/.workbuddy/design/eye-popup-design.md
归属模型：src/core/param_preview_model.py（纯函数，单测见 TestParamPreviewModel）
弹层实现：src/gui/param_preview_popup.py
"""

from __future__ import annotations

import contextlib

from ok import Logger

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False


def install_task_param_preview_patch():
    """包装 TaskCard.__init__：构建完成后按需注入小眼睛。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.Communicate import communicate
    from ok.ui.qt.tasks.TaskCard import TaskCard

    from src.gui.param_preview_popup import (
        ParamPreviewController,
        inject_param_preview_eye,
    )

    original_init = TaskCard.__init__

    def __init__(self, task, onetime):
        original_init(self, task, onetime)
        try:
            inject_param_preview_eye(self)
        except Exception:
            # 弹层注入失败不能影响任务卡本身
            logger.warning("param preview eye inject failed", exc_info=True)

    def _on_task_refresh(*_args):
        # 任务状态刷新（启停/运行态）时收起弹层，避免钉在旧内容上
        with contextlib.suppress(Exception):
            ParamPreviewController.hide()

    TaskCard.__init__ = __init__
    with contextlib.suppress(Exception):
        communicate.task.connect(_on_task_refresh)

    _PATCH_INSTALLED = True
