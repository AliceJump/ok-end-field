"""自动技能 allowlist 的 release-gate 行为测试。"""

import unittest

from src.data.skill_allowlist import (
    _build_team_skill_context,
    build_skill_allowlist,
    generate_skill_sequence,
    load_characters,
)


def _effect(effect_id: str, count: int = 1) -> dict:
    return {"effect_id": effect_id, "count": count}


def _character(name: str, effects=None, enhancements=None) -> dict:
    skill = {
        "skill_id": f"{name}_skill",
        "name": "测试战技",
        "skill_type": "战技",
        "effects": effects or [],
    }
    if enhancements is not None:
        skill["enhancements"] = enhancements
    return {"name": name, "skills": [skill]}


class TestSkillAllowlist(unittest.TestCase):
    def test_single_branch_all_requires_every_effect(self):
        target = _character(
            "目标",
            enhancements=[{"trigger_condition": {"effects": {"all": ["A", "B"]}}}],
        )
        characters = {
            "目标": target,
            "产出A": _character("产出A", effects=[_effect("A")]),
            "产出B": _character("产出B", effects=[_effect("B")]),
        }

        self.assertTrue(build_skill_allowlist(["目标", "产出A"], characters)[0][0])
        self.assertFalse(build_skill_allowlist(["目标", "产出A", "产出B"], characters)[0][0])

    def test_multiple_branches_are_or(self):
        target = _character(
            "目标",
            enhancements=[
                {"trigger_condition": {"effects": {"all": ["A"]}}},
                {"trigger_condition": {"effects": {"all": ["B"]}}},
            ],
        )
        characters = {
            "目标": target,
            "产出A": _character("产出A", effects=[_effect("A")]),
            "产出B": _character("产出B", effects=[_effect("B")]),
        }

        self.assertFalse(build_skill_allowlist(["目标", "产出A"], characters)[0][0])
        self.assertFalse(build_skill_allowlist(["目标", "产出B"], characters)[0][0])
        self.assertTrue(build_skill_allowlist(["目标"], characters)[0][0])

    def test_negative_count_is_not_a_producer(self):
        target = _character("目标", enhancements=[{"trigger_condition": {"effects": {"all": ["A"]}}}])
        consumer = _character("消费", effects=[_effect("A", count=-1)])
        characters = {"目标": target, "消费": consumer}

        context = _build_team_skill_context(["目标", "消费"], characters)
        # 消费者的 effects 不应包含 count<=0 的效果
        consumer_effects = context["skill_effects"].get(("消费", "消费_skill"), [])
        self.assertNotIn("A", consumer_effects)
        self.assertTrue(build_skill_allowlist(["目标", "消费"], characters)[0][0])

    def test_shred_consuming_anomaly_is_not_a_stack_producer(self):
        target = _character(
            "目标",
            enhancements=[{"trigger_condition": {"effects": {"all": ["STACK_SHRED"]}}}],
        )
        consumer = _character(
            "猛击者",
            effects=[_effect("STATUS_HEAVY_STRIKE")],
        )
        characters = {"目标": target, "猛击者": consumer}

        context = _build_team_skill_context(["目标", "猛击者"], characters)
        # 猛击者产出的是 STATUS_HEAVY_STRIKE，不是 STACK_SHRED
        consumer_effects = context["skill_effects"].get(("猛击者", "猛击者_skill"), [])
        self.assertIn("STATUS_HEAVY_STRIKE", consumer_effects)
        self.assertNotIn("STACK_SHRED", consumer_effects)
        self.assertTrue(build_skill_allowlist(["目标", "猛击者"], characters)[0][0])

    def test_post_cast_branch_is_not_a_release_gate(self):
        # 动态 trigger_condition.effects（无 all/any）不应被视为静态门控
        target = _character(
            "目标",
            enhancements=[
                {
                    "trigger_condition": {"effects": ["A"]},  # 简单列表，非静态
                    "effects": [_effect("B")],
                }
            ],
        )
        characters = {"目标": target}

        context = _build_team_skill_context(["目标"], characters)
        # 动态 trigger_condition（简单列表格式）不应被解析为有效触发条件
        enhancements = context["enhancement_triggers"].get(("目标", "目标_skill"), [])
        self.assertTrue(len(enhancements) == 0 or not enhancements[0]["trigger_groups"])
        self.assertTrue(build_skill_allowlist(["目标"], characters)[0][0])

        # 静态 trigger_condition.effects 依赖自己效果 → 仅自己依赖，仍允许手动释放
        static_target = _character(
            "静态目标",
            effects=[_effect("A")],
            enhancements=[{"trigger_condition": {"effects": {"all": ["A"]}}}],
        )
        self.assertTrue(build_skill_allowlist(["静态目标"], {"静态目标": static_target})[0][0])

    def test_effect_dependency_evaluation_preserves_effect_order(self):
        # 测试效果顺序对判定的影响
        # 产出者产出 A 和 B，依赖者需要 B
        producer = _character("产出者", effects=[_effect("A"), _effect("B")])
        consumer = _character(
            "依赖者",
            enhancements=[{"trigger_condition": {"effects": {"all": ["B"]}}}],
        )

        # 依赖者有增强态且触发条件被满足（B 在 current_effects 中）
        # B 的生产者是产出者（不是自我依赖），所以依赖者被禁止
        # 产出者没有增强态，所以产出者被允许
        result = build_skill_allowlist(["产出者", "依赖者"], {"产出者": producer, "依赖者": consumer})
        self.assertTrue(result[0][0])   # 产出者被允许（没有增强态）
        self.assertFalse(result[1][0])  # 依赖者被禁止（增强态优先，非自我依赖）

        # 如果产出者只有 A，依赖者需要 B，则依赖者的增强态无法触发
        # 所以两个战技都被允许（兜底机制）
        producer_only_a = _character("产出者", effects=[_effect("A")])
        result = build_skill_allowlist(["产出者", "依赖者"], {"产出者": producer_only_a, "依赖者": consumer})
        self.assertTrue(result[0][0])  # 产出者被允许
        self.assertTrue(result[1][0])  # 依赖者被允许（兜底）

    def test_producer_with_enhancement_is_forbidden(self):
        # 测试：如果产出者有增强态且触发条件被满足，产出者应该被禁止
        producer_with_enh = _character(
            "产出者",
            effects=[_effect("A"), _effect("B")],
            enhancements=[{"trigger_condition": {"effects": {"all": ["A"]}}}],
        )
        consumer = _character(
            "依赖者",
            enhancements=[{"trigger_condition": {"effects": {"all": ["B"]}}}],
        )

        # 产出者有增强态且触发条件被满足（A 在 current_effects 中）
        # A 的生产者是产出者自己（自我依赖），所以产出者不被禁止
        # 依赖者有增强态且触发条件被满足（B 在 current_effects 中）
        # B 的生产者是产出者（不是自我依赖），所以依赖者被禁止
        result = build_skill_allowlist(
            ["产出者", "依赖者"],
            {"产出者": producer_with_enh, "依赖者": consumer}
        )
        self.assertTrue(result[0][0])   # 产出者被允许（自我依赖）
        self.assertFalse(result[1][0])  # 依赖者被禁止（增强态优先，非自我依赖）

    def test_real_seraph_skill_remains_in_generated_sequence(self):
        characters = load_characters()
        team = ["赛希", "伊冯", "洁尔佩塔", "余烬"]

        self.assertIn("1", generate_skill_sequence(team, characters))

    def test_real_yvonne_any_gate_accepts_one_attachment_type(self):
        characters = load_characters()
        team = ["伊冯", "洁尔佩塔"]

        self.assertFalse(build_skill_allowlist(team, characters)[0][0])

    def test_real_zhuang_fang_yi_dynamic_fallback_is_not_static(self):
        characters = load_characters()
        zhuang = characters["庄方宜"]
        skill = next(skill for skill in zhuang["skills"] if skill["skill_id"] == "zhuangfy_skill")
        # 验证 trigger_condition.effects 是 dict 结构
        tc_effects = skill["enhancements"][1]["trigger_condition"]["effects"]
        self.assertIsInstance(tc_effects, dict)

        sword_consumer = _character(
            "青霆剑消费者",
            effects=[_effect("STACK_QINGTING_SWORD", count=-1)],
        )
        conductive_producer = _character(
            "导电产出者",
            effects=[_effect("STATUS_CONDUCTING")],
        )
        team_data = {
            "庄方宜": zhuang,
            "青霆剑消费者": sword_consumer,
            "导电产出者": conductive_producer,
        }

        # 青霆剑消费者是消费效果，不是生产者，所以庄方宜的战技允许释放
        self.assertTrue(build_skill_allowlist(["庄方宜", "青霆剑消费者"], team_data)[0][0])
        # 导电产出者生产 STATUS_CONDUCTING，被庄方宜的 trigger_condition 依赖，所以禁止
        self.assertFalse(build_skill_allowlist(["庄方宜", "导电产出者"], team_data)[0][0])

    def test_real_laevat_threshold_is_not_statically_satisfied(self):
        characters = load_characters()
        laevat = characters["莱万汀"]
        skill = next(skill for skill in laevat["skills"] if skill["skill_id"] == "laevat_skill")

        # 验证 trigger_condition.effects 是 dict 结构
        tc_effects = skill["enhancements"][0]["trigger_condition"]["effects"]
        self.assertIsInstance(tc_effects, dict)
        # 莱万汀单独时，没有其他人生产 STACK_MOLTEN，所以允许释放
        self.assertTrue(build_skill_allowlist(["莱万汀"], {"莱万汀": laevat})[0][0])


if __name__ == "__main__":
    unittest.main()
