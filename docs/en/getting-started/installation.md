# Installation

## System requirements

- Operating system: Windows 10 or Windows 11.
- Game resolution: 16:9 at 1920×1080 (1080P) or above is required; below 1080P is not guaranteed to work.
- Installation path: use a directory without Chinese characters or restrictive special permissions.
- Game language: some recognition features currently support Simplified Chinese only.

## Use the installer

The installer is recommended for most users. Download the latest `setup.exe` from [GitHub Releases](https://github.com/AliceJump/ok-end-field/releases), not the source-code archive.

If GitHub is unavailable, use [MirrorChyan](https://mirrorchyan.com/zh/projects?rid=ok-end-field&source=ok-ef-readme), [Baidu Netdisk](https://pan.baidu.com/s/1rxLRLkSx34xIL-nGib04sg?pwd=479z), or [Quark Drive](https://pan.quark.cn/s/418018ddf7a0).

After installation, start `ok-ef` from the desktop shortcut or Start menu.

## Run from source

Running from source is intended for contributors, modifications, and debugging.

```powershell
git clone --recurse-submodules https://github.com/AliceJump/ok-end-field.git
Set-Location ok-end-field
uv sync
uv run python main.py
```

See [Running from source](../../dev/QUICKSTART.md) for development and verification details.

!!! warning "Permissions and security software"
    Run the application at the same privilege level as the game. If files are blocked or deleted, add the installation directory to your security software's trusted list.