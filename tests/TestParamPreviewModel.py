"""param_preview_model 归属模型的单测（对齐扩展 groupPopContent 语义）。

覆盖设计文档 2.4 节场景：深链拍平（BattleConfig 结构）、自引用组、
悬空引用、环、被控源在根源前的 fields 顺序（全局 Battle Config 场景）、
无组无规则隐藏眼睛、bool 规则值翻译、整组吸收则跳过。
"""

import unittest

from src.core.param_preview_model import (
    COND_NEST_CAP,
    build_param_preview,
    rule_value_label,
)


def identity_tr(key):
    return key


def make_task(config_order, config_type=None, default_config_group=None,
              default_config=None):
    """构造任务数据：config 的键序即 fields 顺序，值统一 False/[] 占位。"""
    config = {}
    for key in config_order:
        config[key] = default_config.get(key) if default_config else False
    if default_config is None:
        default_config = dict(config)
    return build_param_preview(
        config,
        config_type or {},
        default_config,
        default_config_group or {},
        identity_tr,
    )


def flat_items(nodes):
    """递归收集节点树里全部 item key。"""
    keys = []
    for node in nodes:
        if node["type"] == "item":
            keys.append(node["key"])
        else:
            if "nodes" in node:
                keys.extend(flat_items(node["nodes"]))
            for rule in node.get("rules", []):
                keys.extend(flat_items(rule["nodes"]))
    return keys


def find_cond_blocks(blocks):
    """递归收集全部条件组块（含嵌套在规则子项里的）。"""
    found = []
    for block in blocks:
        if block["type"] == "cond":
            found.append(block)
        for rule in block.get("rules", []):
            found.extend(find_cond_blocks(rule["nodes"]))
        found.extend(find_cond_blocks(block.get("nodes", [])))
    return found


# 与 src/core/BattleConfig.py 同构的最小战斗配置 fixture（4 节点深链）
BATTLE_MODE = "使用独立配置"
SKILL_ALLOWLIST = "自动技能列表"
ENABLE_ROTATION = "启用排轴"
ROTATION_SEQUENCE = "排轴序列"
COND_ENABLED = "启用实时条件"
COND_SEQUENCE = "实时条件序列"

BATTLE_KEYS = [BATTLE_MODE, ENABLE_ROTATION, ROTATION_SEQUENCE,
               COND_ENABLED, COND_SEQUENCE, SKILL_ALLOWLIST]


def battle_config_type():
    return {
        BATTLE_MODE: {"sub_configs": {True: [ENABLE_ROTATION, ROTATION_SEQUENCE,
                                             COND_ENABLED, COND_SEQUENCE]}},
        ENABLE_ROTATION: {"sub_configs": {True: [ROTATION_SEQUENCE]}},
        COND_ENABLED: {"sub_configs": {True: [COND_SEQUENCE]}},
        SKILL_ALLOWLIST: {"sub_configs": {False: [ENABLE_ROTATION, COND_ENABLED]}},
    }


class TestRuleValueLabel(unittest.TestCase):
    def test_bool_choice_translates_on_off(self):
        self.assertEqual(rule_value_label(True, True, identity_tr), "开")
        self.assertEqual(rule_value_label(False, True, identity_tr), "关")

    def test_bool_string_choice_translates_when_parent_bool(self):
        self.assertEqual(rule_value_label("True", True, identity_tr), "开")
        self.assertEqual(rule_value_label("False", True, identity_tr), "关")

    def test_string_choice_kept_when_parent_not_bool(self):
        self.assertEqual(rule_value_label("True", False, identity_tr), "True")
        self.assertEqual(rule_value_label("选项A", False, identity_tr), "选项A")


class TestParamPreviewModel(unittest.TestCase):
    def test_deep_chain_battle_fixture(self):
        """战斗配置 fixture：根显隐源唯一，嵌套组按层建立，字段全局只出现一次。"""
        preview = make_task(BATTLE_KEYS, battle_config_type())
        self.assertIsNotNone(preview)
        blocks = preview["blocks"]

        # 根显隐源只有「使用独立配置」，其余字段都是受控字段不开顶层组
        top_conds = [b for b in blocks if b["type"] == "cond"]
        self.assertEqual([b["key"] for b in top_conds], [BATTLE_MODE])

        # 使用独立配置的规则子项在 depth 1 渲染：启用排轴/启用实时条件
        # 各自建嵌套组（depth 1 < cap 2）
        cond_blocks = find_cond_blocks(blocks)
        self.assertIn(BATTLE_MODE, [b["key"] for b in cond_blocks])
        self.assertIn(ENABLE_ROTATION, [b["key"] for b in cond_blocks])
        self.assertIn(COND_ENABLED, [b["key"] for b in cond_blocks])

        # 一个字段全弹层只出现一次
        all_keys = flat_items(blocks)
        self.assertEqual(len(all_keys), len(set(all_keys)))
        self.assertIn(ROTATION_SEQUENCE, all_keys)

        # 自动技能列表的受控子项已被根规则吸收渲染 → 整组跳过，
        # 落回其他参数当普通行（对齐扩展 hasPending 语义）
        others = [b for b in blocks if b["type"] == "others"]
        self.assertEqual(flat_items(others[0]["nodes"]), [SKILL_ALLOWLIST])

    def test_flat_badge_at_cap(self):
        """三层规则链：第 cap 层字段拍平为徽标，不再建嵌套组。"""
        preview = make_task(
            ["根源", "中继", "深层", "叶子"],
            config_type={
                "根源": {"sub_configs": {True: ["中继"]}},
                "中继": {"sub_configs": {True: ["深层"]}},
                "深层": {"sub_configs": {True: ["叶子"]}},
            },
        )
        cond_keys = [b["key"] for b in find_cond_blocks(preview["blocks"])]
        # 根源、中继建组；深层在 depth 2 触顶不再建组
        self.assertEqual(cond_keys, ["根源", "中继"])
        # 深层变成带摘要徽标的字段行，深层不再建嵌套组
        items = [n for b in find_cond_blocks(preview["blocks"])
                 for r in b["rules"] for n in r["nodes"] if n["type"] == "item"]
        deep = next(n for n in items if n["key"] == "深层")
        self.assertEqual(deep["badge"], "开→叶子")
        # 叶子不在深层组里渲染（只剩徽标摘要），但会落回其他参数作普通行
        leaf_items = [n for b in find_cond_blocks(preview["blocks"])
                      for r in b["rules"] for n in r["nodes"]
                      if n["type"] == "item" and n["key"] == "叶子"]
        self.assertEqual(leaf_items, [])
        others = [b for b in preview["blocks"] if b["type"] == "others"]
        self.assertEqual(flat_items(others[0]["nodes"]), ["叶子"])

    def test_controlled_source_before_root_keeps_chain(self):
        """被控字段（启用排轴）即使 fields 顺序排在根源前也不自己开顶层组。"""
        order = [ENABLE_ROTATION, ROTATION_SEQUENCE, BATTLE_MODE,
                 COND_ENABLED, COND_SEQUENCE, SKILL_ALLOWLIST]
        preview = make_task(order, battle_config_type())
        top_conds = [b for b in preview["blocks"] if b["type"] == "cond"]
        self.assertEqual([b["key"] for b in top_conds], [BATTLE_MODE])
        # 启用排轴不作为顶层条件组出现
        self.assertNotIn(ENABLE_ROTATION, [b["key"] for b in top_conds])

    def test_self_referencing_group(self):
        """自引用组：组 children 含自己 → 渲染字段行但不无限递归。"""
        group = "多账户模式"
        preview = make_task(
            [group, "账号A", "账号B"],
            default_config_group={group: [group, "账号A", "账号B"]},
        )
        static_blocks = [b for b in preview["blocks"] if b["type"] == "static"]
        self.assertEqual(len(static_blocks), 1)
        self.assertEqual(static_blocks[0]["name"], group)
        self.assertEqual(flat_items(static_blocks[0]["nodes"]),
                         [group, "账号A", "账号B"])

    def test_group_absorbs_own_sub_configs(self):
        """规则 1：组名字段自身的 sub_configs 子项吸收进组 children。"""
        group = "战斗设置"
        preview = make_task(
            [group, "完成通知"],
            config_type={group: {"sub_configs": {True: ["完成通知"]}}},
            default_config_group={group: []},
        )
        static_blocks = [b for b in preview["blocks"] if b["type"] == "static"]
        self.assertEqual(len(static_blocks), 1)
        self.assertEqual(flat_items(static_blocks[0]["nodes"]), ["完成通知"])

    def test_dangling_reference_skipped(self):
        """悬空引用：受控字段不存在时不渲染、不崩溃，父字段落回普通行。"""
        preview = make_task(
            ["开关A"],
            config_type={"开关A": {"sub_configs": {True: ["不存在的键"]}}},
        )
        # 规则子项无一处可渲染 → 整组跳过（对齐扩展 hasPending 语义）
        self.assertEqual(find_cond_blocks(preview["blocks"]), [])
        others = [b for b in preview["blocks"] if b["type"] == "others"]
        self.assertEqual(flat_items(others[0]["nodes"]), ["开关A"])

    def test_group_references_filtered_key_skipped(self):
        """静态分组引用被过滤/三处皆无的键：跳过不渲染，不崩溃（多账户模式）。"""
        preview = make_task(
            ["普通开关"],
            config_type={"普通开关": {}},
            default_config_group={"基础": ["普通开关", "多账户模式"]},
        )
        self.assertIsNotNone(preview)
        static = [b for b in preview["blocks"] if b["type"] == "static"]
        self.assertEqual(len(static), 1)
        self.assertEqual(flat_items(static[0]["nodes"]), ["普通开关"])

    def test_rule_with_partially_unrenderable_children(self):
        """规则子项部分悬空/hidden：只渲染存在的，不崩溃（启动技能点数）。"""
        preview = make_task(
            ["主开关", "真实开关"],
            config_type={
                "主开关": {"sub_configs": {True: ["真实开关", "启动技能点数", "隐藏开关"]}},
                "真实开关": {},
                "隐藏开关": {"hidden": True},
            },
        )
        cond_blocks = find_cond_blocks(preview["blocks"])
        self.assertEqual([b["key"] for b in cond_blocks], ["主开关"])
        rule_nodes = cond_blocks[0]["rules"][0]["nodes"]
        # 「启动技能点数」三处皆无、「隐藏开关」被 hidden 过滤 → 都跳过
        self.assertEqual(flat_items(rule_nodes), ["真实开关"])

    def test_cycle_protection(self):
        """A→B→A 纯环：seen 防护不无限递归，无根显隐源 → 弹层为空（None）。"""
        config_type = {
            "开关A": {"sub_configs": {True: ["开关B"]}},
            "开关B": {"sub_configs": {True: ["开关A"]}},
        }
        # 纯环里 A/B 互为下游，谁都当不了根显隐源（与扩展 collectControlled 一致）
        self.assertIsNone(make_task(["开关A", "开关B"], config_type))

    def test_cycle_with_extra_field_still_renders(self):
        """环 + 普通字段：环字段无根可开组，落回其他参数后按普通行 + 子组渲染。"""
        config_type = {
            "开关A": {"sub_configs": {True: ["开关B"]}},
            "开关B": {"sub_configs": {True: ["开关A"]}},
        }
        preview = make_task(["开关A", "开关B", "普通开关"], config_type)
        # 顶层不出现条件组（A/B 都不是根显隐源）
        top_conds = [b for b in preview["blocks"] if b["type"] == "cond"]
        self.assertEqual(top_conds, [])
        # A 在其他参数里作普通行，其规则仍能开一组（B 已可渲染）
        cond_blocks = find_cond_blocks(preview["blocks"])
        self.assertEqual([b["key"] for b in cond_blocks], ["开关A"])
        all_keys = flat_items(preview["blocks"])
        self.assertEqual(len(all_keys), len(set(all_keys)))
        self.assertEqual(sorted(all_keys), ["开关A", "开关B", "普通开关"])

    def test_no_groups_no_rules(self):
        """无组无规则：有字段时收进「参数」伪组（扩展语义），无字段 → None。"""
        preview = make_task(["普通开关"])
        self.assertEqual(flat_items(preview["blocks"]), ["普通开关"])
        others = [b for b in preview["blocks"] if b["type"] == "others"]
        self.assertEqual(others[0]["name"], "参数")
        self.assertIsNone(make_task([]))

    def test_hidden_and_private_keys_excluded(self):
        """下划线/hidden/按钮键不进弹层。"""
        preview = make_task(
            ["可见", "_内部", "隐藏项", "按钮项", "普通开关"],
            config_type={
                "隐藏项": {"hidden": True},
                "按钮项": {"type": "button", "buttons": ["x"]},
            },
        )
        self.assertEqual(flat_items(preview["blocks"]), ["可见", "普通开关"])

    def test_others_section_without_groups_uses_plain_label(self):
        """无静态组时「其他参数」标签降级为「参数」。"""
        preview = make_task(["开关A", "开关B"])
        others = [b for b in preview["blocks"] if b["type"] == "others"]
        self.assertEqual(len(others), 1)
        self.assertEqual(others[0]["name"], "参数")

    def test_selector_skipped_when_all_children_absorbed(self):
        """整组吸收则跳过：BattleTask 的「配置选择」落回其他参数当普通行。"""
        groups = {"⭐组一": ["甲"], "⭐组二": ["乙"]}
        selector = "配置选择"
        preview = make_task(
            [selector, "甲", "乙"],
            config_type={
                selector: {
                    "type": "drop_down",
                    "options": list(groups),
                    "sub_configs": groups,
                },
            },
            default_config_group=dict(groups),
        )
        # 分组下拉的规则不该开条件组（子项全被静态组吸收）
        self.assertEqual(find_cond_blocks(preview["blocks"]), [])
        # 静态组渲染组内字段
        static_blocks = [b for b in preview["blocks"] if b["type"] == "static"]
        self.assertEqual([b["name"] for b in static_blocks], list(groups))
        # 「配置选择」落回其他参数
        others = [b for b in preview["blocks"] if b["type"] == "others"]
        self.assertEqual(flat_items(others[0]["nodes"]), [selector])

    def test_pure_group_label_not_rendered_as_field(self):
        """纯分组标签（不在 config/default_config 里）不作为字段渲染。"""
        preview = make_task(
            ["甲", "乙"],
            default_config_group={"⭐容器": ["甲", "乙"]},
        )
        self.assertEqual(flat_items(preview["blocks"]), ["甲", "乙"])

    def test_nested_cap_matches_constant(self):
        """嵌套 cap 常量为 2（第 3 层拍平），防止被无意改动。"""
        self.assertEqual(COND_NEST_CAP, 2)


if __name__ == "__main__":
    unittest.main()
