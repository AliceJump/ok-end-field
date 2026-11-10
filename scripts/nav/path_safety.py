"""Path containment helpers for agent-invokable navigation tools."""

from pathlib import Path


def resolve_under(root, value, *, label, must_exist=False):
    """Resolve a CLI path and reject anything outside the allowed root."""

    allowed_root = Path(root).expanduser().resolve()
    resolved = Path(value).expanduser().resolve(strict=False)
    if not resolved.is_relative_to(allowed_root):
        raise ValueError(f"{label}_escapes_allowed_root: {resolved}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved
