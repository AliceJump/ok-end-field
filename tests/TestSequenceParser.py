import base64
import gzip
import json
import unittest

from src.core.sequence_parser import parse_int_sequence, parse_sequence
from src.data.endaxis_timeline import (
    EndAxisTimelineError,
    endaxis_spec_to_rotation_tokens,
    parse_endaxis_rotation_spec,
    parse_endaxis_scenarios,
)


def _endaxis_share_code(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(gzip.compress(raw)).decode("ascii").rstrip("=")


class TestSequenceParser(unittest.TestCase):
    def test_parse_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(
            parse_sequence(" 干员经验， 干员进阶, , 钱币收集  ,"),
            ["干员经验", "干员进阶", "钱币收集"],
        )

    def test_parse_int_sequence_supports_chinese_comma_whitespace_and_empty_items(self):
        self.assertEqual(parse_int_sequence(" 36，14, ,108 "), [36, 14, 108])

    def test_endaxis_share_code_maps_supported_actions_and_timing(self):
        code = _endaxis_share_code(
            {
                "scenarioList": [
                    {
                        "id": "scenario-1",
                        "name": "Burst",
                        "data": {
                            "tracks": [
                                {"actions": [{"type": "skill", "startTime": 300, "duration": 30}]},
                                {"actions": [{"type": "ultimate", "startTime": 360, "duration": 60}]},
                                {
                                    "actions": [
                                        {"type": "battleSkill", "startTime": 390, "duration": 30},
                                        {"type": "attack", "startTime": 420, "duration": 30},
                                    ]
                                },
                            ]
                        },
                    }
                ]
            }
        )

        scenarios = parse_endaxis_scenarios(code)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(
            [(action.kind, action.slot) for action in scenarios[0].actions],
            [("skill", 1), ("ultimate", 2), ("skill", 3)],
        )

        converted = endaxis_spec_to_rotation_tokens(f"endaxis:{code}")
        self.assertIsNotNone(converted)
        scenario, tokens = converted
        self.assertEqual(scenario.name, "Burst")
        self.assertEqual(tokens, ["1", "sleep_1", "ult_2", "sleep_0.5", "3"])

    def test_endaxis_spec_can_select_scenario_by_name(self):
        code = _endaxis_share_code(
            {
                "scenarioList": [
                    {
                        "id": "first",
                        "name": "First",
                        "data": {"tracks": [{"actions": [{"type": "skill", "startTime": 300, "duration": 1}]}]},
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "data": {
                            "tracks": [
                                {"actions": []},
                                {"actions": [{"type": "skill", "startTime": 300, "duration": 1}]},
                            ]
                        },
                    },
                ]
            }
        )

        scenario, tokens = endaxis_spec_to_rotation_tokens(f"endaxis[Second]:{code}")
        self.assertEqual(scenario.scenario_id, "second")
        self.assertEqual(tokens, ["2"])

    def test_non_endaxis_rotation_is_left_for_existing_parser(self):
        self.assertIsNone(parse_endaxis_rotation_spec("1,2,e,ult_3"))
        self.assertIsNone(endaxis_spec_to_rotation_tokens("1,2,e,ult_3"))

    def test_endaxis_empty_code_is_rejected(self):
        with self.assertRaises(EndAxisTimelineError):
            parse_endaxis_rotation_spec("endaxis:")

    def test_endaxis_non_finite_timing_is_rejected(self):
        code = _endaxis_share_code(
            {
                "scenarioList": [
                    {
                        "id": "bad-time",
                        "data": {
                            "tracks": [
                                {"actions": [{"type": "skill", "startTime": float("inf"), "duration": 1}]}
                            ]
                        },
                    }
                ]
            }
        )
        with self.assertRaises(EndAxisTimelineError):
            parse_endaxis_scenarios(code)

    def test_endaxis_selector_failure_is_explicit(self):
        code = _endaxis_share_code({"scenarioList": []})
        with self.assertRaises(EndAxisTimelineError):
            endaxis_spec_to_rotation_tokens(f"endaxis[missing]:{code}")


if __name__ == "__main__":
    unittest.main()
