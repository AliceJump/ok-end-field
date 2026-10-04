from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

try:
    from PySide6.QtCore import QThread, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit, QVBoxLayout
except ImportError:  # pragma: no cover - packaged app depends on PySide6
    QApplication = None
    QDialog = object
    QLabel = None
    QPlainTextEdit = None
    QThread = None
    QTimer = None
    Qt = None
    QVBoxLayout = None


@dataclass(frozen=True)
class CombatDecisionEntry:
    timestamp: str
    actor: str
    action: str
    state: str
    reason: str
    detail: str = ""


_LOCK = threading.Lock()
_HISTORY: deque[CombatDecisionEntry] = deque(maxlen=200)
_CURRENT: CombatDecisionEntry | None = None
_CURRENT_KEY: object | None = None
_REVISION = 0
_WINDOW = None


def _timestamp_now() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def clear_combat_decisions() -> None:
    global _CURRENT, _CURRENT_KEY, _REVISION
    with _LOCK:
        _HISTORY.clear()
        _CURRENT = None
        _CURRENT_KEY = None
        _REVISION += 1


def publish_combat_decision(
    actor: str,
    action: str,
    state: str,
    reason: str,
    detail: str = "",
    *,
    dedupe_key: object | None = None,
) -> None:
    """Publish one human-readable decision without feeding anything back to combat logic.

    Repeated polling of the same decision updates only the live row. A new history
    row is appended only when the semantic decision key changes, so rapidly changing
    elapsed-time details stay readable instead of becoming a frame-by-frame log.
    """
    global _CURRENT, _CURRENT_KEY, _REVISION
    entry = CombatDecisionEntry(
        timestamp=_timestamp_now(),
        actor=actor or "未知角色",
        action=action,
        state=state,
        reason=reason,
        detail=detail,
    )
    with _LOCK:
        if dedupe_key is None or dedupe_key != _CURRENT_KEY:
            _HISTORY.append(entry)
        _CURRENT = entry
        _CURRENT_KEY = dedupe_key
        _REVISION += 1


def combat_decision_snapshot() -> tuple[CombatDecisionEntry | None, list[CombatDecisionEntry]]:
    with _LOCK:
        return _CURRENT, list(_HISTORY)


def combat_decision_revision() -> int:
    with _LOCK:
        return _REVISION


def _format_entry(entry: CombatDecisionEntry, *, multiline: bool) -> str:
    head = f"{entry.timestamp}  {entry.actor}  ·  {entry.action}  ·  {entry.state}"
    if multiline:
        body = f"原因：{entry.reason}"
        if entry.detail:
            body += f"\n补充：{entry.detail}"
        return f"{head}\n{body}"
    tail = f"原因：{entry.reason}"
    if entry.detail:
        tail += f"；{entry.detail}"
    return f"{head}\n  {tail}"


if QApplication is not None:

    class CombatDecisionWindow(QDialog):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("战斗决策实时查看")
            self.resize(920, 620)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("当前意图"))

            self.current = QPlainTextEdit(self)
            self.current.setReadOnly(True)
            self.current.setMaximumBlockCount(20)
            self.current.setMinimumHeight(120)
            layout.addWidget(self.current)

            layout.addWidget(QLabel("历史（最近 200 条决策变化）"))
            self.history = QPlainTextEdit(self)
            self.history.setReadOnly(True)
            layout.addWidget(self.history, 1)

            self._last_current = None
            self._last_history_size = -1
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh_view)
            self._timer.start(80)
            self.refresh_view()

        def refresh_view(self):
            current, history = combat_decision_snapshot()
            if current != self._last_current:
                self._last_current = current
                self.current.setPlainText("等待战斗决策…" if current is None else _format_entry(current, multiline=True))

            if len(history) == self._last_history_size:
                return
            self._last_history_size = len(history)
            self.history.setPlainText("\n\n".join(_format_entry(item, multiline=False) for item in reversed(history)))


def show_combat_decision_window() -> bool:
    """Show the modeless viewer on the Qt GUI thread. Returns False if Qt is unavailable."""
    if QApplication is None:
        return False
    app = QApplication.instance()
    if app is None:
        return False

    def _show():
        global _WINDOW
        if _WINDOW is None:
            _WINDOW = CombatDecisionWindow()
        _WINDOW.show()
        _WINDOW.raise_()
        _WINDOW.activateWindow()

    if QThread.currentThread() == app.thread():
        _show()
    else:
        QTimer.singleShot(0, app, _show)
    return True
