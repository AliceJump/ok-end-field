from __future__ import annotations

_PATCH_INSTALLED = False


def install_conditional_rotation_patch():
    """把 config_type 里 type=="cond_sequence_editor" 的 key 渲染为动作列表编辑按钮。

    复用 ok.gui.tasks.ConfigItemFactory.config_widget 的 wrap 机制（与
    cascade_dropdown_patch 同款）：ok 的 ConfigItemFactory 无自定义控件类型
    （未知 type 直接 raise），编辑器无法通过标准 config_type 渲染，故在此
    monkey-patch config_widget，遇到该 type 返回项目自建的
    ConditionalRotationPanel（绑当前 config），其余转发原实现。

    注意：本 patch 与 cascade_dropdown_patch 都 wrap config_widget，按
    startup_patches.py 中的安装顺序链式嵌套，转发链保持完整。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    import ok.gui.tasks.ConfigItemFactory as factory

    from src.gui.ConditionalRotationPanel import ConditionalRotationPanel

    original_config_widget = factory.config_widget

    def patched_config_widget(config_type, config_desc, config, key, value, task):
        the_type = config_type.get(key) if config_type is not None else None
        if isinstance(the_type, dict) and the_type.get("type") == "cond_sequence_editor":
            return ConditionalRotationPanel(config=config)
        return original_config_widget(config_type, config_desc, config, key, value, task)

    factory.config_widget = patched_config_widget
    _PATCH_INSTALLED = True
