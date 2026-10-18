from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.themes.theme_pack import ThemePackError, import_theme_pack, load_active_theme, read_theme_pack, select_theme

ROOT = Path(__file__).resolve().parents[1]


class TestThemePack(unittest.TestCase):
    def _archive(self, path, *, manifest=None, qss="QPushButton { color: #abcdef; }", extra=None):
        """Create a small fixture ZIP with configurable invalid members."""
        manifest = manifest or {"schema_version": 1, "id": "sample", "name": "Example", "version": "1.0"}
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("theme.json", json.dumps(manifest))
            archive.writestr("style.qss", qss)
            if extra:
                archive.writestr(extra, "unexpected")

    def test_install_select_reload_and_reset(self):
        """Verify that importing is opt-in and resetting removes selection."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "themes"
            archive = Path(directory) / "source.zip"
            self._archive(archive)
            self.assertIsNone(load_active_theme(home))
            installed = import_theme_pack(archive, home)
            self.assertEqual(installed.id, "sample")
            self.assertIsNone(load_active_theme(home))
            select_theme("sample", home)
            active = load_active_theme(home)
            self.assertEqual(active.fingerprint, installed.fingerprint)
            self.assertIn("#abcdef", active.qss)
            select_theme(None, home)
            self.assertIsNone(load_active_theme(home))

    def test_invalid_install_does_not_overwrite_an_existing_theme(self):
        """Retain the valid theme when an invalid replacement is rejected."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "themes"
            archive = Path(directory) / "source.zip"
            self._archive(archive)
            import_theme_pack(archive, home)
            installed = home / "sample.zip"
            original = installed.read_bytes()
            self._archive(archive, qss="QWidget { image: url(file:///secret); }")
            with self.assertRaises(ThemePackError):
                import_theme_pack(archive, home)
            self.assertEqual(installed.read_bytes(), original)
            self.assertEqual(list(home.glob(".install-*.zip")), [])

    def test_sample_theme_pack_sources_are_valid(self):
        """Ensure the shipped sample sources are parseable as a theme."""
        sample = ROOT / "theme-packs" / "aurora-night"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "sample.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for name in ("theme.json", "style.qss"):
                    output.write(sample / name, arcname=name)
            self.assertEqual(read_theme_pack(archive).id, "aurora-night")

    def test_rejects_unexpected_zip_member(self):
        """Reject any archive trying to add another file or traversal path."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            self._archive(archive, extra="../hack.py")
            with self.assertRaisesRegex(ThemePackError, "unsupported member"):
                read_theme_pack(archive)

    def test_rejects_unsafe_theme_ids_and_external_qss(self):
        """Prevent path traversal IDs and QSS resource references."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            self._archive(
                archive,
                manifest={"schema_version": 1, "id": "../escape", "name": "Example", "version": "1.0"},
            )
            with self.assertRaises(ThemePackError):
                import_theme_pack(archive, Path(directory) / "installed")
            self._archive(archive, qss="QWidget { image: url(file:///secret); }")
            with self.assertRaisesRegex(ThemePackError, "external resources"):
                read_theme_pack(archive)

    def test_duplicate_members_are_rejected(self):
        """Reject duplicate ZIP names instead of silently using one."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "duplicate.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("theme.json", '{"schema_version": 1, "id": "test", "name": "Test", "version": "1"}')
                output.writestr("theme.json", '{"schema_version": 1, "id": "bad", "name": "Bad", "version": "1"}')
                output.writestr("style.qss", "QLabel { color: red; }")
            with self.assertRaisesRegex(ThemePackError, "requires unique theme.json"):
                read_theme_pack(archive)

    def test_oversized_qss_is_rejected(self):
        """Refuse declared decompressed QSS content beyond the size cap."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "oversized.zip"
            self._archive(archive, qss="QLabel { color: red; }\n" * 7000)
            with self.assertRaisesRegex(ThemePackError, "too large"):
                read_theme_pack(archive)

    def test_nailong_pack_contains_real_images_and_correct_digest(self):
        """Verify the shipped Nailong ZIP includes all three embedded WebPs."""
        archive = ROOT / "theme-packs" / "nailong" / "nailong.okef-theme.zip"
        pack = read_theme_pack(archive)
        self.assertEqual(pack.kind, "nailong")
        self.assertEqual(pack.id, "nailong")
        self.assertEqual(len(pack.assets), 3)
        for image in pack.assets.values():
            self.assertTrue(image.startswith(b"RIFF"))
            self.assertEqual(image[8:12], b"WEBP")
        with tempfile.TemporaryDirectory() as directory:
            installed = import_theme_pack(archive, Path(directory))
            self.assertEqual(installed.fingerprint, pack.fingerprint)

    def test_builder_preserves_asset_directories(self):
        """Rebuilding from checked-in sources yields a loadable theme ZIP."""
        from scripts.themes.build_nailong_theme import MEMBERS, SOURCE, build_theme_zip

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nailong.zip"
            build_theme_zip(path, SOURCE)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(tuple(archive.namelist()), MEMBERS)
            self.assertEqual(read_theme_pack(path).id, "nailong")

    def test_nailong_package_needs_all_images(self):
        """Reject half-installed mascot packages before changing UI state."""
        manifest = {
            "schema_version": 1,
            "id": "nailong",
            "name": "Nailong",
            "version": "1.0",
            "kind": "nailong",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            self._archive(archive, manifest=manifest)
            with self.assertRaisesRegex(ThemePackError, "require background"):
                read_theme_pack(archive)

    def test_qproperty_cannot_disable_the_reset_button(self):
        """Do not allow QSS to change runtime widget state via qproperty."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            self._archive(archive, qss="QPushButton { qproperty-enabled: false; }")
            with self.assertRaisesRegex(ThemePackError, "qproperty"):
                read_theme_pack(archive)

    def test_qss_hex_escapes_cannot_hide_sensitive_properties(self):
        """Reject Qt hex escapes before the QSS parser expands them."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "escaped.zip"
            for qss in (
                r"QPushButton { qpropert\79-enabled: false; }",
                r"QWidget { image: \75rl(file:///secret); }",
            ):
                with self.subTest(qss=qss):
                    self._archive(archive, qss=qss)
                    with self.assertRaisesRegex(ThemePackError, "external resources"):
                        read_theme_pack(archive)

    def test_unimplemented_compression_is_rejected(self):
        """Unsupported ZIP codecs report a normal theme validation error."""
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.zip"
            self._archive(archive)
            payload = bytearray(archive.read_bytes())
            import struct

            local = payload.find(b"PK\x03\x04")
            central = payload.find(b"PK\x01\x02")
            struct.pack_into("<H", payload, local + 8, 99)
            struct.pack_into("<H", payload, central + 10, 99)
            archive.write_bytes(payload)
            with self.assertRaisesRegex(ThemePackError, "compression"):
                read_theme_pack(archive)

    def test_corrupt_selection_and_mismatched_id_fail_closed(self):
        """Reject corrupt selections or renamed installed archives."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "active.json").write_text('{"id":"../danger"}', encoding="utf-8")
            with self.assertRaises(ThemePackError):
                load_active_theme(home)
            archive = home / "different.zip"
            self._archive(archive)
            with self.assertRaisesRegex(ThemePackError, "does not match"):
                select_theme("different", home)


if __name__ == "__main__":
    unittest.main()
