"""给任务配置加一个「带风险确认的开关」类型。

``ok`` 框架的 ``LabelAndSwitchButton`` 在 ``check_changed`` 里直接 ``update_config``，
没有可拦截的钩子，所以高风险开关只能自己实现：勾选时弹一次必须等满 5 秒才能确认的警告，
取消则不写入配置。

用法（任务侧）::

    self.default_config["使用实验技术"] = False
    self.config_type["使用实验技术"] = {"type": "danger_switch"}

正文/标题/按钮都是固定的 gettext 文案；倒计时用静态模板加 ``.format``，不把运行时数据
拼进 ``tr()``。
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from qfluentwidgets import (
    BodyLabel,
    FluentStyleSheet,
    IndicatorPosition,
    MessageBoxBase,
    SubtitleLabel,
    SwitchButton,
)

_PATCH_INSTALLED = False

#: 确认键必须等待的秒数
CONFIRM_DELAY_SECONDS = 5

#: 弹窗文案（gettext msgid 必须与这里的字面量逐字一致）
TITLE_ENABLE = "启用实验技术"
WARNING_BODY = (
    "该技术通过抓取游戏网络流量解析角色坐标，可能违反游戏服务条款，存在账号被封禁的风险。请确认你已了解并自行承担后果。"
)
CONFIRM_BUTTON = "我已知晓风险"
COUNTDOWN_TEMPLATE = "请等待 {seconds} 秒"


def _tr(text: str) -> str:
    from ok import og

    return og.app.tr(text)


def _warn_modal(parent) -> bool:
    """弹出风险警告；返回 True 表示用户等满并确认。

    确认键在前 :data:`CONFIRM_DELAY_SECONDS` 秒内禁用，按钮文字显示剩余秒数，
    避免用户不看清内容就一路点过去。
    """

    dlg = MessageBoxBase(parent)
    dlg.setWindowTitle(_tr(TITLE_ENABLE))
    with contextlib.suppress(Exception):
        FluentStyleSheet.DIALOG.apply(dlg)

    title = SubtitleLabel(_tr(TITLE_ENABLE), dlg)
    body = BodyLabel(_tr(WARNING_BODY), dlg)
    body.setWordWrap(True)
    dlg.viewLayout.addWidget(title)
    dlg.viewLayout.addWidget(body)

    remaining = CONFIRM_DELAY_SECONDS
    dlg.yesButton.setText(_tr(COUNTDOWN_TEMPLATE).format(seconds=remaining))
    dlg.yesButton.setEnabled(False)
    dlg.yesButton.clicked.connect(dlg.accept)
    dlg.cancelButton.clicked.connect(dlg.reject)

    # 计时器挂在弹窗上，弹窗销毁即随之销毁；再判一次可见性，避免关闭后空转。
    timer = QTimer(dlg)
    timer.setInterval(1000)

    def _tick() -> None:
        nonlocal remaining
        if not dlg.isVisible():
            timer.stop()
            return
        remaining -= 1
        if remaining > 0:
            dlg.yesButton.setText(_tr(COUNTDOWN_TEMPLATE).format(seconds=remaining))
            return
        timer.stop()
        dlg.yesButton.setText(_tr(CONFIRM_BUTTON))
        dlg.yesButton.setEnabled(True)

    timer.timeout.connect(_tick)
    timer.start()
    return bool(dlg.exec())


def _notify_confirmed(task, key: str) -> None:
    """把「用户确认打开了这个高风险开关」告诉任务，让它做后续动作（例如预下载）。

    鸭子类型 + 全部包在 try 里：预下载只是锦上添花，任何失败都不该影响开关本身生效，
    也不该让一个任务没实现这个钩子就报错。
    """

    if task is None:
        from src.localization.position_service import get_world_pose_service

        task = get_world_pose_service()

    hook = getattr(task, "on_danger_switch_confirmed", None)
    if not callable(hook):
        return
    # 钩子是可选增强，失败静默
    with contextlib.suppress(Exception):
        hook(key)


class LabelAndDangerSwitch:
    """项目自建的 ``danger_switch`` 配置控件。"""

    def __new__(cls, config_desc, config, key: str, task=None):
        from ok.ui.qt.tasks.ConfigLabelAndWidget import ConfigLabelAndWidget

        class _Widget(ConfigLabelAndWidget):
            def __init__(self):
                super().__init__(config_desc, config, key)
                self.switch_button = SwitchButton(indicatorPos=IndicatorPosition.RIGHT)
                # 与框架自带的 LabelAndSwitchButton 用同一组文案，避免多出一套"是/否"，
                # 也让这个开关和其他开关看起来一致。
                self.switch_button.setOnText(_tr("Yes"))
                self.switch_button.setOffText(_tr("No"))
                self.update_value()
                self.switch_button.checkedChanged.connect(self.on_toggled)
                self.add_widget(self.switch_button, stretch=0)

            def update_value(self):
                value = bool(self.config.get(self.key))
                if self.switch_button.isChecked() != value:
                    self.switch_button.setChecked(value)

            def on_toggled(self, checked: bool):
                # 程序性同步（初始化、外部改配置）也会走到这里：状态与配置一致时不做事，
                # 否则打开任务卡片就会无故弹窗。
                if bool(checked) == bool(self.config.get(self.key)):
                    return
                if not checked:
                    self.update_config(False)
                    return
                parent = QApplication.activeWindow() or self.window()
                if _warn_modal(parent):
                    self.update_config(True)
                    _notify_confirmed(task, self.key)
                    return
                # 用户没确认：开关回弹到关，且不写配置。
                self.update_value()

        return _Widget()


def install_danger_switch_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import ok.ui.qt.tasks.ConfigItemFactory as factory

    original_config_widget = factory.config_widget

    def patched_config_widget(config_type, config_desc, config, key, value, task):
        the_type = config_type.get(key) if config_type is not None else None
        if isinstance(the_type, dict) and the_type.get("type") == "danger_switch":
            return LabelAndDangerSwitch(config_desc, config, key, task)
        return original_config_widget(config_type, config_desc, config, key, value, task)

    factory.config_widget = patched_config_widget
    _PATCH_INSTALLED = True
