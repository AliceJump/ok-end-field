"""Normalize operator names used as keys across official wiki snapshots."""


def _normalize_name(name: str) -> str:
    if name.startswith("管理员"):
        return "管理员"
    return name
