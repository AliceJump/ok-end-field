from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from src.patches.customization_contract import (
    CustomizationContractError,
    customization_fingerprint,
    load_customization_manifest,
    validate_customization_contract,
)

ROOT = Path(__file__).resolve().parents[1]


class TestCustomizationContract(unittest.TestCase):
    def test_manifest_matches_project_and_release_ok_script_pins(self):
        """Ensure declared upstream matches project dependencies and the lockfile."""
        manifest = load_customization_manifest()
        expected = f'{manifest["upstream"]["distribution"]}=={manifest["upstream"]["version"]}'

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(expected, pyproject["project"]["dependencies"])

        # CI rewrites requirements.txt during inline_ok_requirements. Validate the
        # immutable dependency lock instead of that mutable installation artifact.
        locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        upstream_versions = {
            package["version"]
            for package in locked["package"]
            if package["name"] == manifest["upstream"]["distribution"]
        }
        self.assertEqual(upstream_versions, {manifest["upstream"]["version"]})

    def test_manifest_tracks_every_patch_source(self):
        """Ensure new or removed runtime patches update the owned manifest."""
        manifest = load_customization_manifest()
        declared = set(manifest["overlay_files"])
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src/patches").glob("*.py")
            if path.name not in {"__init__.py", "customization_contract.py"}
        }
        self.assertEqual(actual, declared)

    def test_entrypoints_validate_contract_before_installing_patches(self):
        """Ensure validation precedes every startup patch import and install."""
        for name in ("main.py", "main_debug.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("validate_customization_contract()", source)
                validation_index = source.index("validate_customization_contract()")
                for marker in (
                    "from src.patches.pre_config_patch import",
                    "from src.patches.qfluent_mute_promo_patch import",
                    "from src.patches.startup_patches import",
                    "install_pre_config_patch()",
                    "install_mute_promo_patch()",
                    "install_startup_patches()",
                ):
                    with self.subTest(marker=marker):
                        self.assertLess(validation_index, source.index(marker))

    def test_current_contract_validates_with_declared_version(self):
        """Ensure the declared project overlay computes a valid fingerprint."""
        manifest = load_customization_manifest()
        fingerprint = validate_customization_contract(installed_version=manifest["upstream"]["version"])
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_rejects_upstream_version_mismatch(self):
        """Reject startup against an unsupported upstream release."""
        with self.assertRaisesRegex(CustomizationContractError, "Unsupported ok-script version"):
            validate_customization_contract(installed_version="0.0.0")

    def test_fingerprint_is_stable_for_manifest_order_and_changes_with_source(self):
        """Ensure ordering is stable while a byte change updates the digest."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            patch_dir = root / "src/patches"
            patch_dir.mkdir(parents=True)
            (patch_dir / "alpha_patch.py").write_text("VALUE = 1\n", encoding="utf-8")
            (patch_dir / "beta_patch.py").write_text("VALUE = 2\n", encoding="utf-8")
            manifest_path = patch_dir / "customization_manifest.json"
            data = {
                "schema_version": 1,
                "upstream": {"distribution": "ok-script", "version": "test"},
                "overlay_files": [
                    "src/patches/alpha_patch.py",
                    "src/patches/beta_patch.py",
                ],
            }
            manifest_path.write_text(json.dumps(data), encoding="utf-8")

            first = customization_fingerprint(
                manifest_path=manifest_path,
                repo_root=root,
                patch_dir=patch_dir,
            )

            data["overlay_files"].reverse()
            manifest_path.write_text(json.dumps(data), encoding="utf-8")
            reordered = customization_fingerprint(
                manifest_path=manifest_path,
                repo_root=root,
                patch_dir=patch_dir,
            )
            self.assertEqual(first, reordered)

            (patch_dir / "alpha_patch.py").write_text("VALUE = 3\n", encoding="utf-8")
            changed = customization_fingerprint(
                manifest_path=manifest_path,
                repo_root=root,
                patch_dir=patch_dir,
            )
            self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
