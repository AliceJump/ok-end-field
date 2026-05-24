# Locale Data Layer

## 目标

将 OCR 关键词、正则 token、名称映射、简繁兼容等语言差异从业务代码剥离到按 locale 分组的数据层。

## 目录

- `src/data/lang/runtime_locale.py`：运行时 locale 解析与规范化
- `src/data/lang/locale_data.py`：统一数据中间层访问接口
- `src/data/lang/locales/zh_cn.py`：简体中文数据集
- `src/data/lang/locales/zh_tw.py`：繁体中文数据集

## 使用规范

1. 业务代码不直接硬编码语言文本（包括 OCR match 和 regex literal）。
2. 业务代码通过 `src.data.lang` 导出的函数按 key 读取当前语言数据。
3. locale 来源统一走运行时上下文（`context`，通常传 `self`），由中间层自动解析。
4. 新增语言时，先补一整套 locale 数据文件，再接入到 `locale_data.py`。

## 已接入示例

- OCR confusion map（姓名匹配）
- 仓库切换 OCR 关键词与地点文本
- 自动拾取白/黑名单关键词
- 逗号分隔解析符号（中英文逗号）
- 物品名称到模板 key 的映射
