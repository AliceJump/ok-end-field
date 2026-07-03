from __future__ import annotations

import calendar
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import requests
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon, PushButton, BodyLabel, FluentStyleSheet, ComboBox,
    MessageBoxBase, SubtitleLabel,
)


_PATCH_INSTALLED = False


def _normalize_note_filename(note_text: str) -> str:
    note_text = (note_text or "").strip()
    if not note_text:
        return ""

    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        note_text = note_text.replace(char, "_")

    return note_text.strip(" ._")


def _build_logs_zip(note_text: str = ""):
    from ok import Logger, og
    from ok.gui.util.Alert import alert_error
    from ok.util.file import get_downloads_folder

    app_name = og.config.get('gui_title')
    downloads_path = Path(get_downloads_folder())
    zip_path = downloads_path / f"{app_name}-log.zip"
    logger = Logger.get_logger(__name__)

    def _is_image_file(file_path: Path) -> bool:
        return file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}

    def _wait_file_settled(file_path: Path, settle_seconds: float = 0.8) -> bool:
        try:
            first = file_path.stat()
        except Exception:
            return False

        if first.st_size <= 0:
            return False

        if (time.time() - first.st_mtime) < settle_seconds:
            time.sleep(settle_seconds)
            try:
                second = file_path.stat()
            except Exception:
                return False
            if second.st_size != first.st_size or second.st_mtime_ns != first.st_mtime_ns:
                return False

        return True

    def _write_snapshot_file(zipf: zipfile.ZipFile, file_path: Path, arcname: Path):
        if not _wait_file_settled(file_path):
            logger.debug(f"skip unstable file: {file_path}")
            return

        if _is_image_file(file_path):
            try:
                with Image.open(file_path) as img:
                    img.verify()
            except Exception as exc:
                logger.warning(f"skip invalid image file: {file_path} ({exc})")
                return

        with file_path.open("rb") as src:
            zipf.writestr(str(arcname).replace("\\", "/"), src.read())

    downloads_path.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # 将用户输入写入 info.txt，方便查看上传时的说明
            zipf.writestr("info.txt", note_text or "")

            for folder in ["screenshots", "logs"]:
                source_dir = Path.cwd() / folder
                if not source_dir.is_dir():
                    continue
                for file_path in source_dir.rglob("*"):
                    if file_path.is_file():
                        _write_snapshot_file(zipf, file_path, file_path.relative_to(Path.cwd()))

        # 本地先做一次完整性校验，避免把损坏的 zip 继续上传到服务器
        try:
            with zipfile.ZipFile(zip_path, "r") as zipf:
                bad_name = zipf.testzip()
                if bad_name is not None:
                    raise RuntimeError(f"zip integrity check failed: {bad_name}")
        except Exception:
            raise
    except Exception as exc:
        alert_error(f"{og.app.tr('Export failed')}: {exc}", tray=True)
        logger.error('export_logs exception', exc)
        raise

    return zip_path


def _export_logs():
    try:
        zip_path = _build_logs_zip()
        subprocess.run(["explorer", f"/select,{zip_path}"])
    except Exception:
        return


def _upload_logs_bg(note_text: str = ""):
    from ok import Logger, og
    from ok.gui.util.Alert import alert_error, alert_info

    upload_api = (og.config.get('log_upload_api') or '').strip()
    if not upload_api:
        alert_error(og.app.tr("Please configure log upload api in config"), tray=True)
        return

    zip_path = _build_logs_zip(note_text)
    logger = Logger.get_logger(__name__)
    file_size_mb = zip_path.stat().st_size / (1024 * 1024)

    last_exc = None
    for attempt in range(1, 4):
        try:
            with open(zip_path, 'rb') as file_handle:
                response = requests.post(
                    upload_api,
                    files={'file': (zip_path.name, file_handle, 'application/zip')},
                    data={
                        'app_name': og.config.get('gui_title'),
                        'note_text': note_text,
                    },
                    timeout=(10, 900),
                )
            response.raise_for_status()
            alert_info(og.app.tr("Upload succeeded"), tray=True)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(f'upload attempt {attempt}/3 failed ({file_size_mb:.1f} MB): {exc}')
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))

    alert_error(f"{og.app.tr('Upload failed')}: {last_exc}", tray=True)
    logger.error('upload_logs exception', last_exc)


