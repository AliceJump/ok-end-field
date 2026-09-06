"""配置导出/导入补丁。

背景
----
框架没有提供配置目录的导出与导入入口。本补丁通过猴子补丁在 StartTab 的
调试按钮行追加"导出配置/导入配置"两个按钮：

- 导出：把 configs 目录打包为 zip（条目以 configs/ 为根）保存到下载目录；
- 导入：既支持点击按钮选择 zip 文件，也支持把 zip 直接拖入 GUI 窗口。
  导入前会把现有配置备份到 configs/backup/import_backup_<时间戳>/，
  覆盖完成后提示重启应用生效。

配置目录由 ok.util.config.Config 以 cwd 相对路径 `configs` 解析，且配置对象
在启动时读入内存、修改时写回磁盘，因此导入后必须重启应用才能生效。

导出/导入均跳过 backup 与 global_config_migration_backup 两个备份目录，
避免把备份产物当作配置反复打包。
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from PySide6.QtWidgets import QApplication, QFileDialog
from qfluentwidgets import FluentIcon, MessageBox, PushButton

_PATCH_INSTALLED = False

# 备份/迁移产物目录不属于用户配置，导出、备份与导入清理时均跳过
_EXCLUDED_DIR_NAMES = {"backup", "global_config_migration_backup"}

# 导出 zip 内的根目录名；导入时优先识别该前缀
_ZIP_ROOT_DIR = "configs"


def get_configs_dir() -> Path:
    """返回当前应用的配置目录（与 ok.util.config.Config 的解析方式一致）。"""
    return Path.cwd() / "configs"


def _is_excluded(rel_parts) -> bool:
    return any(part in _EXCLUDED_DIR_NAMES for part in rel_parts)


def export_config_zip(configs_dir: Path, zip_path: Path) -> int:
    """把配置目录打包为 zip（条目以 configs/ 为根），返回导出的文件数。"""
    configs_dir = Path(configs_dir).resolve()
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(configs_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(configs_dir)
            if _is_excluded(rel.parts):
                continue
            zf.write(path, Path(_ZIP_ROOT_DIR) / rel)
            count += 1
    return count


def resolve_import_prefix(zip_path: Path) -> str | None:
    """解析 zip 内配置文件的公共前缀。

    返回 "" 表示条目在 zip 根部，返回 "xxx/" 表示条目在某个目录下，
    返回 None 表示不是合法的配置压缩包（无法解析、含 ".." 路径段或
    没有 .json 文件）。前缀在导入时会被剥离，条目直接落到配置目录。
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return None

    for name in names:
        if ".." in PurePosixPath(name).parts:
            return None

    json_names = [name for name in names if name.lower().endswith(".json")]
    if not json_names:
        return None

    root_prefix = f"{_ZIP_ROOT_DIR}/"
    if any(name.startswith(root_prefix) for name in json_names):
        return root_prefix

    top_dirs = {name.split("/", 1)[0] for name in names if "/" in name}
    if len(top_dirs) == 1:
        prefix = f"{next(iter(top_dirs))}/"
        if any(name.startswith(prefix) for name in json_names):
            return prefix
    return ""


def apply_config_import(zip_path: Path, configs_dir: Path) -> Path:
    """把 zip 中的配置覆盖到配置目录，返回旧配置的备份目录。

    备份与清理都会跳过 _EXCLUDED_DIR_NAMES 中的目录；解压时拒绝越出
    配置目录的条目（防 zip-slip）。
    """
    prefix = resolve_import_prefix(zip_path)
    if prefix is None:
        raise ValueError("zip is not a valid config package")

    configs_dir = Path(configs_dir).resolve()
    configs_dir.mkdir(parents=True, exist_ok=True)
    root = configs_dir.resolve()

    backup_dir = configs_dir / "backup" / f"import_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for path in sorted(configs_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(configs_dir)
        if _is_excluded(rel.parts):
            continue
        dest = backup_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)

    for entry in configs_dir.iterdir():
        if entry.name in _EXCLUDED_DIR_NAMES:
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    extracted = 0
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename
            if name.endswith("/") or not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            rel_parts = PurePosixPath(rel).parts
            if ".." in rel_parts or PurePosixPath(rel).is_absolute():
                raise ValueError(f"illegal path in zip: {name}")
            target = (configs_dir / Path(*rel_parts)).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"illegal path in zip: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1

    if extracted == 0:
        raise ValueError("no config files in zip")
    return backup_dir


def _export_config_clicked():
    from ok import Logger, og
    from ok.gui.util.Alert import alert_error, alert_info
    from ok.util.explorer import reveal_in_explorer
    from ok.util.file import get_downloads_folder

    logger = Logger.get_logger(__name__)
    app_name = og.config.get("gui_title") or "config"
    downloads_path = Path(get_downloads_folder())
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = downloads_path / f"{app_name}-config-{timestamp}.zip"
    try:
        downloads_path.mkdir(parents=True, exist_ok=True)
        export_config_zip(get_configs_dir(), zip_path)
    except Exception as exc:
        logger.error("export config zip failed", exc)
        alert_error(f"{og.app.tr('导出失败')}: {exc}", tray=True)
        return

    alert_info(og.app.tr("配置已导出"), tray=True)
    try:
        reveal_in_explorer(zip_path)
    except Exception as exc:
        logger.warning(f"reveal exported config zip failed: {exc}")


