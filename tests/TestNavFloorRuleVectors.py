"""分层判定语义的跨仓库契约测试。

``tests/data/floor_rule_vectors.json`` 由 nav-grid-editor 产出，定义「三维坐标 → 玩家在
哪一层」的判定语义。ok-end-field 必须**独立实现**同一份判定（跟随器要能在编辑器进程不在
时判断位置是否可信），两边各跑这组向量：任何一侧改了行为，另一侧立刻红。

所以这里的断言不是"我们的实现恰好这样"，而是"契约要求这样"。与编辑器执行器一致，
``contract: false`` 的用例是对方当前实现的偶然行为，仍然跑，但失败只提示、不判失败
——那是"这里被改过了"的信号，不是我们错了。

来源与版本（**改向量前先核对哈希**，它是两边唯一可靠的漂移信号）::

    nav-grid-editor/tests/data/floor_rule_vectors.json
    commit  923762d  test(nav): 层判定规则的共享测试向量
    18 向量（15 个 contract=true）

    sha256（对方仓库磁盘上的原文，CRLF 行尾，26935 字节）
      62fe8347ef1ef21b9433036b2316ba5258841a1ccab72c35eb8a90cba91ac37b
    sha256（本仓库里提交的副本，Git 已把行尾规范成 LF，25736 字节）
      f0b30286583da19140a4ce2a976f4012461329116e0e47ba844b632639184f4e

**为什么有两个哈希**：两边内容完全一致，差别只在行尾——Git 提交时按 ``* text=auto eol=lf``
规范化了。所以核对时**先按行尾归一**再比（把 ``
`` 换成 ``
``），否则会误判成"向量被改过"。
两个原始哈希都留着，是为了能分别对上对方磁盘上的文件、和本仓库里提交的字节。

优先核对 **sha256** 而不是 commit hash：后者指向仓库状态，对方哪怕只在同一提交里改别的
文件或 force-push 都会变；内容哈希只在向量本身变化时才变。

**注意**：这份向量锁定的是 nav-grid-editor 的 ``resolve_position_floor`` 语义——即
「用 ``level_id`` 选候选层、单候选无条件 ``unique``」。产品侧已改为**只按 Y 判定**
（:func:`~src.nav.layered_map.resolve_tier_by_height`），因为 ``levelId`` 不可信。
所以本文件校验的是**编辑器的规则**：它是跨仓库契约的锚点与漂移探针，**不等于**生产路径。
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.nav.grid_io import CELL_BLOCKED, CELL_FREE, GRID_SUFFIX, new_grid, save_grid
from src.nav.layered_map import (
    LayeredMap,
    load_layered_map,
    normalize_tier,
    resolve_tier_for_point,
)

VECTORS_PATH = Path(__file__).resolve().parent / "data" / "floor_rule_vectors.json"


def _number(value):
    """向量里的非有限数用字符串写出，这里还原成 float。"""
    if isinstance(value, str):
        return float(value)
    return float(value)


class FloorRuleVectorsTest(unittest.TestCase):
    """按共享向量校验 :func:`resolve_tier_for_point`。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        if not cls.payload.get("vectors"):
            raise AssertionError("向量文件不该是空的")

    @staticmethod
    def _materialize(root: Path, vector: dict) -> LayeredMap:
        """按向量把网格与分层目录物化成文件，再走生产路径（文件发现）装配 LayeredMap。"""
        map_name = str(vector["query"]["map"])
        for grid in vector.get("grids") or []:
            cells = grid.get("cells") or [[0, 0]]
            blocked = grid.get("blocked") or []
            height = max(i for i, _j in cells + blocked) + 1
            width = max(j for _i, j in cells + blocked) + 1
            dense = np.full((height, width), CELL_BLOCKED, dtype=np.uint8)
            for i, j in cells:
                dense[i, j] = CELL_FREE
            tier = str(grid.get("tier") or "")
            # floor_regions_raw：模拟"文件里已经躺着一段坏区间"，验证加载端**不因一段
            # 坏数据拒掉整个网格**，而是让该层按"没有区间"处理。
            regions = grid.get("floor_regions_raw")
            if regions is None:
                regions = grid.get("floor_regions") or []
            instance = new_grid(
                (height, width),
                origin=grid.get("origin") or [0, 0, 0],
                cell_size=grid.get("cell_size", 1),
                map_name=map_name,
                zoom="4",
                tier=tier,
                floor_regions=regions,
            )
            name = f"{map_name}__{tier}_4{GRID_SUFFIX}" if tier else f"{map_name}_4{GRID_SUFFIX}"
            save_grid(root / name, instance)
        # 分层目录：包含"已声明但还没有网格文件"的层，是区分 no_grid 与 main 的唯一依据
        catalog = vector.get("catalog") or []
        if catalog:
            (root / "catalog.json").write_text(
                json.dumps({"layers": catalog}, ensure_ascii=False),
                encoding="utf-8",
            )
        return load_layered_map(root, map_name, zoom="4")

    def test_vectors(self):
        """逐条跑共享向量；契约项必须完全一致。"""
        hard_failures = []
        soft_failures = []
        for vector in self.payload["vectors"]:
            with self.subTest(vector=vector["name"]):
                with tempfile.TemporaryDirectory() as tmp:
                    layered = self._materialize(Path(tmp), vector)
                    query = vector["query"]
                    got = resolve_tier_for_point(
                        layered,
                        map_name=query["map"],
                        level_id=query["level_id"],
                        x=_number(query["x"]),
                        y=_number(query["y"]),
                        z=_number(query["z"]),
                    )
                expect = vector["expect"]
                # tier_id 的两种主图拼写（"" / "__main__"）等价，比较前归一
                same = (
                    got["status"] == expect["status"]
                    and normalize_tier(got["tier"]) == normalize_tier(expect["tier_id"])
                    and [normalize_tier(item) for item in sorted(got["matched"])]
                    == [normalize_tier(item) for item in sorted(expect["matched"])]
                )
                if same:
                    continue
                detail = f"{vector['name']}: 期望 {expect} 实际 {got}"
                (hard_failures if vector.get("contract") else soft_failures).append(detail)
        if soft_failures:
            # 非契约项：对方明确说这些是偶然行为、允许改动，失败只提示
            print("\n[提示] 非契约向量不一致（对方允许改动，不算失败）：")
            for line in soft_failures:
                print(f"  - {line}")
        self.assertEqual(hard_failures, [], "契约向量不一致：\n" + "\n".join(hard_failures))

    def test_vectors_cover_every_status(self):
        """向量至少覆盖到全部分支，否则"通过"没有意义。"""
        statuses = {vector["expect"]["status"] for vector in self.payload["vectors"]}
        self.assertTrue({"main", "unique", "ambiguous", "no_match", "no_grid"} <= statuses, statuses)

    def test_non_finite_coordinates_are_rejected(self):
        """非有限坐标不能靠 float(nan) 的比较悄悄判成 no_match 之外的结论。"""
        layered = LayeredMap()
        for bad in (math.nan, math.inf, -math.inf):
            result = resolve_tier_for_point(layered, map_name="map02", level_id="lv1", x=bad, y=0.0, z=0.0)
            self.assertEqual(result["status"], "no_match", (bad, result))
