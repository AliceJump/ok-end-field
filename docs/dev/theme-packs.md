# OKEF 外部主题包（初版）

主题包是一个 **ZIP**，根目录恰好包含 `theme.json` 和 `style.qss` 两个 UTF-8 文件；不能带脚本或第三方资源。示例源码见 `theme-packs/aurora-night/`。主题**仅改变 GUI 的 Qt 样式**，不修改游戏、任务、补丁或 `ok-script` 代码。

在包含本功能的 OKEF 版本中，打开侧边栏底部的「主题」页 →「导入主题 ZIP 并应用」→ 选择 `aurora-night.okef-theme.zip`。主题立即应用、下次启动自动恢复；点「恢复默认主题」会清除选择并还原本次启动的原始样式。未安装主题时行为保持原样。

`theme.json` 格式：

```json
{
  "schema_version": 1,
  "id": "aurora-night",
  "name": "极光之夜 · Aurora Night",
  "version": "1.0.0"
}
```

`id` 限小写 ASCII 字母开头，其余小写字母、数字、下划线与连字符，总长最多 64；名称/版本是最多 80 字符的非空字符串。ZIP 不提取到磁盘，只按 `id` 复制至 `configs/themes/<id>.zip`，选择保存在 `configs/themes/active.json`。安装是原子替换；导入后需要显式选择才能激活。包最多 512 KiB，QSS 最多 128 KiB，清单最多 16 KiB。QSS 禁止 `url(...)` 与 `@import`，不允许网络和本地文件引用。

这是轻量 QSS 装饰，不保证覆盖上游 qfluentwidgets 具有更高优先级的每个控件样式，也不支持动态图形、主题脚本、自动适配明暗模式和资源引用。未来这些能力应单独评审。
