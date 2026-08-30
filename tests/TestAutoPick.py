import unittest

from src.tasks.trigger.auto_pick_rules import (
    PICKABLE_PLANT_VARIANTS,
    WHITE_LIST,
    should_skip_pick,
)


class TestAutoPickRules(unittest.TestCase):
    def test_producible_plants_are_skipped_by_default(self):
        self.assertTrue(should_skip_pick("荞花"))
        self.assertTrue(should_skip_pick("获得锦草 x3"))
        self.assertTrue(should_skip_pick("酮化灌木"))

    def test_producible_plant_filter_can_be_disabled(self):
        self.assertFalse(should_skip_pick("荞花", skip_producible=False))
        self.assertTrue(should_skip_pick("激活箱子"))

    def test_special_plant_variants_are_not_skipped_by_default(self):
        for item_name in PICKABLE_PLANT_VARIANTS:
            self.assertFalse(should_skip_pick(item_name))

    def test_blacklist_always_wins(self):
        self.assertTrue(should_skip_pick("协议核心", skip_producible=False))

    def test_whitelist_covers_non_producible_items(self):
        for item in (
            # 采集物
            "血菌",
            "星门菌",
            "武陵石",
            "燎石",
            "受蚀玉化叶",
            "岩天使叶",
            "轻黯石",
            "荆刺芽针",
            "蓬茸锦草",
            "映火荞花",
            "黯银柑实",
            "蓝铁矿",
            "紫晶矿",
            "源矿",
            "原木",
            # 掉落物
            "兽肉",
            "虫肉",
            "新笋",
            "虬兽的须",
            "彪兽的长绒",
            "球刺兽的肝脏",
            "水灯虫的灯坠",
            "软骨碎屑",
            "驮兽粪便",
            "大斧角",
            "异色油脂",
            "刺鼻干肉",
            "草籽干粉",
            "苦涩麦粉",
            "高能异香石",
            "天然晶城锭",
            "导能肖像石",
            "速成陈酿",
            "甜腻黑水",
            "天然气泡水",
            "坚韧的水",
            "浸雾大弩",
            "破城者拳甲",
            "崩碎斧刃",
        ):
            self.assertTrue(
                any(entry in item for entry in WHITE_LIST),
                f"{item} 应被子串匹配到白名单",
            )

    def test_whitelist_variants_match_by_substring(self):
        for text in ("轻红柱状菌", "重黯石", "纯晶多齿叶", "至晶多齿叶"):
            self.assertTrue(
                any(entry in text for entry in WHITE_LIST),
                f"{text} 应被子串匹配到白名单",
            )


if __name__ == "__main__":
    unittest.main()
