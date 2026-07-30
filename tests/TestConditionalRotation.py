# -*- coding: utf-8 -*-
import unittest

from src.core.rotation_ast import normalize_ast, eval_cond, iter_actions


class _FakeProbe:
    """实现 CondProbe 协议的测试替身。"""

    def __init__(self, ults=(), link=False, skill=0):
        self._ults = {str(u) for u in ults}
        self._link = link
        self._skill = skill

    def ult_available(self, n):
        return str(n) in self._ults

    def link_available(self):
        return self._link

    def skill_count(self):
        return self._skill


class TestNormalizeAst(unittest.TestCase):
    def test_normalize_plain_actions_kept(self):
        ast, w = normalize_ast(["1", "2", "e", "ult_3", "sleep_1.5", "normal_2"])
        self.assertEqual(ast, ["1", "2", "e", "ult_3", "sleep_1.5", "normal_2"])
        self.assertEqual(w, [])

    def test_normalize_drops_invalid_action(self):
        ast, w = normalize_ast(["1", "ult_5", "foo", "normal_0", "sleep_-1"])
        self.assertEqual(ast, ["1"])
        self.assertEqual(len(w), 4)

    def test_normalize_drops_invalid_atom_in_condition(self):
        ast, w = normalize_ast([{"if": "ult9", "then": ["1"]}])
        self.assertEqual(ast, [])
        self.assertTrue(any("条件" in x for x in w))

    def test_normalize_skill_out_of_range(self):
        ast, w = normalize_ast([
            {"if": "skill>=4", "then": ["1"]},
            {"if": "skill>=0", "then": ["2"]},
            {"if": "skill>=3", "then": ["3"]},
        ])
        self.assertEqual(ast, [{"if": "skill>=3", "then": ["3"]}])
        # 每个非法 if 的 Condition 产生 2 条 warning（原子非法 + 整块丢弃），共 4 条
        self.assertEqual(len(w), 4)

    def test_normalize_top_level_not_list(self):
        ast, w = normalize_ast("ult_1,1")
        self.assertEqual(ast, [])
        self.assertEqual(len(w), 1)

    def test_normalize_condition_then_non_list_becomes_empty(self):
        ast, w = normalize_ast([{"if": "link", "then": "e"}])
        self.assertEqual(ast, [{"if": "link", "then": []}])
        self.assertTrue(any("then" in x for x in w))

    def test_normalize_condition_else_omitted_when_absent(self):
        ast, w = normalize_ast([{"if": "link", "then": ["e"]}])
        self.assertEqual(ast, [{"if": "link", "then": ["e"]}])
        self.assertNotIn("else", ast[0])
        self.assertEqual(w, [])

    def test_normalize_nested_all_any(self):
        ast, w = normalize_ast([
            {"if": {"all": ["ult1", {"any": ["link", "skill>=2"]}]}, "then": ["1"], "else": ["2"]}
        ])
        self.assertEqual(len(ast), 1)
        self.assertEqual(ast[0]["if"], {"all": ["ult1", {"any": ["link", "skill>=2"]}]})
        self.assertEqual(ast[0]["then"], ["1"])
        self.assertEqual(ast[0]["else"], ["2"])
        self.assertEqual(w, [])

    def test_normalize_empty_list(self):
        ast, w = normalize_ast([])
        self.assertEqual(ast, [])
        self.assertEqual(w, [])

    def test_normalize_structured_action_dropped(self):
        ast, w = normalize_ast([{"t": "dodge"}, "1"])
        self.assertEqual(ast, ["1"])
        self.assertEqual(len(w), 1)

    def test_normalize_invalid_sub_cond_in_all_dropped(self):
        ast, w = normalize_ast([{"if": {"all": ["ult1", "bad_atom"]}, "then": ["1"]}])
        self.assertEqual(ast, [{"if": {"all": ["ult1"]}, "then": ["1"]}])
        self.assertTrue(any("非法条件原子" in x for x in w))


