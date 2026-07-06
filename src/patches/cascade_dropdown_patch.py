from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QFontMetrics
from qfluentwidgets import ComboBox
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu, MenuAnimationType


_PATCH_INSTALLED = False


class LabelAndCascadeDropDown:
    def __new__(cls, config_desc, options, config, key: str, labels=None):
        from ok import og
        from ok.gui.tasks.ConfigLabelAndWidget import ConfigLabelAndWidget

        class _Widget(ConfigLabelAndWidget):
            def __init__(self):
                super().__init__(config_desc, config, key)
                self.options = options
                self.labels = labels or {}
                self.combo_box = ComboBox()
                self.combo_box.mouseReleaseEvent = self._mouse_release_event
                self.tr_options = [og.app.tr(str(value)) for values in self.options.values() for value in values]
                self.combo_box.addItems(self.tr_options)
                self.add_widget(self.combo_box, stretch=0)
                self.update_value()
                self._fit_width()

            def _fit_width(self):
                fm = QFontMetrics(self.combo_box.font())
                max_width = max((fm.horizontalAdvance(label) for label in self.tr_options), default=160)
                self.combo_box.setFixedWidth(max_width + 50)

            def _build_menu(self):
                menu = ComboBoxMenu(self.combo_box)
                for category, values in self.options.items():
                    sub_menu = ComboBoxMenu(menu)
                    sub_menu.setTitle(og.app.tr(str(self.labels.get(category, category))))
                    current_action = None
                    for value in values:
                        text = og.app.tr(str(value))
                        action = QAction(text, sub_menu)
                        action.triggered.connect(lambda checked=False, stage=value: self.select_stage(stage))
                        sub_menu.addAction(action)
                        if value == self.config.get(self.key):
                            current_action = action
                    if current_action is not None:
                        sub_menu.setDefaultAction(current_action)
                    menu.addMenu(sub_menu)
                return menu

            def _mouse_release_event(self, event):
                self.combo_box.isPressed = False
                self.combo_box.update()
                self.show_menu()

            def show_menu(self):
                if self.combo_box.dropMenu:
                    self.combo_box._closeComboMenu()
                    return

                menu = self._build_menu()
                if menu.view.width() < self.combo_box.width():
                    menu.view.setMinimumWidth(self.combo_box.width())
                    menu.adjustSize()

                menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                self.combo_box.dropMenu = menu
                menu.closedSignal.connect(self._on_menu_closed)

                x = -menu.width() // 2 + menu.layout().contentsMargins().left() + self.combo_box.width() // 2
                drop_down_pos = self.combo_box.mapToGlobal(QPoint(x, self.combo_box.height()))
                drop_down_height = menu.view.heightForAnimation(drop_down_pos, MenuAnimationType.DROP_DOWN)

                pull_up_pos = self.combo_box.mapToGlobal(QPoint(x, 0))
                pull_up_height = menu.view.heightForAnimation(pull_up_pos, MenuAnimationType.PULL_UP)

                if drop_down_height >= pull_up_height:
                    menu.view.adjustSize(drop_down_pos, MenuAnimationType.DROP_DOWN)
                    menu.exec(drop_down_pos, aniType=MenuAnimationType.DROP_DOWN)
                else:
                    menu.view.adjustSize(pull_up_pos, MenuAnimationType.PULL_UP)
                    menu.exec(pull_up_pos, aniType=MenuAnimationType.PULL_UP)

            def _on_menu_closed(self):
                self.combo_box.dropMenu = None

            def select_stage(self, stage):
                self.update_config(stage)
                self.update_value()

            def update_value(self):
                text = og.app.tr(str(self.config.get(self.key)))
                self.combo_box.setCurrentIndex(self.combo_box.findText(text))
                self.combo_box.setText(text)

        return _Widget()


def install_cascade_dropdown_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import ok.gui.tasks.ConfigItemFactory as factory

    original_config_widget = factory.config_widget

    def patched_config_widget(config_type, config_desc, config, key, value, task):
        the_type = config_type.get(key) if config_type is not None else None
        if isinstance(the_type, dict) and the_type.get("type") == "cascade_drop_down":
            return LabelAndCascadeDropDown(config_desc, the_type["options"], config, key, the_type.get("labels"))
        return original_config_widget(config_type, config_desc, config, key, value, task)

    factory.config_widget = patched_config_widget
    _PATCH_INSTALLED = True
