import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ok.util.config import Config

from src.tasks.account import account_scope_store


class TestAccountScopeStore(unittest.TestCase):
    def test_load_overrides_switches_paths_with_matching_mtimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            first_dir = Path(tmp) / "first"
            second_dir = Path(tmp) / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            for folder, value in ((first_dir, "first"), (second_dir, "second")):
                store = folder / "account_scoped_overrides.json"
                store.write_text(json.dumps({"account_list_text": value}), encoding="utf-8")
                os.utime(store, (1_700_000_000, 1_700_000_000))

            with (
                patch.object(account_scope_store, "_CACHE_PATH", None),
                patch.object(account_scope_store, "_CACHE_MTIME", object()),
                patch.object(account_scope_store, "_CACHE_DATA", {}),
            ):
                with patch.object(Config, "config_folder", str(first_dir)):
                    self.assertEqual(account_scope_store.load_overrides()["account_list_text"], "first")
                with patch.object(Config, "config_folder", str(second_dir)):
                    self.assertEqual(account_scope_store.load_overrides()["account_list_text"], "second")
                    self.assertEqual(account_scope_store.load_overrides()["account_list_text"], "second")

    def test_parse_account_list_keeps_only_username_before_comma(self):
        self.assertEqual(
            account_scope_store.parse_account_list_text("13812345678,ignored\n13987654321"),
            [{"username": "13812345678"}, {"username": "13987654321"}],
        )

    def test_sync_account_list_does_not_persist_comma_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(Config, "config_folder", tmp),
                patch.object(account_scope_store, "_CACHE_PATH", None),
                patch.object(account_scope_store, "_CACHE_MTIME", object()),
                patch.object(account_scope_store, "_CACHE_DATA", {}),
            ):
                summary = account_scope_store.sync_account_list_text("13812345678,ignored\n13987654321")
                stored = account_scope_store.load_overrides(force=True)

        self.assertEqual(summary["total_valid"], 2)
        self.assertNotIn("password_ignored_for_identity", summary)
        self.assertEqual(stored["account_list_text"], "13812345678\n13987654321")


if __name__ == "__main__":
    unittest.main()
