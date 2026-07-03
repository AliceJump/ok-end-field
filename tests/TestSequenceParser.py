# -*- coding: utf-8 -*-
import unittest

from src.tasks.sequence_parser import parse_int_sequence, parse_sequence


class TestSequenceParser(unittest.TestCase):
    def test_parse_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(
            parse_sequence(" 干员经验， 干员进阶, , 钱币收集  ,"),
            ["干员经验", "干员进阶", "钱币收集"],
        )

    def test_parse_int_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(parse_int_sequence(" 36，14, ,108 "), [36, 14, 108])


if __name__ == "__main__":
    unittest.main()
