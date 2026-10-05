import re
from typing import Any


def account_name_from_line(value: Any) -> str:
    """Return the account name before the first comma; trailing content is ignored."""
    text = "" if value is None else str(value)
    return text.split(",", 1)[0].strip()


def visible_account_parts(username: str) -> tuple[str, str]:
    """Return the visible leading/trailing parts used by the game's masked account list."""
    account = account_name_from_line(username)
    digits = re.sub(r"\D", "", account)
    if len(digits) >= 7:
        return digits[:3], digits[-4:]
    if len(account) >= 7:
        return account[:3], account[-4:]
    return "", account


def visible_account_label(username: str) -> str:
    """Format an account like the game's masked list, e.g. 138****1234."""
    prefix, suffix = visible_account_parts(username)
    if prefix:
        return f"{prefix}****{suffix}"
    return suffix
