# -*- coding: utf-8 -*-
import unittest

from src.data.item_map_query import (
    get_item_map,
    get_item_positions,
    get_supported_item_names,
    get_supported_map_types,
    search_item_names,
)


class TestItemMapQuery(unittest.TestCase):
    def test_get_supported_lists_include_known_values(self):
        self.assertIn("柑实", get_supported_item_names())
        self.assertIn("base01", get_supported_map_types())
        self.assertIn("map01", get_supported_map_types())

    def test_search_item_names_supports_keyword_matching(self):
        matches = search_item_names("柑")
        self.assertIn("柑实", matches)
        self.assertIn("黯银柑实", matches)

    def test_get_item_positions_filters_by_map_type(self):
        result = get_item_positions("柑实", "map01")
        self.assertEqual(list(result.keys()), ["map01"])
        self.assertGreater(len(result["map01"]), 0)

    def test_get_item_map_supports_multiple_items_and_map_types(self):
        result = get_item_map(["柑实", "荞花"], ["map01", "base01"])
        self.assertIn("map01", result)
        self.assertIn("柑实", result["map01"])
        self.assertIn("荞花", result["map01"])


if __name__ == "__main__":
    unittest.main()