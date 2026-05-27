重构目标：建立统一 runtime locale → self.lang → OCR/文本系统 的完整语言链路。在完全完成前不要停止，不要只完成部分替换。

1. 统一运行时语言来源

统一通过 `executor.locale -> obj.locale` 获取当前 UI 显示语言，作为全项目唯一语言来源。

代码示例：

```python
from typing import Any


def _extract_locale_from_object(obj: Any) -> str | None:
    """统一获取运行时 UI 语言。"""

    if obj is None:
        return None

    # 优先使用 executor.locale
    executor = getattr(obj, "executor", None)

    locale_obj = (
        getattr(executor, "locale", None)
        if executor is not None
        else getattr(obj, "locale", None)
    )

    if locale_obj is None:
        return None

    # 支持 enum / QLocale / 自定义 Locale 类
    if hasattr(locale_obj, "name"):
        try:
            name_attr = getattr(locale_obj, "name")
            value = name_attr() if callable(name_attr) else name_attr

            if value:
                return str(value)

        except Exception:
            pass

    return str(locale_obj)
```

BaseEfTask 内统一暴露 runtime locale：

```python
class BaseEfTask:
    @property
    def runtime_locale(self) -> str | None:
        return _extract_locale_from_object(self)
```

使用示例：

```python
locale = self.runtime_locale

# zh_CN / zh_TW / en_US
print(locale)
```

2. 统一 self.lang 语言系统

初始化 `self.lang` 时，根据 runtime locale 自动加载对应模块语言文件。

语言资源必须按“模块”拆分，而不是按语言拆目录：

```text
assets/lang/
├── common/
│   ├── zh_CN.json
│   ├── zh_TW.json
│   └── en_US.json
├── battle/
│   ├── zh_CN.json
│   ├── zh_TW.json
│   └── en_US.json
```

3. 统一语言访问方式

所有 OCR、matcher、regex、UI 文本统一通过 `self.lang` 访问：

```python
self.ocr(match=self.lang.common.confirm)

re.compile(self.lang.common.confirm.pattern)

self.click_text(self.lang.common.start.string)
```

`self.lang.xxx` 必须返回语言节点对象，而不是裸字符串：

```python
self.lang.common.confirm.string
self.lang.common.confirm.pattern
self.lang.common.confirm.terms
```

JSON 结构统一：

```json
{
  "confirm": {
    "string": "确认",
    "pattern": "(确认|確定)",
    "terms": [
      "确认"
    ]
  }
}
```

4. OCR/matcher 自动 matcher 构建

OCR/matcher 内部必须自动构建 matcher，遵循“有什么就用什么”的早返回原则：

```python
def build_matcher(node):
    if node is None:
        return None

    if getattr(node, "pattern", None):
        return re.compile(node.pattern)

    if getattr(node, "string", None):
        return node.string

    if getattr(node, "terms", None):
        return node.terms

    return None
```

例如：

```python
self.ocr(match=self.lang.common.confirm)
```

内部自动处理：

```text
pattern -> regex
string  -> text
terms   -> list matcher
```

业务层禁止继续手动区分：

* regex
* string
* terms
* OCR matcher 类型

5. 必须完成的迁移范围

必须全量扫描并替换：

* OCR 裸字符串
* `re.compile("中文")`
* 硬编码 UI 文本
* `lang_ocr`
* 多套 locale/language 状态
* 旧式 OCR 文本入口

禁止新增：

* 裸字符串 OCR
* 新旧双语言系统
* 临时兼容层
* 新的 language alias

6. 完成标准

不要只完成框架或部分模块。

必须持续执行直到：

* runtime locale 全链路统一
* self.lang 全链路接管
* OCR/matcher 全部切换
* 旧语言入口全部移除
* 所有模块完成迁移
* 不再存在旧式语言访问方式

未完成全部迁移前不要停止。
