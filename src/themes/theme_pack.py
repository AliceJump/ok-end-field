"""Validate, install and select data-only QSS theme ZIPs.

Theme packages are never extracted or executed. Only two known text members
are accepted, keeping installs separate from the application update directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

THEME_HOME = Path("configs") / "themes"
_MAX_ARCHIVE_BYTES = 512 * 1024
_MAX_MANIFEST_BYTES = 16 * 1024
_MAX_QSS_BYTES = 128 * 1024
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_UNSAFE_QSS = re.compile(r"(?:\burl\s*\(|@import\b)", re.IGNORECASE)


class ThemePackError(ValueError):
    """Invalid or unsafe theme-package content."""


@dataclass(frozen=True)
class ThemePack:
    id: str
    name: str
    version: str
    qss: str
    fingerprint: str


def _validate_id(theme_id: str) -> str:
    """Reject unsafe IDs before using them in installed archive paths."""
    if not isinstance(theme_id, str) or _ID_PATTERN.fullmatch(theme_id) is None:
        raise ThemePackError("Theme ID must be lowercase ASCII letters, digits, '_' or '-'.")
    return theme_id


def read_theme_pack(path: Path) -> ThemePack:
    """Read a ZIP with exactly theme.json and style.qss, with bounded sizes."""
    path = Path(path)
    try:
        if path.stat().st_size > _MAX_ARCHIVE_BYTES:
            raise ThemePackError("Theme ZIP is too large.")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 2 or {member.filename for member in members} != {"theme.json", "style.qss"}:
                raise ThemePackError("Theme ZIP must contain only theme.json and style.qss at its root.")
            for member in members:
                if (
                    member.is_dir()
                    or member.flag_bits & 1
                    or ((member.external_attr >> 16) & 0o170000) == 0o120000
                ):
                    raise ThemePackError("Theme ZIP contains an unsupported member.")
                limit = _MAX_MANIFEST_BYTES if member.filename == "theme.json" else _MAX_QSS_BYTES
                if member.file_size > limit:
                    raise ThemePackError(f"Theme member too large: {member.filename}")
            manifest_bytes = archive.read("theme.json")
            qss_bytes = archive.read("style.qss")
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise ThemePackError(f"Unable to read theme ZIP: {path}") from exc

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        qss = qss_bytes.decode("utf-8")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ThemePackError("Theme manifest or QSS is not valid UTF-8 text.") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "id", "name", "version"}:
        raise ThemePackError("Invalid theme manifest fields.")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ThemePackError("Unsupported theme manifest schema.")
    theme_id = _validate_id(manifest["id"])
    if not all(
        isinstance(manifest[key], str) and 0 < len(manifest[key]) <= 80
        for key in ("name", "version")
    ):
        raise ThemePackError("Invalid theme name or version.")
    if not qss.strip() or _UNSAFE_QSS.search(qss):
        raise ThemePackError("Theme QSS cannot be empty or reference external resources.")
    digest = hashlib.sha256(manifest_bytes + b"\0" + qss_bytes).hexdigest()
    return ThemePack(theme_id, manifest["name"], manifest["version"], qss, digest)


def import_theme_pack(source: Path, theme_home: Path = THEME_HOME) -> ThemePack:
    """Install the validated archive atomically; do not activate it implicitly."""
    theme_home = Path(theme_home)
    temporary = None
    try:
        # Validate the staged bytes before publishing rather than re-reading
        # an untrusted source after validation (TOCTOU).
        with Path(source).open("rb") as input_file:
            contents = input_file.read(_MAX_ARCHIVE_BYTES + 1)
        if len(contents) > _MAX_ARCHIVE_BYTES:
            raise ThemePackError("Theme ZIP is too large.")
        theme_home.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=theme_home, prefix=".install-", suffix=".zip", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(contents)
        pack = read_theme_pack(temporary)
        os.replace(temporary, theme_home / f"{pack.id}.zip")
        return pack
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def select_theme(theme_id: str | None, theme_home: Path = THEME_HOME) -> None:
    """Set installed active theme, or None to restore the framework default."""
    theme_home = Path(theme_home)
    selection = theme_home / "active.json"
    if theme_id is None:
        selection.unlink(missing_ok=True)
        return
    _validate_id(theme_id)
    theme = read_theme_pack(theme_home / f"{theme_id}.zip")
    if theme.id != theme_id:
        raise ThemePackError("Installed theme ID does not match its filename.")
    theme_home.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=theme_home, prefix=".active-", suffix=".json",
            mode="w", encoding="utf-8", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump({"id": theme_id}, output)
        os.replace(temporary, selection)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_active_theme(theme_home: Path = THEME_HOME) -> ThemePack | None:
    """Return the active QSS theme; absent selection is a safe no-op."""
    selection = Path(theme_home) / "active.json"
    if not selection.exists():
        return None
    try:
        data = json.loads(selection.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ThemePackError("Unable to read active theme selection.") from exc
    if not isinstance(data, dict) or set(data) != {"id"}:
        raise ThemePackError("Invalid active theme selection.")
    theme_id = _validate_id(data["id"])
    pack = read_theme_pack(Path(theme_home) / f"{theme_id}.zip")
    if pack.id != theme_id:
        raise ThemePackError("Active theme ID does not match installed archive.")
    return pack