def _prompt_upload_note() -> str:
    from ok import og
    try:
        from ok import Logger
        Logger.get_logger(__name__).debug('prompt_upload_note called')
    except Exception:
        pass

    try:
        from datetime import datetime
        from PySide6.QtWidgets import QApplication
        from ok.gui.tasks.ConfigCard import ConfigCard

        parent = QApplication.activeWindow()
        dlg = MessageBoxBase(parent)
        dlg.setWindowTitle(og.app.tr("日志上传"))

        try:
            FluentStyleSheet.DIALOG.apply(dlg)
        except Exception:
            pass

        title = SubtitleLabel(og.app.tr("日志上传"), dlg)
        tip = BodyLabel(og.app.tr("请填写如下信息以便排查（错误描述为必填）"))
        tip.setWordWrap(True)
        dlg.viewLayout.addWidget(title)
        dlg.viewLayout.addWidget(tip)

        class _LocalInMemoryConfig(dict):
            def __init__(self, initial, defaults):
                super().__init__(initial)
                self.default = defaults
                self.on_reset = None

            def get_default(self, key):
                return self.default.get(key)

            def has_user_config(self):
                return any(not str(k).startswith("_") for k in self.keys())

            def reset_to_default(self):
                # 重置文本项为空；时间项重置为触发重置时的当前时间
                from datetime import datetime

                now_local = datetime.now()
                self["出错的任务"] = ""
                self["错误描述"] = ""
                self["复现步骤"] = ""
                self["time_month"] = f"{now_local.month:02d}"
                self["time_day"] = f"{now_local.day:02d}"
                self["time_hour"] = f"{now_local.hour:02d}"
                self["time_minute"] = f"{now_local.minute:02d}"

                if callable(self.on_reset):
                    self.on_reset(now_local)

        now = datetime.now()
        keys = [
            "出错的任务",
            "错误描述",
            "复现步骤",
        ]

        defaults = {k: "" for k in keys}
        initial = dict(defaults)

        task_options = []
        try:
            executor = getattr(og, 'executor', None)
            tasks = getattr(executor, 'tasks', None)
            if tasks:
                task_options = [t.name for t in tasks if getattr(t, 'name', None)]
        except Exception:
            task_options = []

        config_type = {
            "复现步骤": {"type": "text_edit"},
        }
        if task_options:
            config_type["出错的任务"] = {"type": "drop_down", "options": task_options}

        config_description = {
            "出错的任务": og.app.tr("发生错误的任务名（可选）"),
            "错误描述": og.app.tr("简要描述错误（必填）"),
            "复现步骤": og.app.tr("复现步骤（可选）"),
        }

        virtual_config = _LocalInMemoryConfig(initial, defaults)
        card = ConfigCard(
            None,
            og.app.tr("错误信息"),
            virtual_config,
            og.app.tr("请填写错误相关信息"),
            defaults,
            config_description,
            config_type,
            None,
        )
        dlg.viewLayout.addWidget(card)

        # 月/日/时/分放在一行
        time_row_label = BodyLabel(og.app.tr("大致发生时间（月/日/时/分）"), dlg)
        dlg.viewLayout.addWidget(time_row_label)

        time_row_widget = QWidget(dlg)
        time_row_layout = QHBoxLayout(time_row_widget)
        time_row_layout.setContentsMargins(0, 0, 0, 0)
        time_row_layout.setSpacing(8)

        month_cb = ComboBox(time_row_widget)
        # 月份不允许超过当前月
        month_cb.addItems([f"{m:02d}" for m in range(1, now.month + 1)])
        month_cb.setCurrentText(f"{now.month:02d}")
        month_cb.setFixedWidth(82)
        time_row_layout.addWidget(month_cb)

        day_cb = ComboBox(time_row_widget)
        day_cb.setFixedWidth(82)
        time_row_layout.addWidget(day_cb)

        hour_cb = ComboBox(time_row_widget)
        hour_cb.addItems([f"{h:02d}" for h in range(0, 24)])
        hour_cb.setCurrentText(f"{now.hour:02d}")
        hour_cb.setFixedWidth(82)
        time_row_layout.addWidget(hour_cb)

        minute_cb = ComboBox(time_row_widget)
        minute_cb.addItems([f"{mi:02d}" for mi in range(0, 60)])
        minute_cb.setCurrentText(f"{now.minute:02d}")
        minute_cb.setFixedWidth(82)
        time_row_layout.addWidget(minute_cb)
        time_row_layout.addStretch(1)

        dlg.viewLayout.addWidget(time_row_widget)

        def _rebuild_day_options(preferred_day: str | None = None):
            """按月份重建日期下拉：当前月最大到今天，其它月份到月底。"""
            try:
                selected_month = int(month_cb.currentText())
            except Exception:
                selected_month = now.month

            if selected_month == now.month:
                max_day = now.day
            else:
                max_day = calendar.monthrange(now.year, selected_month)[1]

            previous_day = preferred_day or day_cb.currentText() or f"{max_day:02d}"
            day_cb.clear()
            day_cb.addItems([f"{d:02d}" for d in range(1, max_day + 1)])

            if previous_day in [f"{d:02d}" for d in range(1, max_day + 1)]:
                day_cb.setCurrentText(previous_day)
            else:
                day_cb.setCurrentText(f"{max_day:02d}")

        _rebuild_day_options(preferred_day=f"{now.day:02d}")
        month_cb.currentTextChanged.connect(lambda _: _rebuild_day_options())

        # 将 ConfigCard 的重置操作同步到时间下拉框（当前时间）
        def _sync_time_controls(dt):
            month_cb.setCurrentText(f"{dt.month:02d}")
            _rebuild_day_options(preferred_day=f"{dt.day:02d}")
            hour_cb.setCurrentText(f"{dt.hour:02d}")
            minute_cb.setCurrentText(f"{dt.minute:02d}")

        virtual_config.on_reset = _sync_time_controls

        dlg.yesButton.setText(og.app.tr("上传"))
        dlg.cancelButton.setText(og.app.tr("取消"))
        dlg.yesButton.setEnabled(False)

        def _adjust_dialog_width():
            try:
                content_width = max(
                    dlg.widget.sizeHint().width(),
                    card.sizeHint().width(),
                    time_row_widget.sizeHint().width() + 40,
                )
                desired_width = max(680, int(content_width + 96))
                if desired_width > dlg.minimumWidth():
                    dlg.setMinimumWidth(desired_width)
                    dlg.widget.setMinimumWidth(max(620, desired_width - 32))
                if desired_width > dlg.width():
                    dlg.resize(desired_width, dlg.height())
            except Exception:
                pass

        def _timer_check():
            try:
                has = bool((virtual_config.get('错误描述') or '').strip())
                dlg.yesButton.setEnabled(has)
                _adjust_dialog_width()
            except Exception:
                dlg.yesButton.setEnabled(False)

        timer = QTimer(dlg)
        timer.setInterval(200)
        timer.timeout.connect(_timer_check)
        timer.start()
        _timer_check()

        dlg.yesButton.clicked.connect(dlg.accept)
        dlg.cancelButton.clicked.connect(dlg.reject)
        dlg.widget.setMinimumHeight(420)

        accepted = dlg.exec()
        try:
            timer.stop()
        except Exception:
            pass
        if not accepted:
            return ""

        import json
        data = {
            "task": (virtual_config.get('出错的任务') or '').strip(),
            "error_description": (virtual_config.get('错误描述') or '').strip(),
            "reproduction_steps": (virtual_config.get('复现步骤') or '').strip(),
            "time": (
                f"{now.year}-{month_cb.currentText()}-{day_cb.currentText()} "
                f"{hour_cb.currentText()}:{minute_cb.currentText()}"
            ),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        try:
            from ok import Logger
            Logger.get_logger(__name__).exception('prompt_upload_note exception')
            from ok.gui.util.Alert import alert_error
            alert_error(f"{og.app.tr('打开日志上传窗口失败')}: {exc}", tray=True)
        except Exception:
            pass
        return ""


def _upload_logs():
    note_text = _prompt_upload_note()
    if not note_text:
        return

    worker = threading.Thread(target=_upload_logs_bg, args=(note_text,))
    worker.daemon = True
    worker.start()


def install_log_upload_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    from ok.gui.start.StartTab import StartTab

    original_init = StartTab.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            from ok import og
            label = og.app.tr("Upload Logs")
        except Exception:
            label = og.app.tr("Upload Logs")

        self.upload_log_button = PushButton(FluentIcon.SEND, label)
        self.upload_log_button.clicked.connect(_upload_logs)

        try:
            self.debug_layout.insertWidget(1, self.upload_log_button)
        except Exception:
            self.debug_layout.addWidget(self.upload_log_button)

    StartTab.__init__ = patched_init
    StartTab.export_logs = staticmethod(_export_logs)
    StartTab.upload_logs = staticmethod(_upload_logs)
    StartTab._build_logs_zip = staticmethod(_build_logs_zip)

    _PATCH_INSTALLED = True