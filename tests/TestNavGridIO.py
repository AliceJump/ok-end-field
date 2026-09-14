"""导航网格表示与读写（src/nav/grid_io.py）的单元测试。

不需要游戏窗口：合成网格 + 临时目录即可覆盖格式校验、坐标换算、邻域、
贴墙安全距离与往返一致性。真实地图（尺寸/未知比例都大得多）的规划行为见
``tests/TestNavGridPlanner.py`` 与 :mod:`src.nav.grid_planner` 的说明。
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.nav.grid_io import (
    AXIS_CONVENTION,
    CELL_BLOCKED,
    CELL_FREE,
    CELL_UNKNOWN,
    GRID_MAGIC,
    GRID_SUFFIX,
    SCHEMA_VERSION,
    DenseGrid,
    GridMeta,
    load_grid,
    new_grid,
    save_grid,
)


def _grid(rows, *, origin=(10.0, 0.0, 20.0), cell_size=1.0, map_name="t", zoom="4"):
    """用字符画建网格：'.' 未知、'o' 可行走、'#' 阻挡（第一行是 i=0）。"""
    table = {".": CELL_UNKNOWN, "o": CELL_FREE, "#": CELL_BLOCKED}
    arr = np.array([[table[ch] for ch in row] for row in rows], dtype=np.uint8)
    return DenseGrid(arr, GridMeta(map_name=map_name, zoom=zoom, origin=origin,
                                   cell_size=cell_size, source="test"))


class TestGridMeta(unittest.TestCase):
    def test_json_round_trip(self):
        meta = GridMeta(map_name="base01", zoom="4", origin=(-127.74, -10.0, -86.47),
                        cell_size=1.0, source="x")
        again = GridMeta.from_json(meta.to_json())
        self.assertEqual(again.map_name, "base01")
        self.assertEqual(again.origin, (-127.74, -10.0, -86.47))
        self.assertEqual(again.axis_convention, AXIS_CONVENTION)

    def test_rejects_wrong_magic(self):
        bad = json.dumps({"magic": "something-else", "schema_version": SCHEMA_VERSION})
        with self.assertRaises(ValueError):
            GridMeta.from_json(bad)

    def test_rejects_version_mismatch(self):
        bad = json.dumps({"magic": GRID_MAGIC, "schema_version": SCHEMA_VERSION + 1})
        with self.assertRaises(ValueError):
            GridMeta.from_json(bad)

    def test_rejects_non_positive_cell_size(self):
        bad = json.dumps({"magic": GRID_MAGIC, "schema_version": SCHEMA_VERSION, "cell_size": 0})
        with self.assertRaises(ValueError):
            GridMeta.from_json(bad)


class TestDenseGridValidation(unittest.TestCase):
    def test_rejects_bad_state_values(self):
        with self.assertRaises(ValueError):
            DenseGrid(np.array([[0, 1], [2, 7]], dtype=np.uint8))

    def test_rejects_empty_and_3d(self):
        with self.assertRaises(ValueError):
            DenseGrid(np.zeros((0, 3), dtype=np.uint8))
        with self.assertRaises(ValueError):
            DenseGrid(np.zeros((2, 2, 2), dtype=np.uint8))

    def test_counts(self):
        grid = _grid(["o#.", ".oo"])     # 可行走 3、阻挡 1、未知 2
        self.assertEqual(grid.counts(), {"未知": 2, "可行走": 3, "阻挡": 1})


class TestCoordinates(unittest.TestCase):
    def setUp(self):
        # 3x2，origin=(10,0,20)，cell=1 → x:10~13, z:20~22
        self.grid = _grid(["ooo", "ooo"], origin=(10.0, 0.0, 20.0), cell_size=1.0)

    def test_extent(self):
        self.assertEqual(self.grid.extent(), (10.0, 20.0, 13.0, 22.0))

    def test_index_to_world_is_cell_center(self):
        self.assertEqual(self.grid.world_of_index(0, 0), (10.5, 20.5))
        self.assertEqual(self.grid.world_of_index(1, 2), (12.5, 21.5))

    def test_world_to_index_and_back(self):
        for i in range(2):
            for j in range(3):
                x, z = self.grid.world_of_index(i, j)
                self.assertEqual(self.grid.index_of_world(x, z), (i, j))
        # 边界：最小角属于 (0,0)，最大角外面一格
        self.assertEqual(self.grid.index_of_world(10.0, 20.0), (0, 0))
        self.assertEqual(self.grid.index_of_world(12.999, 21.999), (1, 2))
        self.assertEqual(self.grid.index_of_world(9.999, 20.5), (0, -1))

    def test_scaled_cell(self):
        grid = _grid(["oo", "oo"], origin=(0.0, 0.0, 0.0), cell_size=0.5)
        self.assertEqual(grid.world_of_index(1, 1), (0.75, 0.75))
        self.assertEqual(grid.index_of_world(0.74, 0.76), (1, 1))

    def test_out_of_bounds_is_blocked(self):
        self.assertFalse(self.grid.in_bounds(-1, 0))
        self.assertTrue(self.grid.is_blocked(-1, 0))
        self.assertTrue(self.grid.is_blocked(0, 3))


class TestNeighbors(unittest.TestCase):
    def test_four_and_eight(self):
        grid = _grid(["ooo", "ooo", "ooo"])
        self.assertEqual(len(grid.neighbors(1, 1, diagonal=False)), 4)
        self.assertEqual(len(grid.neighbors(1, 1, diagonal=True)), 8)

    def test_blocked_neighbor_excluded(self):
        grid = _grid(["oo", "o#"])
        self.assertNotIn((1, 1), grid.neighbors(0, 0, diagonal=True))

    def test_no_corner_cutting(self):
        """斜向目标虽是可行走，但会擦过阻挡格的一角 -> 不许走。"""
        grid = _grid(["o#", ".o"])          # (0,1) 阻挡，(1,0) 未知，(1,1) 可行走
        self.assertNotIn((1, 1), grid.neighbors(0, 0, diagonal=True))
        # 两侧都不是阻挡时，同样的斜向允许走
        grid2 = _grid(["o.", "oo"])
        self.assertIn((1, 1), grid2.neighbors(0, 0, diagonal=True))


class TestClearance(unittest.TestCase):
    def test_distance(self):
        grid = _grid(["ooo", "o#o", "ooo"])
        dist = grid.clearance()
        self.assertEqual(dist[1, 1], 0)                 # 阻挡格自身
        self.assertEqual(dist[0, 0], 2)                 # 斜对角到最近阻挡是 2（4 连通 BFS）
        self.assertEqual(dist[1, 0], 1)
        self.assertEqual(dist[0, 1], 1)

    def test_one_when_no_blocked(self):
        dist = _grid(["oo", "oo"]).clearance()
        self.assertTrue((dist == -1).all())

    def test_max_dist_truncates_wavefront(self):
        """给了 max_dist 就只扩散这么多层：够用的距离照算，更远的留 -1。

        规划器只用得到 ``min(距离, margin)``，而 ``-1`` 恰好表示"距离 ≥ max_dist"，
        按"不欠安全距离"处理即可，所以截断不丢精度——大图上这是秒级 vs 毫秒级的差别。
        """
        grid = _grid(["ooooooo",
                      "ooo#ooo",
                      "ooooooo"])
        full = grid.clearance()
        capped = grid.clearance(max_dist=1)
        self.assertEqual(capped[1, 3], 0)               # 阻挡格自身
        self.assertEqual(capped[0, 3], 1)               # 一层之内照算
        self.assertEqual(full[0, 0], 4)                 # 四连通波前，是曼哈顿距离
        self.assertEqual(capped[0, 0], -1)              # 超过 max_dist -> -1
        near = capped >= 0
        self.assertTrue((capped[near] == full[near]).all())
        self.assertTrue((full[~near] > 1).all())

    def test_frontier_clearance_max_dist(self):
        grid = _grid(["o.o",
                      "ooo"])
        full = grid.frontier_clearance()
        capped = grid.frontier_clearance(max_dist=1)
        self.assertEqual(full[0, 0], 1)
        self.assertEqual(capped[0, 0], 1)
        self.assertEqual(full[1, 0], 2)
        self.assertEqual(capped[1, 0], -1)


    def test_frontier_clearance(self):
        grid = _grid(["o.o", "ooo"])
        dist = grid.frontier_clearance()
        self.assertEqual(dist[0, 1], 0)                  # 未知格自身
        self.assertEqual(dist[0, 0], 1)
        self.assertEqual(dist[0, 2], 1)
        self.assertEqual(dist[1, 1], 1)
        self.assertEqual(dist[1, 0], 2)
        self.assertEqual(dist[1, 2], 2)


class TestNearestFree(unittest.TestCase):
    def test_finds_closest(self):
        grid = _grid(["###", "#o#", "###"])
        self.assertEqual(grid.nearest_free(0, 0), (1, 1))
        self.assertEqual(grid.nearest_free(1, 1), (1, 1))

    def test_none_when_no_free(self):
        self.assertIsNone(_grid(["###"]).nearest_free(0, 0, max_radius=3))


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_npz_round_trip(self):
        grid = _grid(["o#.", ".oo", "###"], origin=(-127.74, -10.0, -86.47),
                     map_name="base01", zoom="4")
        path = save_grid(self.dir / "base01_4", grid)
        self.assertTrue(path.name.endswith(GRID_SUFFIX), path.name)

        again = load_grid(path)
        self.assertTrue(np.array_equal(again.cells, grid.cells))
        self.assertEqual(again.meta.map_name, "base01")
        self.assertEqual(again.meta.zoom, "4")
        self.assertEqual(again.meta.origin, (-127.74, -10.0, -86.47))
        self.assertEqual(again.meta.cell_size, 1.0)
        self.assertEqual(again.meta.magic, GRID_MAGIC)
        self.assertEqual(again.meta.schema_version, SCHEMA_VERSION)
        self.assertTrue(again.meta.created)             # 落盘时会补上时间
        self.assertEqual(again.counts(), grid.counts())

    def test_save_normalizes_extension(self):
        grid = _grid(["oo", "oo"])
        self.assertTrue(save_grid(self.dir / "m.npz", grid).name == "m" + GRID_SUFFIX)
        self.assertTrue(save_grid(self.dir / "m", grid).name == "m" + GRID_SUFFIX)

    def test_load_rejects_wrong_name(self):
        grid = _grid(["oo"])
        path = save_grid(self.dir / "ok", grid)
        other = self.dir / "not_a_grid.npz"
        other.write_bytes(path.read_bytes())
        with self.assertRaises(ValueError):
            load_grid(other)

    def test_load_rejects_missing_keys(self):
        path = self.dir / ("broken" + GRID_SUFFIX)
        np.savez_compressed(path, cells=np.zeros((2, 2), np.uint8))
        with self.assertRaises(ValueError):
            load_grid(path)

    def test_save_does_not_mutate_grid_meta(self):
        grid = _grid(["oo"])
        before = grid.meta.to_dict()
        save_grid(self.dir / "keep", grid)
        self.assertEqual(grid.meta.to_dict(), before)


class TestNewGrid(unittest.TestCase):
    def test_defaults_to_unknown(self):
        grid = new_grid((3, 4), origin=(1.0, 0.0, 2.0), cell_size=0.5, map_name="m")
        self.assertEqual(grid.shape, (3, 4))
        self.assertEqual(grid.counts()["未知"], 12)
        self.assertEqual(grid.meta.cell_size, 0.5)
        self.assertEqual(grid.extent(), (1.0, 2.0, 3.0, 3.5))

    def test_rejects_bad_fill(self):
        with self.assertRaises(ValueError):
            new_grid((2, 2), fill=9)


if __name__ == "__main__":
    unittest.main()
