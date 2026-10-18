# 奶龙主题（OKEF / PySide6）

本示例以 [MaaEnd #6561 奶龙主题](https://github.com/MaaEnd/MaaEnd/pull/6561) 为参考，使用其仓库中现有的奶龙形象资源改编为适用于 OKEF Qt 的资源包：去瞳孔的大脸背景、站立奶龙以及工作动图。它不是官方授权的奶龙产品。上游资源见 [MaaEnd](https://github.com/MaaEnd/MaaEnd/tree/821d6399f565e7a3b4b5f844fa86bf11e0a45e38/tools/client/nailong/assets)，遵循其 AGPL-3.0 许可；改编代码和来源应保留相应版权与许可信息。

## 立即导入

运行 **包含 PR #555 的 OKEF**（当前正式版不支持），在侧边栏底部选择「奶龙主题」→「导入主题 ZIP 并应用」，选择同目录的 [nailong.okef-theme.zip](./nailong.okef-theme.zip)。主题页可见大脸和追视瞳孔；Qt 使用全局指针位置，鼠标移出窗口仍可追视。主窗口右下角显示奶龙陪伴角色，执行器存在运行中的真实任务时显示工作形象。退出重进会保持选择；「恢复默认主题」恢复标准外观。

也可以从仓库根目录使用打包脚本重新生成（保留 ZIP 内的 assets/ 目录层级）：

```sh
python scripts/themes/build_nailong_theme.py
```

ZIP 的根目录必须包含 `theme.json`、`style.qss` 和 `assets/background.webp`、`assets/standing.webp`、`assets/working.webp`；不可把整个 `nailong/` 文件夹作为 ZIP 的顶层目录。

## 功能边界

- `theme.json` 的 `kind: "nailong"` 代表额外加载图片素材；不运行任何 ZIP 中的脚本，资源始终留在 ZIP 里。
- 背景大脸使用固定尺寸 1448×1086 的原图眼睛位置，按 `cover` 裁切并把黑瞳孔画在独立 Qt 图层；每 50ms 在可见时检查鼠标坐标，隐藏页面会停止追视。
- 任务状态来自 `TaskExecutor.current_task` 和 `paused`，不是演示假状态；主窗口右下角小奶龙不拦截鼠标点击。工作 WebP 能否逐帧播放取决于 Qt 平台插件；不支持时自动显示第一帧。
- 样式文件不能使用 `qproperty-`、`url(...)`、`@import` 或反斜杠转义，以免控制 Qt 控件状态或读外部资源。主题无效时默认外观仍可恢复。
- 本版不处理气泡语音与复杂 Live2D 骨骼；如需额外的交互动作与可配置动画，应该在后续独立修改中实现。

## 授权与来源

原始奶龙图像资源来自 MaaEnd 项目 PR #6561，MaaEnd 项目使用 AGPL-3.0。贡献/修改时应遵循该许可证，并保留源项目版权信息和源码提供途径。
