"""声明式动态配置渲染补丁。

LabelAndDropDown（ok-script 框架）会对下拉的每个 option 和当前值调用
og.app.tr()，导致用户输入载入的动态值（如账号名 *0705）进入 i18n 收集池。
本补丁让已声明为动态配置的 key（见 src/core/dynamic_config_keys.py）
跳过翻译，直接显示原值。
"""

from __future__ import annotations

from functools import wraps

from src.core.dynamic_config_keys import is_dynamic_dropdown_key

_PATCH_INSTALLED = False


def install_dynamic_config_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.tasks.LabelAndDropDown import LabelAndDropDown

    original_init = LabelAndDropDown.__init__
    original_update_value = LabelAndDropDown.update_value

    @wraps(original_init)
    def init_respect_dynamic(self, config_desc, options, config, key: str):
        self._dynamic_values = is_dynamic_dropdown_key(key)
        if self._dynamic_values:
            # 动态配置：option 原样展示，不经过 app.tr（不进收集池）
            original_init(self, config_desc, [], config, key)
            self.tr_dict = {}
            self.tr_options = []
            for option in options:
                text = str(option)
                self.tr_options.append(text)
                self.tr_dict[text] = option
            self.combo_box.clear()
            self.combo_box.addItems(self.tr_options)
            self.combo_box.setCurrentIndex(
                self.tr_options.index(str(self.config.get(self.key)))
                if str(self.config.get(self.key)) in self.tr_options
                else -1
            )
            return
        original_init(self, config_desc, options, config, key)

    @wraps(original_update_value)
    def update_value_respect_dynamic(self):
        if getattr(self, "_dynamic_values", False):
            value = str(self.config.get(self.key))
            self.combo_box.setText(value)
            self.combo_box.setCurrentIndex(self.tr_options.index(value) if value in self.tr_options else -1)
            return
        original_update_value(self)

    LabelAndDropDown.__init__ = init_respect_dynamic
    LabelAndDropDown.update_value = update_value_respect_dynamic
    _PATCH_INSTALLED = True
