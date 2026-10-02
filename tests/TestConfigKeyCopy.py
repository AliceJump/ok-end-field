import os
import tempfile
import unittest
from unittest.mock import patch

from ok.util.file import read_json_file, write_json_file

from src.core import config_migration, global_config_store


class TestConfigKeyCopy(unittest.TestCase):
    def migrate(self, directory, *, replace_defaults):
        module = global_config_store if replace_defaults else config_migration
        with patch.object(module, "config_path", side_effect=lambda name: os.path.join(directory, name)):
            if replace_defaults:
                module._migrate_key_names_in_file("Task", {"old": "new"}, {"new": ""})
            else:
                module.migrate_config_file_keys("Task", {"old": "new"})

    def test_file_migrations_preserve_their_distinct_default_policies(self):
        cases = [
            ({"old": "user", "new": ""}, {"old": "user", "new": ""}, {"old": "user", "new": "user"}),
            ({"old": ""}, {"old": "", "new": ""}, {"old": ""}),
            ({"old": "user", "new": "chosen"}, {"old": "user", "new": "chosen"}, {"old": "user", "new": "chosen"}),
            ({"new": "chosen"}, {"old": "chosen", "new": "chosen"}, {"old": "chosen", "new": "chosen"}),
        ]
        for initial, missing_only, replace_default in cases:
            for replace_defaults, expected in ((False, missing_only), (True, replace_default)):
                with self.subTest(initial=initial, replace_defaults=replace_defaults), tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "Task.json")
                    write_json_file(path, initial)
                    self.migrate(tmp, replace_defaults=replace_defaults)
                    self.assertEqual(read_json_file(path), expected)
                    self.migrate(tmp, replace_defaults=replace_defaults)
                    self.assertEqual(read_json_file(path), expected)

    def test_missing_or_invalid_config_files_are_not_written(self):
        for replace_defaults in (False, True):
            with self.subTest(replace_defaults=replace_defaults), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "Task.json")
                self.migrate(tmp, replace_defaults=replace_defaults)
                self.assertFalse(os.path.exists(path))
                write_json_file(path, ["invalid config"])
                self.migrate(tmp, replace_defaults=replace_defaults)
                self.assertEqual(read_json_file(path), ["invalid config"])
