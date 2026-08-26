from qfluentwidgets import FluentIcon

from src.core.BaseEfTask import BaseEfTask


class I18nTemplateTestTask(BaseEfTask):
    """gettext 双层翻译实验：验证「模板 tr + 内层值 tr + format」的呈现结果。

    预期（以 en_US 界面为例）：
        外层 self.tr("测试模板: {value}") 命中 po → "Test template: {value}"
        内层 self.tr("原始值")            命中 po → "Original Value"
        format 后日志输出 → "Test template: Original Value"

    对照判读：
        输出 "测试模板: Original Value"   → 外层 msgid 未命中（po 未加载/未编译）
        输出 "Test template: {value}"     → 忘记 format（占位符原样漏出）
        输出 "测试模板: 原始值"           → 两层都未命中
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.tr("i18n 模板实验")
        self.group_name = "测试"
        self.group_icon = FluentIcon.DEVELOPER_TOOLS
        self.description = self.tr("验证 gettext 模板与占位符的双层翻译行为")
        self.visible = self.debug

    def run(self):
        self.log_info(self.tr("测试模板: {value}").format(value=self.tr("原始值")))
