import copy
import json
import os
import threading
from typing import Any, Dict

from ok.util.file import ensure_dir_for_file, get_relative_path

_STORE_PATH = get_relative_path("configs", "account_scoped_overrides.json")
_LOCK = threading.Lock()
_CACHE_MTIME = object()
_CACHE_DATA: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {"accounts": {}}


def get_store_path() -> str:
    return _STORE_PATH


def _normalize(data: Any) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    if not isinstance(data, dict):
        return {"accounts": {}}

    raw_accounts = data.get("accounts")
    if not isinstance(raw_accounts, dict):
        return {"accounts": {}}

    normalized_accounts: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for account_name, task_map in raw_accounts.items():
        if not isinstance(account_name, str):
            continue
        account = account_name.strip()
        if not account or not isinstance(task_map, dict):
            continue

        normalized_task_map: Dict[str, Dict[str, Any]] = {}
        for task_name, override_map in task_map.items():
            if not isinstance(task_name, str):
                continue
            task = task_name.strip()
            if not task or not isinstance(override_map, dict):
                continue
            normalized_task_map[task] = dict(override_map)

        normalized_accounts[account] = normalized_task_map

    return {"accounts": normalized_accounts}


def load_overrides(force: bool = False) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    global _CACHE_MTIME
    global _CACHE_DATA

    with _LOCK:
        if os.path.exists(_STORE_PATH):
            current_mtime: Any = os.path.getmtime(_STORE_PATH)
        else:
            current_mtime = None

        if not force and current_mtime == _CACHE_MTIME:
            return copy.deepcopy(_CACHE_DATA)

        if current_mtime is None:
            data = {"accounts": {}}
        else:
            try:
                with open(_STORE_PATH, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
            except Exception:
                data = {"accounts": {}}

        normalized = _normalize(data)
        _CACHE_DATA = normalized
        _CACHE_MTIME = current_mtime
        return copy.deepcopy(normalized)


def save_overrides(data: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    global _CACHE_MTIME
    global _CACHE_DATA

    normalized = _normalize(data)

    with _LOCK:
        ensure_dir_for_file(_STORE_PATH)
        with open(_STORE_PATH, "w", encoding="utf-8") as fp:
            json.dump(normalized, fp, ensure_ascii=False, indent=2)

        _CACHE_DATA = normalized
        _CACHE_MTIME = os.path.getmtime(_STORE_PATH)

    return copy.deepcopy(normalized)


def get_account_task_overrides(account: str, task_name: str) -> Dict[str, Any]:
    if not account or not task_name:
        return {}
    data = load_overrides()
    return dict(data["accounts"].get(account, {}).get(task_name, {}))


def set_account_task_overrides(account: str, task_name: str, values: Dict[str, Any]) -> None:
    if not account or not task_name:
        return

    data = load_overrides()
    accounts = data.setdefault("accounts", {})
    task_map = accounts.setdefault(account, {})

    if values:
        task_map[task_name] = dict(values)
    else:
        task_map.pop(task_name, None)

    if not task_map:
        accounts.pop(account, None)

    save_overrides(data)


def remove_account_task_overrides(account: str, task_name: str) -> None:
    if not account or not task_name:
        return

    data = load_overrides()
    accounts = data.get("accounts", {})
    task_map = accounts.get(account)
    if not isinstance(task_map, dict):
        return

    task_map.pop(task_name, None)
    if not task_map:
        accounts.pop(account, None)

    save_overrides(data)


def list_accounts() -> list[str]:
    data = load_overrides()
    return list(data["accounts"].keys())
