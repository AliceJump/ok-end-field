# 安装

## 系统要求

- 操作系统：Windows 10 或 Windows 11。
- 游戏分辨率：要求 16:9，1920×1080（1080P）及以上；低于 1080P 不保证正常运行。
- 运行路径：建议使用不含中文和特殊权限限制的目录。
- 游戏语言：当前部分识别功能仅支持简体中文。

## 使用安装包

这是大多数用户推荐的方式。请在 [GitHub Releases](https://github.com/AliceJump/ok-end-field/releases) 下载最新的 `setup.exe` 安装包，而不是 `Source Code` 源码压缩包。

网络访问 GitHub 不稳定时，可使用 [Mirror酱](https://mirrorchyan.com/zh/projects?rid=ok-end-field&source=ok-ef-readme)、[百度网盘](https://pan.baidu.com/s/1rxLRLkSx34xIL-nGib04sg?pwd=479z) 或 [夸克网盘](https://pan.quark.cn/s/418018ddf7a0)。

安装完成后，从桌面快捷方式或开始菜单启动 `ok-ef`。

## 从源码运行

源码运行适合二次开发和调试。完整过程见[从源码运行](../../dev/QUICKSTART.md)。

```powershell
git clone --recurse-submodules https://github.com/AliceJump/ok-end-field.git
Set-Location ok-end-field
uv sync
uv run python main.py
```

!!! warning "权限与安全软件"
    建议以管理员权限运行程序，使其与游戏窗口权限一致。若程序无法启动或文件被删除，请将安装目录加入安全软件信任列表。