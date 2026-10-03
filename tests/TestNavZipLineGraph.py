"""用户滑索点解析与连接半径测试。"""

import unittest

from src.nav.zip_line_graph import (
    LONG_RANGE_ZIP_LINE_NAME,
    ZIP_LINE_NAME,
    ZIP_LINE_RANGES,
    ZipLineGraph,
)


def _payload(marks):
    templates = {
        "normal": {"id": "normal", "name": ZIP_LINE_NAME},
        "long": {"id": "long", "name": LONG_RANGE_ZIP_LINE_NAME},
    }
    return {
        "code": 0,
        "data": {
            "markTemplates": list(templates.values()),
            "saveMarks": marks,
        },
    }


def _mark(node_id, template_id, x, y, z, *, map_id="map01", level_id="lv1"):
    return {
        "id": node_id,
        "templateId": template_id,
        "mapId": map_id,
        "levelId": level_id,
        "pos": {"x": x, "y": y, "z": z},
    }


class TestZipLineGraph(unittest.TestCase):
    def test_builds_edges_from_each_endpoint_range(self):
        graph = ZipLineGraph.from_mark_payloads(
            [
                _payload(
                    [
                        _mark("a", "normal", 0, 0, 0),
                        _mark("b", "normal", 75, 0, 0),
                        _mark("c", "long", 170, 0, 0),
                    ]
                )
            ],
            map_id="map01",
        )

        self.assertEqual(len(graph), 3)
        self.assertEqual(
            {link.distance_m for link in graph.links},
            {75.0, 95.0},
        )
        self.assertEqual(ZIP_LINE_RANGES[ZIP_LINE_NAME], 80.0)
        self.assertEqual(ZIP_LINE_RANGES[LONG_RANGE_ZIP_LINE_NAME], 110.0)

    def test_three_dimensional_distance_prevents_vertical_false_link(self):
        graph = ZipLineGraph.from_mark_payloads(
            [
                _payload(
                    [
                        _mark("a", "long", 0, 0, 0),
                        _mark("b", "long", 0, 120, 0),
                    ]
                )
            ]
        )

        self.assertEqual(len(graph), 2)
        self.assertEqual(graph.links, ())

    def test_does_not_connect_different_maps_or_levels(self):
        graph = ZipLineGraph.from_mark_payloads(
            [
                _payload(
                    [
                        _mark("a", "normal", 0, 0, 0, map_id="map01", level_id="lv1"),
                        _mark("b", "normal", 10, 0, 0, map_id="map01", level_id="lv2"),
                        _mark("c", "normal", 20, 0, 0, map_id="map02", level_id="lv1"),
                    ]
                )
            ]
        )

        self.assertEqual(graph.links, ())

    def test_for_map_keeps_only_requested_map(self):
        graph = ZipLineGraph.from_mark_payloads(
            [
                _payload(
                    [
                        _mark("a", "normal", 0, 0, 0, map_id="map01"),
                        _mark("b", "normal", 10, 0, 0, map_id="map01"),
                        _mark("c", "normal", 0, 0, 0, map_id="map02"),
                        _mark("d", "normal", 10, 0, 0, map_id="map02"),
                    ]
                )
            ]
        )

        selected = graph.for_map("map02")

        self.assertEqual({node.node_id for node in selected.nodes}, {"c", "d"})
        self.assertEqual(len(selected.links), 1)


if __name__ == "__main__":
    unittest.main()
