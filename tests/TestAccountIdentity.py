import unittest

from src.tasks.account.account_identity import (
    account_name_from_line,
    visible_account_label,
    visible_account_parts,
    visible_account_pattern,
)


class TestAccountIdentity(unittest.TestCase):
    def test_ignores_everything_after_first_comma(self):
        self.assertEqual(account_name_from_line("13812345678,anything here"), "13812345678")
        self.assertEqual(account_name_from_line("13812345678,"), "13812345678")

    def test_visible_account_label_uses_prefix_and_suffix(self):
        self.assertEqual(visible_account_parts("13812345678"), ("138", "5678"))
        self.assertEqual(visible_account_label("13812345678"), "138****5678")

    def test_visible_pattern_matches_masked_account_text(self):
        pattern = visible_account_pattern("13812345678")

        self.assertIsNotNone(pattern.search("138****5678"))
        self.assertIsNotNone(pattern.search("138  ****  5678"))
        self.assertIsNone(pattern.search("139****5678"))
        self.assertIsNone(pattern.search("138****0000"))


if __name__ == "__main__":
    unittest.main()
