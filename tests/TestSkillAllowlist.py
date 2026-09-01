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
            enhancements=[{"release_gate": {"all": ["A", "B"]}}],
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
                {"release_gate": {"all": ["A"]}},
                {"release_gate": {"all": ["B"]}},
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
        target = _character("目标", enhancements=[{"release_gate": {"all": ["A"]}}])
        consumer = _character("消费", effects=[_effect("A", count=-1)])
        characters = {"目标": target, "消费": consumer}

        context = _build_team_skill_context(["目标", "消费"], characters)
        self.assertNotIn("A", context["producers"])
        self.assertTrue(build_skill_allowlist(["目标", "消费"], characters)[0][0])

    def test_shred_consuming_anomaly_is_not_a_stack_producer(self):
        target = _character(
            "目标",
            enhancements=[{"release_gate": {"all": ["STACK_SHRED"]}}],
        )
        consumer = _character(
            "猛击者",
            effects=[_effect("STATUS_HEAVY_STRIKE")],
        )
        characters = {"目标": target, "猛击者": consumer}

        context = _build_team_skill_context(["目标", "猛击者"], characters)
        self.assertNotIn("STACK_SHRED", context["producers"])
        self.assertTrue(build_skill_allowlist(["目标", "猛击者"], characters)[0][0])

    def test_post_cast_branch_is_not_a_release_gate(self):
        target = _character(
            "目标",
            enhancements=[
                {
                    "release_gate": {"static": False},
                    "trigger_condition": {"effects": ["A"]},
                    "effects": [_effect("B")],
                }
            ],
        )
        characters = {"目标": target}

        self.assertNotIn(("目标", "目标_skill"), _build_team_skill_context(["目标"], characters)["release_gates"])
        self.assertTrue(build_skill_allowlist(["目标"], characters)[0][0])

        static_target = _character(
            "静态目标",
            effects=[_effect("A")],
            enhancements=[{"release_gate": {"all": ["A"]}}],
        )
        self.assertTrue(build_skill_allowlist(["静态目标"], {"静态目标": static_target})[0][0])

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
        self.assertFalse(skill["enhancements"][1]["release_gate"]["static"])

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

        self.assertTrue(build_skill_allowlist(["庄方宜", "青霆剑消费者"], team_data)[0][0])
        self.assertFalse(build_skill_allowlist(["庄方宜", "导电产出者"], team_data)[0][0])

    def test_real_laevat_threshold_is_not_statically_satisfied(self):
        characters = load_characters()
        laevat = characters["莱万汀"]
        skill = next(skill for skill in laevat["skills"] if skill["skill_id"] == "laevat_skill")

        self.assertFalse(skill["enhancement"]["release_gate"]["static"])
        self.assertTrue(build_skill_allowlist(["莱万汀"], {"莱万汀": laevat})[0][0])


if __name__ == "__main__":
    unittest.main()
