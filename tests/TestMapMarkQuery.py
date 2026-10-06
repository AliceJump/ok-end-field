import unittest

from src.data.map_mark_query import extract_mark_points, merge_item_maps


def _payload(*, marks=(), save_marks=(), templates=()):
    return {
        "code": 0,
        "data": {
            "markTemplates": list(templates),
            "marks": list(marks),
            "saveMarks": list(save_marks),
        },
    }


def _mark(name_id, x, y, z, *, map_id="map01"):
    return {
        "templateId": name_id,
        "mapId": map_id,
        "pos": {"x": x, "y": y, "z": z},
    }


class TestMapMarkQuery(unittest.TestCase):
    def setUp(self):
        self.templates = (
            {"id": "power", "name": "供电桩"},
            {"id": "relay", "name": "中继器"},
            {"id": "zip", "name": "滑索架"},
        )

    def test_extract_mark_points_includes_official_and_user_marks(self):
        payload = _payload(
            templates=self.templates,
            marks=[_mark("power", 1, 2, 3)],
            save_marks=[_mark("relay", 4, 5, 6)],
        )

        result = extract_mark_points(payload, names={"供电桩", "中继器"})

        self.assertEqual(set(result), {"供电桩", "中继器"})
        self.assertEqual(result["供电桩"], [{"x": 1.0, "y": 2.0, "z": 3.0}])
        self.assertEqual(result["中继器"], [{"x": 4.0, "y": 5.0, "z": 6.0}])

    def test_extract_mark_points_filters_other_map_ids(self):
        payload = _payload(
            templates=self.templates,
            save_marks=[_mark("power", 1, 2, 3, map_id="map02")],
        )

        self.assertEqual(extract_mark_points(payload, map_id="map01"), {})

    def test_extract_mark_points_user_only_excludes_official_marks(self):
        payload = _payload(
            templates=self.templates,
            marks=[_mark("power", 1, 2, 3)],
            save_marks=[_mark("relay", 4, 5, 6)],
        )

        result = extract_mark_points(payload, user_only=True)

        self.assertEqual(result, {"中继器": [{"x": 4.0, "y": 5.0, "z": 6.0}]})

    def test_merge_item_maps_deduplicates_exact_points(self):
        first = {"供电桩": [{"x": 1.0, "y": 2.0, "z": 3.0}]}
        second = {
            "供电桩": [{"x": 1.0, "y": 2.0, "z": 3.0}, {"x": 4.0, "y": 5.0, "z": 6.0}],
            "中继器": [{"x": 7.0, "y": 8.0, "z": 9.0}],
        }

        merged = merge_item_maps([first, second])

        self.assertEqual(len(merged["供电桩"]), 2)
        self.assertEqual(len(merged["中继器"]), 1)


if __name__ == "__main__":
    unittest.main()
