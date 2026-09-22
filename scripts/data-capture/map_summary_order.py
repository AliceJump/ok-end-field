"""地图标点数据的顺序归一化。

`assets/items/map/summary.json` 由两条路径生成：

- `dump_endfield_map_marks_public.py` —— 直接调用线上接口；
- `dump_endfield_map_marks.py` —— 从本地抓包文件转换。

两者必须输出**完全一致且可复现**的顺序。原因：`json.dumps` 会保留 dict 的插入顺序，
而插入顺序取决于上游接口返回次序 / 文件遍历次序，这些都不保证稳定。一旦顺序漂移，
即使数据一个字都没变，也会产出数万行的无意义 diff（污染 git blame、淹没真实改动）。

因此**所有**写出 summary.json 的地方都要经过本模块归一。
"""

from __future__ import annotations

from typing import Any


def point_sort_key(point: dict[str, Any]) -> tuple[Any, Any, Any]:
    """标点排序键：依次按 x、y、z。"""
    return (point["x"], point["y"], point["z"])


def canonical_summary(
    all_maps: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """把「地图 id -> 物品名 -> 标点列表」整理成稳定顺序。

    - 地图按 id 排序；
    - 物品名按名称排序；
    - 标点按 (x, y, z) 排序。
    """
    return {
        map_id: {name: sorted(points, key=point_sort_key) for name, points in sorted(groups.items())}
        for map_id, groups in sorted(all_maps.items())
    }
