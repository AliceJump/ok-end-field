# Material Icons

https://fonts.google.com/icons?icon.size=256&icon.color=%23FFFFFF

## 支持的格式

- `.svg` / `.png`：静态图标（默认）
- `.gif`：动图图标，渲染时自动播放动画（`src/icons.py` 中 `ThemeIcon(..., suffix=".gif")` 或 `GifIcon(path)`）

## 命名约定

`ThemeIcon(light_icon, dark_icon, suffix=...)` 按主题加载文件：

- 浅色主题：`{light_icon}{suffix}`（如 `swords_black.svg`）
- 深色主题：`{dark_icon}{suffix}`（如 `swords_white.svg`）

新增 GIF 图标时同样准备黑白两份文件（如 `xxx_black.gif` / `xxx_white.gif`），
在 `src/icons.py` 的 `Icons` 类中声明即可，无需改任何渲染代码。

## 单变体自动反转（可选）

只传一个变体时，另一个变体自动取其“颜色反转”版本，无需准备第二份文件：

```python
ThemeIcon("xxx_black", suffix=".gif")  # 深色版自动 = 浅色版反色
ThemeIcon(dark_icon="xxx_white", suffix=".svg")
```

- 反转结果写入根目录 `cache/icons/`（已在 `.gitignore` 中忽略），文件名
  `{源名}_inv_{源路径md5前8位}{后缀}`，避免不同目录同名冲突。
- 缓存会持久化：GIF 逐帧反转代价高，首次生成后后续启动直接读缓存（“秒加载”）；
  源文件 mtime 新于缓存时自动重建（先写 `.tmp` 再原子替换）。
- 各格式反转规则：SVG 反转全部十六进制颜色（`#000000`→`#ffffff`，保留 alpha）；
  PNG/GIF 反转 RGB 通道并保留透明通道；GIF 保留每帧时长与循环次数。
- 源文件缺失时不崩溃，回落到源路径（渲染为空）并记 warning 日志。
