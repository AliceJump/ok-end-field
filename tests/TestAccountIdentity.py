import unittest

from src.tasks.account.account_identity import account_name_from_line, visible_account_label, visible_account_parts


class TestAccountIdentity(unittest.TestCase):
    def test_ignores_everything_after_first_comma(self):
        self.assertEqual(account_name_from_line("13812345678,anything here"), "13812345678")
        self.assertEqual(account_name_from_line("13812345678,"), "13812345678")

    def test_visible_account_label_uses_prefix_and_suffix(self):
        self.assertEqual(visible_account_parts("13812345678"), ("138", "5678"))
        self.assertEqual(visible_account_label("13812345678"), "138****5678")


if __name__ == "__main__":
    unittest.main()
