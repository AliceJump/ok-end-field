# i18n 与 OCR 配置流程

返回：[文档索引](../README.md) / [README](../../README.md)

本文记录当前项目中运行时语言、模块化语言资源、OCR 文本匹配和 OCR 纠错的实际链路，适用于新增任务、补充翻译、维护 OCR 匹配文本时参考。

---

## 总体链路

```mermaid
flowchart TD
    A[ok-script executor.locale / task.locale] --> B[BaseEfTask.runtime_locale]
    B --> C[get_lang_accessor]
    C --> D[assets/lang/module/locale.json]
    D --> E[self.lang.module.key]
    E --> F[ocr / wait_ocr / wait_click_ocr]
    G[assets/ocr_fix/ocr_text_fix.json] --> H[startup_patches]
    H --> I[TaskExecutor.text_fix]
    I --> F
```

```text
ok-script executor.locale / task.locale
        ↓
BaseEfTask.runtime_locale
        ↓
get_lang_accessor(self)
        ↓
assets/lang/<module>/<locale>.json
        ↓
self.lang.<module>.<key>
        ↓
ocr / wait_ocr / wait_click_ocr / 业务比较
```

启动时还会安装 OCR 文本纠错补丁：

```text
main.py
  → install_startup_patches()
  → install_ocr_text_fix_patch()
  → assets/ocr_fix/ocr_text_fix.json 注入 ok-script TaskExecutor.text_fix
```

---

## 运行时语言来源

`BaseEfTask` 通过 `runtime_locale` 统一暴露当前 UI 语言：

1. 优先读取 `self.executor.locale`。
2. 若没有 executor，则读取 `self.locale`。
3. 支持 enum、Qt `QLocale` 风格对象和普通字符串。
4. 无法识别时交给 `get_lang_accessor(None)`，默认回退为 `zh_CN`。

相关实现：

- `src/core/BaseEfTask.py`：`_extract_locale_from_object()`、`runtime_locale`、`self.lang = get_lang_accessor(self)`。
- `src/data/lang/__init__.py`：`get_lang_accessor()`、`_normalize_locale()`、`LangAccessor`。

---

## 支持语言与资源目录

支持语言由 `i18n/<locale>/LC_MESSAGES/` 目录自动发现；如果目录不存在，则使用默认列表：

- `zh_CN`
- `zh_TW`
- `en_US`
- `ja_JP`
- `ko_KR`
- `es_ES`

模块化语言资源存放在：

```text
assets/lang/<module>/<locale>.json
```

示例：

```text
assets/lang/AutoSkipDialogTask/zh_CN.json
assets/lang/AutoSkipDialogTask/en_US.json
assets/lang/daily_battle_mixin/zh_CN.json
```

加载规则：

1. 先加载当前 locale 的 `<locale>.json`。
2. 如果当前 locale 文件不存在或读取失败，回退到 `zh_CN.json`。
3. 找不到模块或 key 时返回空模块/空值，调用处需要提供 fallback 或让测试发现缺失。

---

## 语言 JSON 节点格式

每个 key 通常使用自动生成的 `k_xxxxxxxx` 命名，节点支持三种匹配形式。

### 固定字符串

```json
{
  "k_confirm": {
    "string": "确认"
  }
}
```

访问：

```python
self.lang.common.k_confirm
```

返回值为字符串，可直接传给 `self.ocr(match=...)`。

### 正则表达式

```json
{
  "k_amount": {
    "pattern": "\\d+"
  }
}
```

访问后会编译为 `re.Pattern`，适合 OCR 数字、模糊文本、变体文本。

### 词条列表

```json
{
  "k_confirm_terms": {
    "terms": ["确认", "確定"]
  }
}
```

返回值为列表，适合多个可接受 OCR 文本。

优先级：`pattern` → `string` → `terms`。如果节点不是 dict，则原样返回。

---

## 代码中如何使用

### OCR 匹配

推荐：

```python
self.wait_ocr(match=self.lang.DeliveryTask.k_ae8fb114, time_out=5)
self.wait_click_ocr(match=self.lang.daily_battle_mixin.k_b56d9ac6, box=self.box.bottom_right)
```

不推荐：

```python
self.wait_ocr(match="确认", time_out=5)
```

新增 OCR 文本时，应先在 `assets/lang/<module>/zh_CN.json` 中添加 key，再在代码中引用 `self.lang.<module>.<key>`。

### 业务数据本地化

当业务数据仍以中文为 canonical key 时，使用工具函数转换：

