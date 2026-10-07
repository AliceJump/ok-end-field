"""Account-scoped latest user facility snapshot stored under ``logs``.

Official map data is authoritative, so these files are a local convenience
snapshot, not a source of truth. Each account has exactly one file and every
successful navigation overwrites it.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data.map_mark_query import ItemMap, extract_mark_points

LOG_DIR = Path("logs") / "user_map_marks"


def _safe_account_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._")
    return text or "unknown"


def resolve_user_mark_account_key(
    *,
    account_id: str = "",
    map_user_id: str = "",
    role_id: str = "",
    server_id: str = "",
) -> str:
    """Return the stable account key to use for the latest snapshot file."""

    for candidate in (account_id, map_user_id):
        if str(candidate or "").strip():
            return _safe_account_key(candidate)
    if str(role_id or "").strip() or str(server_id or "").strip():
        server = str(server_id or "s").strip()
        role = str(role_id or "r").strip()
        return _safe_account_key(f"{server}_{role}")
    return "unknown"


def account_snapshot_path(account_key: str) -> Path:
    """Return the single latest snapshot path for an account."""

    return LOG_DIR / f"{_safe_account_key(account_key)}.json"


def write_latest_snapshot(account_key: str, map_id: str, marks: ItemMap) -> Path:
    """Overwrite the latest snapshot for one account."""

    path = account_snapshot_path(account_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "account": _safe_account_key(account_key),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "map_id": str(map_id or "").strip(),
        "marks": marks,
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp_path, path)
    return path


def persist_user_marks(
    payload: Any,
    *,
    map_id: str,
    account_id: str = "",
    map_user_id: str = "",
    role_id: str = "",
    server_id: str = "",
) -> Path | None:
    """Parse user ``saveMarks`` from a map response and write one latest file."""

    account_key = resolve_user_mark_account_key(
        account_id=account_id,
        map_user_id=map_user_id,
        role_id=role_id,
        server_id=server_id,
    )
    marks = extract_mark_points(payload, map_id=map_id, user_only=True)
    return write_latest_snapshot(account_key, map_id, marks)


__all__ = [
    "LOG_DIR",
    "account_snapshot_path",
    "persist_user_marks",
    "resolve_user_mark_account_key",
    "write_latest_snapshot",
]
