"""任务「使用说明」富文本混入。

ok 库把任务的 ``instructions`` 渲染到任务卡片上的「使用说明」按钮里（Qt RichText），
本模块把这类说明集中成一处：延迟构建、追加而非覆盖任务原有说明，
并提供统一的富文本行构造工具。

约定（与 ``$ok-script-i18n`` 一致）：
- emoji、树形符号（``└─``/``├─``）、HTML 标签与颜色留在代码里拼接；
- 只有需要翻译的纯文本经 ``self.tr()`` 走 gettext（msgid 写入 ``i18n/*/LC_MESSAGES/ok.po``）；
- 翻译输入必须是稳定模板，运行时数据用 ``.format(...)`` 注入，禁止把运行时值喂给 ``tr()``。
"""


def inst_line(text: str, color: str = "", *, bold: bool = False, indent: int = 0):
    """构造一行富文本：可选缩进、加粗与颜色。"""
    content = f"{'&nbsp;' * (indent * 4)}{text}"
    if bold:
        content = f"<strong>{content}</strong>"
    return f'<span style="color:{color};">{content}</span>'


def inst_gap():
    """构造一个细小的垂直间隔。"""
    return '<span style="font-size:4px;">&nbsp;</span>'


class InstructionsMixin:
    """为任务提供延迟构建的富文本使用说明。

    子类实现 :meth:`build_instructions` 返回说明片段，本混入负责把它追加到任务原有说明之后。

    类级默认值用于规避 ``__init__`` 赋值顺序问题：ok 库 ``BaseTask.__init__`` 会执行
    ``self.instructions = None``，此时 setter 只缓存基础说明，真正的片段在首次读取时构建。
    """

    _instructions_dirty = True
    _instructions_base = None

    @property
    def instructions(self):
        if self._instructions_dirty:
            self._instructions_dirty = False
            base = self._instructions_base
            extra = self.build_instructions()
            self._instructions_base = f"{base}<br><br>{extra}" if base else extra
        return self._instructions_base

    @instructions.setter
    def instructions(self, value):
        # ok 库 BaseTask.__init__ 会执行 self.instructions = None，这里仅缓存基础说明
        self._instructions_base = value
        self._instructions_dirty = True

    def build_instructions(self) -> str:
        """返回本任务追加的富文本说明片段；子类必须实现。"""
        raise NotImplementedError