- `src/data/world_map_utils.py`：`get_world_map_matcher()`、`get_world_map_text()`、`is_world_map_text()`。
- `src/data/characters_utils.py`：`get_localized_name_by_canonical()`。

这些工具会从 `assets/lang/world_map`、`assets/lang/characters` 中查找当前语言对应文本，并在缺失时回退中文。

---

## OCR 纠错配置

项目有两层 OCR 纠错机制。

### 全局 OCR 文本替换

配置文件：

```text
assets/ocr_fix/ocr_text_fix.json
```

加载位置：

```text
src/patches/ocr_text_fix_patch.py
```

启动时会把 JSON 中的 `错误文本 -> 正确文本` 注入 `ok-script` 的 `TaskExecutor.text_fix`。

适用场景：

- OCR 引擎稳定把某个完整文本识别错。
- 多个任务都会受同一个误识别影响。

示例：

```json
{
  "乾員聯絡": "幹員聯絡"
}
```

### 业务级混淆字符映射（已迁移）

> ⚠️ **旧机制 `src/data/ocr_normalize_map.py` 已移除。**
> 改用新机制：在 `assets/ocr_fix/ocr_text_fix.json` 中添加 OCR 错误→正确的文本对。

示例：

```json
{"錯誤文本": "正確文本"}
```

系统在运行时自动提取字符级混淆（如"幹"↔"乾"），并动态扩展 match pattern，无需手动写入代码映射。

- 业务解析时需要把某些字形视为等价。
- 只影响特定算法或解析流程，不适合全局替换。

---

## 新增/修改语言文本流程

```mermaid
flowchart TD
    A[确认代码模块名] --> B[更新 assets/lang/module/zh_CN.json]
    B --> C{节点类型}
    C -->|string| D[固定 OCR 文本]
    C -->|pattern| E[正则匹配]
    C -->|terms| F[多候选文本]
    D --> G[代码引用 self.lang.module.key]
    E --> G
    F --> G
    G --> H[补齐其他 locale 同名 key]
    H --> I[运行 TestCheckLang]
```

1. 确认代码模块名，例如 `DeliveryTask`、`daily_battle_mixin`、`world_map`。
2. 在 `assets/lang/<module>/zh_CN.json` 添加新 key。
3. 根据使用场景选择 `string`、`pattern` 或 `terms`。
4. 在代码中使用 `self.lang.<module>.<key>`，不要写裸中文 OCR 文本。
5. 为其他 locale 添加同名 key；可以先复制中文，后续再翻译。
6. 运行语言资源检查：

```powershell
python -m unittest tests/TestCheckLang.py
```

`TestCheckLang` 会扫描源码中的 `self.lang.<module>.k_xxx` 引用：

- 所有语言都缺失时测试失败。
- 部分语言缺失时只打印 warning，不阻止测试通过。

---

## 批量补齐翻译

辅助脚本：

```powershell
python tools/lang_batch_translate.py
```

脚本行为：

1. 扫描 `assets/lang/*/zh_CN.json`。
2. 对比 `SUPPORTED_LOCALES` 中其他 locale 的 JSON key。
3. 找出缺失 key。
4. 使用 `deep_translator.GoogleTranslator` 尝试批量翻译文本。
5. 写回目标 locale JSON。

注意：该脚本依赖网络和 `deep_translator`，翻译结果需要人工复核，尤其是游戏专有名词、正则表达式和 OCR 变体词。

---

## 常见问题

### 语言文件存在但运行时没有生效

检查：

1. `i18n/<locale>/LC_MESSAGES/` 是否存在，用于让 locale 被发现。
2. `assets/lang/<module>/<locale>.json` 是否存在。
3. JSON key 是否与代码中的 `self.lang.<module>.<key>` 完全一致。
4. 运行时 `executor.locale` 或 `task.locale` 是否为预期 locale。

### OCR 匹配在某语言下失败

优先检查语言 JSON：

- 固定按钮文本用 `string`。
- 有繁简、空格、数字或 OCR 变体时用 `pattern`。
- 多个可接受词时用 `terms`。

如果 OCR 原始结果本身稳定错误，再考虑加入 `assets/ocr_fix/ocr_text_fix.json`。

### 什么时候用 `zh_CN` 兜底

`zh_CN` 是 canonical fallback。任何 locale 缺文件或读文件失败时，语言访问器都会回退到 `zh_CN`，保证任务不会因为缺翻译直接崩溃。

### 是否还需要 `target_doc/i18n_ocr.md`

历史草稿状态页：[target_doc/i18n_ocr.md](../../target_doc/i18n_ocr.md)。正式流程以本文为准。