class TestEvalCond(unittest.TestCase):
    def test_eval_atom_ult(self):
        self.assertTrue(eval_cond("ult1", _FakeProbe(ults=[1])))
        self.assertFalse(eval_cond("ult1", _FakeProbe(ults=[2])))

    def test_eval_atom_link(self):
        self.assertTrue(eval_cond("link", _FakeProbe(link=True)))
        self.assertFalse(eval_cond("link", _FakeProbe(link=False)))

    def test_eval_atom_skill(self):
        self.assertTrue(eval_cond("skill>=2", _FakeProbe(skill=2)))
        self.assertTrue(eval_cond("skill>=2", _FakeProbe(skill=3)))
        self.assertFalse(eval_cond("skill>=2", _FakeProbe(skill=1)))
        self.assertFalse(eval_cond("skill>=2", _FakeProbe(skill=-1)))

    def test_eval_all_short_circuit(self):
        self.assertFalse(eval_cond({"all": ["ult1", "link"]}, _FakeProbe(ults=[], link=True)))
        self.assertTrue(eval_cond({"all": ["ult1", "link"]}, _FakeProbe(ults=[1], link=True)))

    def test_eval_any_short_circuit(self):
        self.assertTrue(eval_cond({"any": ["ult1", "link"]}, _FakeProbe(ults=[], link=True)))
        self.assertFalse(eval_cond({"any": ["ult1", "link"]}, _FakeProbe(ults=[], link=False)))

    def test_eval_not(self):
        self.assertTrue(eval_cond({"not": "link"}, _FakeProbe(link=False)))
        self.assertFalse(eval_cond({"not": "link"}, _FakeProbe(link=True)))

    def test_eval_unknown_returns_false(self):
        self.assertFalse(eval_cond("unknown", _FakeProbe()))
        self.assertFalse(eval_cond(123, _FakeProbe()))
        self.assertFalse(eval_cond({"weird": []}, _FakeProbe()))


class TestIterActions(unittest.TestCase):
    def test_iter_plain_sequence(self):
        self.assertEqual(
            list(iter_actions(["1", "2", "e"], _FakeProbe())),
            ["1", "2", "e"],
        )

    def test_iter_condition_then_branch(self):
        ast = [{"if": "link", "then": ["e", "1"]}]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(link=True))),
            ["e", "1"],
        )

    def test_iter_condition_else_branch(self):
        ast = [{"if": "link", "then": ["e"], "else": ["1"]}]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(link=False))),
            ["1"],
        )

    def test_iter_condition_else_omitted_yields_nothing(self):
        ast = [{"if": "link", "then": ["e"]}]
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=False))), [])

    def test_iter_nested_all_any(self):
        ast = [
            {"if": {"all": ["ult1", {"any": ["link", "skill>=2"]}]}, "then": ["1"], "else": ["2"]},
        ]
        # all True（ult1 + link）
        self.assertEqual(list(iter_actions(ast, _FakeProbe(ults=[1], link=True))), ["1"])
        # all False（无 ult1，走 else）
        self.assertEqual(list(iter_actions(ast, _FakeProbe(ults=[], link=True, skill=3))), ["2"])

    def test_iter_empty_ast(self):
        self.assertEqual(list(iter_actions([], _FakeProbe())), [])

    def test_iter_mixed_plain_and_condition(self):
        ast = ["ult_1", {"if": "ult2", "then": ["2", "3"]}, "1"]
        self.assertEqual(
            list(iter_actions(ast, _FakeProbe(ults=[2]))),
            ["ult_1", "2", "3", "1"],
        )

    def test_iter_unknown_structure_ignored(self):
        ast = ["1", {"weird": True}, "2"]
        self.assertEqual(list(iter_actions(ast, _FakeProbe())), ["1", "2"])

    def test_iter_not_branch(self):
        # link 不可用时走 then（not link 为 True）
        ast = [{"if": {"not": "link"}, "then": ["1"], "else": ["e"]}]
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=False))), ["1"])
        self.assertEqual(list(iter_actions(ast, _FakeProbe(link=True))), ["e"])


if __name__ == "__main__":
    unittest.main()
