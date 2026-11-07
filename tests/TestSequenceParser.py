import unittest

from src.core.sequence_parser import parse_int_sequence, parse_sequence, parse_sequence_list


class TestSequenceParser(unittest.TestCase):
    def test_parse_sequence_list_splits_each_item_by_comma(self):
        self.assertEqual(
            parse_sequence_list(["刻写,助剂", " 理智，高阶 ", "", "折金票"]),
            ["刻写", "助剂", "理智", "高阶", "折金票"],
        )

    def test_parse_sequence_list_accepts_string_and_none(self):
        self.assertEqual(parse_sequence_list("刻写, 助剂"), ["刻写", "助剂"])
        self.assertEqual(parse_sequence_list(None), [])

    def test_parse_sequence_list_keeps_commas_inside_regex_syntax(self):
        self.assertEqual(
            parse_sequence_list(["货品[甲,乙]", ".{2,4}", r"a\,b", "(甲,乙)", "刻写,助剂"]),
            ["货品[甲,乙]", ".{2,4}", r"a\,b", "(甲,乙)", "刻写", "助剂"],
        )
        self.assertEqual(
            parse_sequence_list("货品[甲,乙], 刻写，.{2,4}"),
            ["货品[甲,乙]", "刻写", ".{2,4}"],
        )
        self.assertEqual(parse_sequence_list([r"[\d],foo"]), [r"[\d]", "foo"])

    def test_parse_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(
            parse_sequence(" 干员经验， 干员进阶, , 钱币收集  ,"),
            ["干员经验", "干员进阶", "钱币收集"],
        )

    def test_parse_int_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(parse_int_sequence(" 36，14, ,108 "), [36, 14, 108])


if __name__ == "__main__":
    unittest.main()
