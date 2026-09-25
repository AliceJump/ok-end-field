import unittest

from scripts.i18n.sync_official_i18n_langs import guard_anchor_official_match


class AnchorGuardTests(unittest.TestCase):
    def test_width_equivalents_match_without_accepting_derived_prefix(self):
        ranked = [("key", 1)]
        self.assertTrue(guard_anchor_official_match("储藏箱Ａ１", ranked, {"zh_CN": {"key": "储藏箱A1"}}))
        self.assertTrue(guard_anchor_official_match("储藏箱Ａ１", ranked, {"zh_CN": {"key": "协议空间·储藏箱A1"}}))
        self.assertTrue(guard_anchor_official_match("干员经验", ranked, {"zh_CN": {"key": "协议空间·干员经验"}}))
        self.assertFalse(guard_anchor_official_match("干员经验", ranked, {"zh_CN": {"key": "干员经验素材"}}))


if __name__ == "__main__":
    unittest.main()
