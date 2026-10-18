from __future__ import annotations

import hashlib
import json
from importlib import metadata
from pathlib import Path
from typing import Any

PATCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = PATCH_DIR.parents[1]
MANIFEST_PATH = PATCH_DIR / "customization_manifest.json"
_EXCLUDED_PATCH_MODULES = {"__init__.py", "customization_contract.py"}


class CustomizationContractError(RuntimeError):
    pass


def load_customization_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Parse the owned manifest and reject unsupported or malformed schemas."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CustomizationContractError(f"Unable to read customization manifest: {path}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise CustomizationContractError("Unsupported customization manifest schema.")

    upstream = manifest.get("upstream")
    if not isinstance(upstream, dict):
        raise CustomizationContractError("Customization manifest is missing upstream metadata.")
    if not isinstance(upstream.get("distribution"), str) or not isinstance(upstream.get("version"), str):
        raise CustomizationContractError("Customization manifest upstream metadata is invalid.")

    overlay_files = manifest.get("overlay_files")
    if (
        not isinstance(overlay_files, list)
        or not overlay_files
        or not all(isinstance(item, str) for item in overlay_files)
    ):
        raise CustomizationContractError("Customization manifest overlay_files must be a non-empty string list.")
    if len(overlay_files) != len(set(overlay_files)):
        raise CustomizationContractError("Customization manifest contains duplicate overlay files.")

    return manifest


def _declared_overlay_paths(manifest: dict[str, Any], repo_root: Path) -> dict[str, Path]:
    """Resolve all declared patch sources without allowing repository escape."""
    resolved_root = repo_root.resolve()
    paths: dict[str, Path] = {}
    for relative in manifest["overlay_files"]:
        path = (resolved_root / relative).resolve()
        if not path.is_relative_to(resolved_root):
            raise CustomizationContractError(f"Overlay file escapes repository root: {relative}")
        if not path.is_file():
            raise CustomizationContractError(f"Overlay file is missing: {relative}")
        paths[relative] = path
    return paths


def _discover_patch_sources(repo_root: Path, patch_dir: Path) -> set[str]:
    """Discover project patch modules that the manifest must account for."""
    return {
        path.resolve().relative_to(repo_root.resolve()).as_posix()
        for path in patch_dir.glob("*.py")
        if path.name not in _EXCLUDED_PATCH_MODULES
    }


def _validate_overlay_coverage(manifest: dict[str, Any], repo_root: Path, patch_dir: Path) -> dict[str, Path]:
    """Reject absent, stale, or untracked patch entries before hashing."""
    declared = _declared_overlay_paths(manifest, repo_root)
    discovered = _discover_patch_sources(repo_root, patch_dir)
    declared_names = set(declared)
    if declared_names != discovered:
        missing = sorted(discovered - declared_names)
        stale = sorted(declared_names - discovered)
        details = []
        if missing:
            details.append(f"untracked patch sources: {', '.join(missing)}")
        if stale:
            details.append(f"manifest-only files: {', '.join(stale)}")
        raise CustomizationContractError("Customization overlay manifest is out of sync: " + "; ".join(details))
    return declared


def _hash_component(hasher: Any, name: str, payload: bytes) -> None:
    """Add an unambiguous length-prefixed component to the digest."""
    name_bytes = name.encode("utf-8")
    hasher.update(len(name_bytes).to_bytes(8, "big"))
    hasher.update(name_bytes)
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def customization_fingerprint(
    *,
    manifest_path: Path = MANIFEST_PATH,
    repo_root: Path = REPO_ROOT,
    patch_dir: Path = PATCH_DIR,
) -> str:
    """Fingerprint the manifest and the exact bytes of every owned patch."""
    manifest = load_customization_manifest(manifest_path)
    overlay_paths = _validate_overlay_coverage(manifest, repo_root, patch_dir)

    canonical_manifest = dict(manifest)
    canonical_manifest["overlay_files"] = sorted(manifest["overlay_files"])
    manifest_bytes = json.dumps(
        canonical_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    hasher = hashlib.sha256()
    _hash_component(hasher, "customization_manifest.json", manifest_bytes)
    for relative in sorted(overlay_paths):
        _hash_component(hasher, relative, overlay_paths[relative].read_bytes())
    return hasher.hexdigest()


def validate_customization_contract(
    *,
    installed_version: str | None = None,
    manifest_path: Path = MANIFEST_PATH,
    repo_root: Path = REPO_ROOT,
    patch_dir: Path = PATCH_DIR,
) -> str:
    """Check the installed upstream version and full overlay consistency."""
    manifest = load_customization_manifest(manifest_path)
    upstream = manifest["upstream"]
    distribution = upstream["distribution"]
    expected_version = upstream["version"]

    if installed_version is None:
        try:
            installed_version = metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            message = f"Required upstream distribution is not installed: {distribution}"
            raise CustomizationContractError(message) from exc

    if installed_version != expected_version:
        raise CustomizationContractError(
            f"Unsupported {distribution} version: expected {expected_version}, found {installed_version}. "
            "Update the customization manifest only after validating every overlay patch against the new upstream."
        )

    return customization_fingerprint(
        manifest_path=manifest_path,
        repo_root=repo_root,
        patch_dir=patch_dir,
    )


def main() -> int:
    """Print a compact, reproducible fingerprint for manual verification."""
    manifest = load_customization_manifest()
    fingerprint = validate_customization_contract()
    upstream = manifest["upstream"]
    print(f'{upstream["distribution"]}=={upstream["version"]} overlay={fingerprint}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
