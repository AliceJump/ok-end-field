"""任务卡悬停参数概要弹层注入。

把 ok-script-toolkit 控制台的悬停参数概要弹层（分组吸收显隐版）移植到
ok-end-field 的 Qt GUI：悬停整张任务卡 500ms 弹出参数概要浮层，向右展开，
无按钮注入（对齐扩展 showHoverPop 交互）。

设计文档：ok-script-toolkit/.workbuddy/design/eye-popup-design.md
归属模型：src/core/param_preview_model.py（纯函数，单测见 TestParamPreviewModel）
弹层实现：src/gui/param_preview_popup.py
"""

from __future__ import annotations

import contextlib

from ok import Logger
from PySide6.QtCore import QTimer

logger = Logger.get_logger(__name__)

_PATCH_INSTALLED = False


def install_task_param_preview_patch():
    """包装 TaskCard.__init__：构建完成后按需安装悬停弹出。"""
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.Communicate import communicate
    from ok.ui.qt.tasks.TaskCard import TaskCard

    from src.gui.param_preview_popup import (
        ParamPreviewController,
        install_param_preview_hover,
    )

    original_init = TaskCard.__init__

    def _install_hover_after_mount(card):
        try:
            install_param_preview_hover(card)
        except Exception as e:
            # 弹层注入失败不能影响任务卡本身；框架 Logger 没有 exc_info 参数
            logger.error("param preview hover install failed", exception=e)

    def __init__(self, task, onetime):
        original_init(self, task, onetime)
        # TaskCard 此时可能还未挂到列表：下一轮事件循环再装，才能找到
        # 祖先滚动区与主窗口，给它们也安装收起事件过滤器。
        QTimer.singleShot(0, self, lambda: _install_hover_after_mount(self))

    def _on_task_refresh(*_args):
        # 任务状态刷新（启停/运行态）时收起弹层，避免钉在旧内容上
        with contextlib.suppress(Exception):
            ParamPreviewController.hide()

    TaskCard.__init__ = __init__
    with contextlib.suppress(Exception):
        communicate.task.connect(_on_task_refresh)

    _PATCH_INSTALLED = True
