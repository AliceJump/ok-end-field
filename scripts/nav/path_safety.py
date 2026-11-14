"""Path containment helpers for agent-invokable navigation tools."""

from pathlib import Path


def resolve_under(root, value, *, label, must_exist=False, relative_to_root=False):
    """Resolve a CLI path and reject anything outside the allowed root."""

    allowed_root = Path(root).expanduser().resolve()
    candidate = Path(value).expanduser()
    if relative_to_root and not candidate.is_absolute():
        candidate = allowed_root / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(allowed_root):
        raise ValueError(f"{label}_escapes_allowed_root: {resolved}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved
