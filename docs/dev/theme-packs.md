# 外部 QSS / 奶龙主题包

从 OKEF 主题管理页导入 ZIP。普通 QSS 包需要 `theme.json` 和 `style.qss` 两个 UTF-8 文件；`kind: "nailong"` 的素材包还必须包含 `assets/background.webp`、`assets/standing.webp` 和 `assets/working.webp`。不会执行主题包代码或展开任意文件。

## 奶龙主题示例

[源码与具体打包操作](../../theme-packs/nailong/README.md)。示例 ZIP 已放在 `theme-packs/nailong/nailong.okef-theme.zip`，并且可以在仓库根目录下用下面的命令重新生成（保留 ZIP 内文件路径）：

```sh
python scripts/themes/build_nailong_theme.py
```

导入必须使用本功能分支编译的 OKEF；旧版软件没有「奶龙主题」入口。启用后主窗口会持续显示追视的大脸背景；切换任务、配置、设置等页面仍能看到动态眼睛，右下角也会显示随真实任务运行状态变化的小奶龙。主题背景不拦截鼠标点击，关闭主题后恢复默认页面外观。用户可以一键清除主题，恢复原有 Qt 样式。

普通 QSS 包的 `theme.json` 格式：

```json
{"schema_version": 1, "id": "sample", "name": "My Theme", "version": "1.0"}
```

名称、ID 和版本有长度限制；ZIP 拒绝额外未知文件、目录穿越、重复成员、特殊 ZIP 压缩方法、符号链接、超过大小限制或包含 `qproperty-`、`url(...)`、`@import` 或 Qt 反斜杠转义 的样式。外部主题是**纯数据**而不是 Python 插件。更复杂的交互必须由 OKEF 主程序受控实现。