def _collect_zip_paths(mime_data) -> list[Path]:
    paths = []
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        if path.suffix.lower() == ".zip" and path.is_file():
            paths.append(path)
    return paths


def _pause_executor():
    from ok import og

    try:
        executor = getattr(og, "executor", None)
        if executor is not None and not executor.paused:
            executor.pause()
    except Exception:
        pass


def _restart_application():
    from ok import Logger, og
    from ok.gui.util.Alert import alert_error

    logger = Logger.get_logger(__name__)
    import ctypes
    import sys

    try:
        params = subprocess.list2cmdline(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "open", sys.executable, params, None, 0)
        logger.info(f"config import restart application: {sys.executable} {params}")
    except Exception as exc:
        logger.error("restart application after config import failed", exc)
        alert_error(f"{og.app.tr('重启失败')}: {exc}", tray=True)
        return

    app = getattr(og, "app", None)
    if app is not None:
        app.quit()


def _confirm_and_import(zip_path: Path):
    from ok import Logger, og
    from ok.gui.util.Alert import alert_error, alert_info

    logger = Logger.get_logger(__name__)
    if resolve_import_prefix(zip_path) is None:
        alert_error(og.app.tr("压缩包中未找到配置文件"), tray=True)
        return

    parent = QApplication.activeWindow()
    confirm_box = MessageBox(
        og.app.tr("配置导入"),
        og.app.tr("导入将覆盖当前配置文件，原配置会备份到 configs/backup 目录，重启应用后生效。是否继续？"),
        parent,
    )
    if not confirm_box.exec():
        return

    _pause_executor()
    try:
        apply_config_import(zip_path, get_configs_dir())
    except Exception as exc:
        logger.error("import config failed", exc)
        alert_error(f"{og.app.tr('导入失败')}: {exc}", tray=True)
        return

    alert_info(og.app.tr("导入完成"), tray=True)
    restart_box = MessageBox(
        og.app.tr("导入完成"),
        og.app.tr("导入成功，配置将在重启应用后生效。是否立即重启？"),
        parent,
    )
    restart_box.yesButton.setText(og.app.tr("立即重启"))
    restart_box.cancelButton.setText(og.app.tr("稍后"))
    if restart_box.exec():
        _restart_application()


def _import_config_clicked():
    from ok import og
    from ok.util.file import get_downloads_folder

    parent = QApplication.activeWindow()
    downloads_path = Path(get_downloads_folder())
    selected, _ = QFileDialog.getOpenFileName(
        parent,
        og.app.tr("选择配置压缩包"),
        str(downloads_path),
        "Zip (*.zip);;All Files (*)",
    )
    if not selected:
        return
    _confirm_and_import(Path(selected))


def _drag_enter_event(self, event):
    if _collect_zip_paths(event.mimeData()):
        event.acceptProposedAction()
    else:
        event.ignore()


def _drop_event(self, event):
    zip_paths = _collect_zip_paths(event.mimeData())
    if not zip_paths:
        event.ignore()
        return
    event.acceptProposedAction()
    _confirm_and_import(zip_paths[0])


def install_config_transfer_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.ui.qt.MainWindow import MainWindow
    from ok.ui.qt.start.StartTab import StartTab

    original_start_init = StartTab.__init__

    def patched_start_init(self, *args, **kwargs):
        original_start_init(self, *args, **kwargs)
        try:
            from ok import og

            self.export_config_button = PushButton(FluentIcon.SAVE, og.app.tr("Export Config"))
            self.import_config_button = PushButton(FluentIcon.DOWNLOAD, og.app.tr("Import Config"))
        except Exception:
            self.export_config_button = PushButton(FluentIcon.SAVE, "Export Config")
            self.import_config_button = PushButton(FluentIcon.DOWNLOAD, "Import Config")

        self.export_config_button.clicked.connect(_export_config_clicked)
        self.import_config_button.clicked.connect(_import_config_clicked)

        try:
            # 插到行尾弹性空白之前，与现有按钮保持一行
            count = self.debug_layout.count()
            insert_at = count
            if count > 0 and self.debug_layout.itemAt(count - 1).spacerItem() is not None:
                insert_at = count - 1
            self.debug_layout.insertWidget(insert_at, self.export_config_button)
            self.debug_layout.insertWidget(insert_at + 1, self.import_config_button)
        except Exception:
            self.debug_layout.addWidget(self.export_config_button)
            self.debug_layout.addWidget(self.import_config_button)

    StartTab.__init__ = patched_start_init

    original_main_init = MainWindow.__init__

    def patched_main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        self.setAcceptDrops(True)

    MainWindow.__init__ = patched_main_init
    MainWindow.dragEnterEvent = _drag_enter_event
    MainWindow.dropEvent = _drop_event

    _PATCH_INSTALLED = True
