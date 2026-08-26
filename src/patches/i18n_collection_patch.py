from __future__ import annotations

import re
from functools import wraps


_PATCH_INSTALLED = False

# 运行时动态内容特征：命中任意一条即不进入 i18n 收集池。
# 背景：GUI 渲染层（配置下拉框当前值、任务信息卡、计划任务名等）会对动态值
# 调用 og.app.tr()，App.tr 会把参数收进 to_translate（仅 debug 模式启用）；
# 点「生成 i18n 文件」时导出为 po 条目，导致账号名、正则对象、进度日志等
# 污染翻译目录。框架（ok-script 2.0.4）没有单条豁免参数，故在 tr 入口统一过滤。
_DYNAMIC_PATTERNS = (
    re.compile(r"^Log\("),              # 任务日志标签，如 Log(0705)
    re.compile(r"^re\.compile\("),      # re.Pattern 对象被 str() 的产物
    re.compile(r"^\d+$"),               # 纯数字
    re.compile(r"^[^\w\s]*\d+$"),       # 前导符号+数字，如 *0705、#123
    re.compile(r"\(\d{3,}\)"),          # 括号包裹的长数字（账号尾号等），
                                        # 如 开始第 1/3 个账号(0705)任务执行
)


def is_dynamic_text(value) -> bool:
    """判断字符串是否为运行时动态内容（不应进入 i18n 收集池）。"""
    if not isinstance(value, str):
        return True
    return any(pattern.search(value) for pattern in _DYNAMIC_PATTERNS)


def install_i18n_collection_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok import App

    original_tr = App.tr

    @wraps(original_tr)
    def tr_without_dynamic_content(self, key):
        translated = original_tr(self, key)
        if self.to_translate is not None and is_dynamic_text(key):
            self.to_translate.discard(key)
        return translated

    App.tr = tr_without_dynamic_content
    _PATCH_INSTALLED = True
