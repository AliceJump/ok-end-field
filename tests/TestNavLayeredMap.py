"""``src/nav/layered_map.py`` 的单元测试：层发现、连接装载与降级可见性。

分层判定的**语义**由共享向量（``TestNavFloorRuleVectors``）锁定，这里只测围绕它的
数据装配：文件发现口径、links 解析、端点区域取格，以及"没有分层目录时结果会降级"
这件事必须**看得见**（调用方要据此提示用户，而不是照用一个可能串层的结论）。
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, GRID_SUFFIX, new_grid, save_grid
from src.nav.layered_map import (
    MAIN_TIER,
    LayeredMap,
    discover_layer_paths,
    discover_layers,
    endpoint_cells,
    layer_key,
    load_catalog,
    load_layered_map,
    load_links,
    normalize_tier,
    resolve_tier_by_height,
    resolve_tier_for_point,
)

_REAL_DATA = Path(r"E:\LuQing\python\nav-grid-editor\assets\nav-grid-data")


def _grid(rows, *, tier="", map_name="map02", zoom="4", origin=(0.0, 0.0, 0.0), regions=()):
    """用字符画建网格：``o`` 可走、``#`` 阻挡、``.`` 未知。"""
    chars = {"o": CELL_FREE, "#": CELL_BLOCKED, ".": 0}
    dense = np.array([[chars[ch] for ch in row] for row in rows], dtype=np.uint8)
    instance = new_grid(
        dense.shape,
        origin=origin,
        cell_size=1.0,
        map_name=map_name,
        zoom=zoom,
        tier=tier,
        floor_regions=regions,
    )
    instance.cells = dense
    return instance


class TestTierKeys(unittest.TestCase):
    """主图的两种拼写必须归一，否则消费方会静默错配。"""

    def test_main_spellings_normalize_to_empty(self):
        for raw in ("", "__main__", "  ", None):
            self.assertEqual(normalize_tier(raw), "", raw)
        self.assertEqual(MAIN_TIER, "__main__")
        # 不做大小写折叠：编辑器也是精确比较，放宽会让两边结论不一致
        self.assertEqual(normalize_tier("__MAIN__"), "__MAIN__")

    def test_layer_key_distinguishes_map_and_tier(self):
        self.assertEqual(layer_key("map02", "__main__"), ("map02", ""))
        self.assertNotEqual(layer_key("map02", "a"), layer_key("map02", "b"))
        self.assertNotEqual(layer_key("map02", ""), layer_key("map01", ""))


class TestLayerDiscovery(unittest.TestCase):
    """文件发现口径：主图排除含 __ 的文件，分层按 __<tier>，且都要校验 meta。"""

    def test_main_glob_excludes_layered_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_grid(root / f"map02_4{GRID_SUFFIX}", _grid(["oo"], tier=""))
            save_grid(root / f"map02__lv1_4{GRID_SUFFIX}", _grid(["oo"], tier="lv1"))
            paths = discover_layer_paths(root, "map02", "", "4")
            self.assertEqual([p.name for p in paths], [f"map02_4{GRID_SUFFIX}"])

    def test_meta_mismatch_is_not_loaded(self):
        """文件名对得上但 meta.tier 不是这一层时不能收——光看文件名会把别的层收进来。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 文件名谎称是 lv1，内容其实是 lv2
            save_grid(root / f"map02__lv1_4{GRID_SUFFIX}", _grid(["oo"], tier="lv2"))
            layers = discover_layers(root, "map02", zoom="4")
            self.assertEqual(layers, {})

    def test_zoom_filter_uses_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_grid(root / f"map02__lv1_4{GRID_SUFFIX}", _grid(["oo"], tier="lv1", zoom="4"))
            save_grid(root / f"map02__lv1_3{GRID_SUFFIX}", _grid(["oo"], tier="lv1", zoom="3"))
            self.assertEqual(set(discover_layers(root, "map02", zoom="3")), {("map02", "lv1")})
            # 不指定 zoom 时该层有多档 -> 拒绝猜
            with self.assertRaises(ValueError):
                discover_layers(root, "map02")


class TestLinkLoading(unittest.TestCase):
    """links.json 的解析与校验。"""

    def test_real_links_file_parses(self):
        if not (_REAL_DATA / "links.json").is_file():
            self.skipTest("本机没有 nav-grid-data")
        links = load_links(_REAL_DATA / "links.json")
        self.assertEqual(len(links), 7)
        kinds = {link.kind for link in links}
        self.assertTrue(kinds <= {"stairs", "elevator", "portal", "door", "rope", "other"}, kinds)
        # 实测数据里有一条单向 rope
        self.assertTrue(any(not link.bidirectional for link in links))
        self.assertTrue(all(link.cost > 0 for link in links))

    def test_missing_file_is_empty_not_error(self):
        self.assertEqual(load_links(Path("__nope__.json")), [])

    def test_degenerate_rect_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "links": [
                            {
                                "from": {"map": "map02", "tier": "", "rect": {"x0": 1, "z0": 0, "x1": 1, "z1": 5}},
                                "to": {"map": "map02", "tier": "lv1", "rect": {"x0": 2, "z0": 0, "x1": 3, "z1": 5}},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "x1>x0"):
                load_links(path)

    def test_reversed_keeps_direction_semantics(self):
        """单向边反向仍为单向，且 ``from``/``to`` 互换。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "links": [
                            {
                                "id": "one",
                                "from": {"map": "map02", "tier": "a", "rect": {"x0": 0, "z0": 0, "x1": 1, "z1": 1}},
                                "to": {"map": "map02", "tier": "b", "rect": {"x0": 2, "z0": 0, "x1": 3, "z1": 1}},
                                "bidirectional": False,
                                "kind": "rope",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            link = load_links(path)[0]
            back = link.reversed
            self.assertFalse(back.bidirectional)
            self.assertEqual(back.source.key, link.target.key)
            self.assertEqual(back.target.key, link.source.key)


class TestEndpointCells(unittest.TestCase):
    """端点区域 -> 该层可走格集合。"""

    def test_partial_and_unaligned_rects(self):
        """不要求矩形对齐到格：部分覆盖也算。"""
        grid = _grid(["ooo", "o#o", "ooo"], tier="lv1")
        # 覆盖 (0,0)-(0,1) 与 (1,0) 的一半；(1,1) 是阻挡不算
        cells = endpoint_cells(grid, {"x0": 0.0, "z0": 0.0, "x1": 1.5, "z1": 1.5})
        self.assertIn((0, 0), cells)
        self.assertIn((0, 1), cells)
        self.assertIn((1, 0), cells)
        self.assertNotIn((1, 1), cells)

    def test_out_of_bounds_is_clamped(self):
        grid = _grid(["oo"], tier="lv1")
        cells = endpoint_cells(grid, {"x0": -5.0, "z0": -5.0, "x1": 9.0, "z1": 9.0})
        self.assertEqual(set(cells), {(0, 0), (0, 1)})

    def test_real_links_have_walkable_endpoints(self):
        """实测数据的每个端点都必须落在本层可走格上——两边对同一份数据的结论要一致。"""
        if not (_REAL_DATA / "links.json").is_file():
            self.skipTest("本机没有 nav-grid-data")
        layered = load_layered_map(_REAL_DATA, "map02", zoom="4")
        self.assertTrue(layered.links)
        for link in layered.links:
            for endpoint in (link.source, link.target):
                grid = layered.layers.get(endpoint.key)
                if grid is None:
                    continue
                self.assertTrue(
                    endpoint_cells(grid, endpoint.rect),
                    f"{link.link_id} 的 {endpoint.key} 端点没有可走格",
                )


class TestTierByHeight(unittest.TestCase):
    """用 Y 判定层级（产品路径）：不依赖 levelId，只看 floor_regions 的 Y 区间。"""

    @staticmethod
    def _region(x0, x1, z0, z1, y_min, y_max, cells=None):
        region = {"x0": x0, "x1": x1, "z0": z0, "z1": z1, "y_min": y_min, "y_max": y_max}
        if cells is not None:
            region["cells"] = cells
        return region

    def _layered(self, specs):
        """``specs``: {(tier, regions)} -> LayeredMap，格子全可走。"""
        layers = {}
        for tier, regions in specs:
            grid = _grid(["ooo", "ooo", "ooo"], tier=tier, regions=regions)
            layers[layer_key("map02", tier)] = grid
        return LayeredMap(layers=layers, catalog_loaded=True)

    def test_unique_when_one_region_covers(self):
        layered = self._layered(
            [
                ("", []),
                ("lv_a", [self._region(0, 3, 0, 3, 100, 110)]),
                ("lv_b", [self._region(0, 3, 0, 3, 200, 210)]),
            ]
        )
        self.assertEqual(
            resolve_tier_by_height(layered, map_name="map02", x=1.5, y=105, z=1.5),
            {"status": "unique", "tier": "lv_a", "matched": ["lv_a"], "reason": "ok"},
        )
        self.assertEqual(resolve_tier_by_height(layered, map_name="map02", x=1.5, y=205, z=1.5)["tier"], "lv_b")

    def test_ambiguous_does_not_guess(self):
        """两层区间重叠时绝不挑一个——挑错就是把角色送错层。"""
        layered = self._layered(
            [
                ("", []),
                ("lv_a", [self._region(0, 3, 0, 3, 100, 200)]),
                ("lv_b", [self._region(0, 3, 0, 3, 150, 250)]),
            ]
        )
        result = resolve_tier_by_height(layered, map_name="map02", x=1.5, y=175, z=1.5)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["tier"], "")
        self.assertEqual(sorted(result["matched"]), ["lv_a", "lv_b"])

    def test_falls_back_to_main_when_no_region_covers(self):
        """没有区间命中 -> 按主图处理，但 reason 必须说明这是兜底。"""
        layered = self._layered([("", []), ("lv_a", [self._region(0, 3, 0, 3, 100, 110)])])
        result = resolve_tier_by_height(layered, map_name="map02", x=1.5, y=999, z=1.5)
        self.assertEqual(result["status"], "main")
        self.assertEqual(result["tier"], "")
        self.assertEqual(result["reason"], "no_region_covers")

    def test_falls_back_to_main_when_nothing_is_mapped(self):
        """整张图一个区间都没标：兜底到主图，并且与"真的判定出主图"要区分开。"""
        layered = self._layered([("", []), ("lv_a", [])])
        result = resolve_tier_by_height(layered, map_name="map02", x=1.5, y=5, z=1.5)
        self.assertEqual(result["status"], "main")
        self.assertEqual(result["reason"], "no_floor_regions_mapped")

    def test_main_tier_with_regions_can_match_by_height(self):
        """主图也是候选：玩家在地表时应当由主图的区间命中，而不是靠兜底。"""
        layered = self._layered(
            [
                ("", [self._region(0, 3, 0, 3, 300, 310)]),
                ("lv_a", [self._region(0, 3, 0, 3, 100, 110)]),
            ]
        )
        result = resolve_tier_by_height(layered, map_name="map02", x=1.5, y=305, z=1.5)
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["tier"], "")
        self.assertEqual(result["reason"], "ok", "由主图区间命中，不是兜底")

    def test_closed_interval_boundaries(self):
        layered = self._layered([("", []), ("lv_a", [self._region(0, 3, 0, 3, 100, 110)])])
        for y in (100, 110):
            self.assertEqual(
                resolve_tier_by_height(layered, map_name="map02", x=1.5, y=y, z=1.5)["status"],
                "unique",
                f"y={y} 是闭区间端点，应当命中",
            )
        for y in (99.9, 110.1):
            self.assertEqual(
                resolve_tier_by_height(layered, map_name="map02", x=1.5, y=y, z=1.5)["status"],
                "main",
                f"y={y} 在区间外",
            )

    def test_cells_gate_still_applies(self):
        """区间带 cells 时仍要先命中某格的 X/Z——间隙不算命中。"""
        layered = self._layered(
            [
                ("", []),
                ("lv_a", [self._region(0, 3, 0, 3, 100, 110, cells=[[0, 1, 0, 1]])]),
            ]
        )
        self.assertEqual(resolve_tier_by_height(layered, map_name="map02", x=0.5, y=105, z=0.5)["status"], "unique")
        # X/Z 在外框内、但不在任何 cell 里
        self.assertEqual(
            resolve_tier_by_height(layered, map_name="map02", x=2.5, y=105, z=2.5)["reason"],
            "no_region_covers",
        )

    def test_no_level_id_parameter_at_all(self):
        """签名里没有 levelId —— 这是刻意的：它不可信，判定只认 Y。

        同一坐标无论如何都得到同一结论，也不存在"按 level_id 选候选层"的分支。
        """
        import inspect

        params = set(inspect.signature(resolve_tier_by_height).parameters)
        self.assertNotIn("level_id", params)
        self.assertNotIn("candidates", params)
        self.assertEqual(params, {"layered", "map_name", "x", "y", "z"})

    def test_invalid_input(self):
        layered = self._layered([("", []), ("lv_a", [self._region(0, 3, 0, 3, 100, 110)])])
        for kwargs in (
            {"map_name": "bad/name", "x": 1.0, "y": 100.0, "z": 1.0},
            {"map_name": "map02", "x": float("nan"), "y": 100.0, "z": 1.0},
            {"map_name": "map02", "x": 1.0, "y": float("inf"), "z": 1.0},
            {"map_name": "map02", "x": None, "y": 100.0, "z": 1.0},
        ):
            with self.subTest(**kwargs):
                result = resolve_tier_by_height(layered, **kwargs)
                self.assertEqual(result["status"], "no_match")
                self.assertEqual(result["reason"], "invalid_input")


class TestCatalogDegradation(unittest.TestCase):
    """没有分层目录时判定会降级；这一点必须**看得见**。"""

    @staticmethod
    def _two_levels(tmp: Path) -> Path:
        root = Path(tmp)
        # 同地图两个 level_id 的分层各一张（map02 的目录里有 9 个 level_id，现在只有
        # lv005 有网格，所以"按文件发现"恰好不出错——这里造出那个只差一张网格的情形）
        region = [{"x0": 0, "x1": 1, "z0": 0, "z1": 1, "y_min": 0, "y_max": 10}]
        # 主图不给高度区间，好让"分层之间串层"这件事单独暴露出来
        save_grid(root / f"map02_4{GRID_SUFFIX}", _grid(["oo"], tier=""))
        save_grid(root / f"map02__lv005_a_4{GRID_SUFFIX}", _grid(["oo"], tier="lv005_a", regions=region))
        save_grid(root / f"map02__lv002_b_4{GRID_SUFFIX}", _grid(["oo"], tier="lv002_b", regions=region))
        return root

    def test_without_catalog_the_source_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            layered = load_layered_map(self._two_levels(Path(tmp)), "map02", zoom="4")
            self.assertFalse(layered.catalog_loaded)
            result = resolve_tier_for_point(layered, map_name="map02", level_id="lv005", x=0.5, y=5.0, z=0.5)
            self.assertEqual(result["tier_source"], "files")

    def test_without_catalog_other_levels_leak_into_candidates(self):
        """这就是降级的实际后果：查 lv005 时 lv002 的层也是候选。

        两个候选都命中区间 -> ``ambiguous``；若只有 lv002 的层命中，就会报出一个
        **属于另一个 level_id 的层**。调用方看到 ``tier_source == "files"`` 就该提示
        用户，而不是照用。
        """
        with tempfile.TemporaryDirectory() as tmp:
            layered = load_layered_map(self._two_levels(Path(tmp)), "map02", zoom="4")
            result = resolve_tier_for_point(layered, map_name="map02", level_id="lv005", x=0.5, y=5.0, z=0.5)
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(sorted(result["matched"]), ["lv002_b", "lv005_a"])

    def test_with_catalog_only_the_asked_level_is_considered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._two_levels(Path(tmp))
            (root / "catalog.json").write_text(
                json.dumps(
                    {
                        "layers": [
                            {"id": "lv005_a", "level_id": "lv005"},
                            {"id": "lv002_b", "level_id": "lv002"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            layered = load_layered_map(root, "map02", zoom="4")
            self.assertTrue(layered.catalog_loaded)
            result = resolve_tier_for_point(layered, map_name="map02", level_id="lv005", x=0.5, y=5.0, z=0.5)
            self.assertEqual(result["tier_source"], "catalog")
            self.assertEqual(result["status"], "unique")
            self.assertEqual(result["tier"], "lv005_a")

    def test_declared_but_gridless_level_reports_no_grid(self):
        """目录里有、但还没有网格 -> ``no_grid``；没有目录时同样的数据会退化成 main。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 不写主图网格：有主图时单候选短路会报 unique/__main__，那是向量 #17 之外的
            # 另一种形状（它要求"所有候选层都没有网格文件"）
            (root / "catalog.json").write_text(
                json.dumps({"layers": [{"id": "lv009_a", "level_id": "lv009"}]}),
                encoding="utf-8",
            )
            layered = load_layered_map(root, "map02", zoom="4")
            with_catalog = resolve_tier_for_point(layered, map_name="map02", level_id="lv009", x=0.5, y=5.0, z=0.5)
            self.assertEqual(with_catalog["status"], "no_grid")

            plain = LayeredMap(layers=layered.layers, catalog_loaded=False)
            without_catalog = resolve_tier_for_point(plain, map_name="map02", level_id="lv009", x=0.5, y=5.0, z=0.5)
            self.assertEqual(without_catalog["status"], "main")

    def test_load_catalog_tolerates_bad_shapes(self):
        for payload in ("{}", "[]", '{"layers": "x"}', "not json"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "catalog.json"
                path.write_text(payload, encoding="utf-8")
                self.assertEqual(load_catalog(path), {}, payload)
        self.assertEqual(load_catalog(Path("__nope__.json")), {})


if __name__ == "__main__":
    unittest.main()
