"""用户滑索点的解析、连通图与路径步骤模型。

官方地图 ``mark/list`` 的用户数据在 ``data.saveMarks`` 中返回滑索架，
``markTemplates`` 提供模板名称。滑索点带有三维世界坐标，但接口没有返回
可靠的连接边；因此按设备连接半径推导无向边：

- ``滑索架``：80m
- ``长距滑索架``：110m

连接距离使用三维欧氏距离。这样同一 X/Z 位置但不同楼层的两个滑索架不会
因为二维投影重合而被错误连接。连接半径只在两端都是长距滑索架时取 110m，
其余组合取普通滑索架 80m。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "LONG_RANGE_ZIP_LINE_NAME",
    "LONG_RANGE_ZIP_LINE_NAMES",
    "SHARED_LONG_RANGE_ZIP_LINE_NAME",
    "SHARED_ZIP_LINE_NAME",
    "SHARED_ZIP_LINE_NAMES",
    "SHARED_ZIP_LINE_RADIUS_M",
    "ZIP_LINE_NAME",
    "ZIP_LINE_POWER_FACILITY_NAMES",
    "ZIP_LINE_RANGES",
    "ZIP_LINE_TEMPLATE_IDS",
    "ZipLineGraph",
    "ZipLineLink",
    "ZipLineNode",
    "ZipLineStep",
]

ZIP_LINE_NAME = "滑索架"
LONG_RANGE_ZIP_LINE_NAME = "长距滑索架"
SHARED_ZIP_LINE_NAME = "共享滑索架"
SHARED_LONG_RANGE_ZIP_LINE_NAME = "共享长距离滑索架"
SHARED_ZIP_LINE_NAMES = frozenset({SHARED_ZIP_LINE_NAME, SHARED_LONG_RANGE_ZIP_LINE_NAME})
LONG_RANGE_ZIP_LINE_NAMES = frozenset({LONG_RANGE_ZIP_LINE_NAME, SHARED_LONG_RANGE_ZIP_LINE_NAME})
SHARED_ZIP_LINE_RADIUS_M = 6.0
ZIP_LINE_POWER_FACILITY_NAMES = frozenset(
    {
        "供电桩",
        "中继器",
        "息壤供电桩",
        "息壤中继器",
    }
)
ZIP_LINE_RANGES = {
    ZIP_LINE_NAME: 80.0,
    LONG_RANGE_ZIP_LINE_NAME: 110.0,
    SHARED_ZIP_LINE_NAME: 80.0,
    SHARED_LONG_RANGE_ZIP_LINE_NAME: 110.0,
}
ZIP_LINE_TEMPLATE_IDS = {
    "5d53bdb714ba42c1e1a1b748b55b686f": ZIP_LINE_NAME,
    "0f45150a59b97bd0de9a4eed7a0fbf23": LONG_RANGE_ZIP_LINE_NAME,
}


def _finite_float(value: Any) -> float | None:
    """把坐标字段转换为有限浮点数；非法值返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mark_shared_zip_line_nodes(
    nodes: Iterable[ZipLineNode],
    power_facility_points: Iterable[dict[str, float]],
    radius_m: float,
) -> list[ZipLineNode]:
    """把附近没有供电桩/中继器的滑索架改标为共享滑索架。"""

    points = [
        (float(point["x"]), float(point["y"]), float(point["z"]))
        for point in power_facility_points
        if isinstance(point, dict)
        and point.get("x") is not None
        and point.get("y") is not None
        and point.get("z") is not None
    ]
    radius = max(0.0, float(radius_m))
    result: list[ZipLineNode] = []

    for node in nodes:
        shared_name = {
            ZIP_LINE_NAME: SHARED_ZIP_LINE_NAME,
            LONG_RANGE_ZIP_LINE_NAME: SHARED_LONG_RANGE_ZIP_LINE_NAME,
        }.get(node.name)
        if shared_name is None:
            result.append(node)
            continue

        has_power = any(math.dist(node.xyz, point) <= radius for point in points)
        result.append(
            ZipLineNode(
                node_id=node.node_id,
                map_id=node.map_id,
                level_id=node.level_id,
                name=shared_name if not has_power else node.name,
                x=node.x,
                y=node.y,
                z=node.z,
            )
        )

    return result


@dataclass(frozen=True)
class ZipLineNode:
    """一个用户滑索架。"""

    node_id: str
    map_id: str
    level_id: str
    name: str
    x: float
    y: float
    z: float

    @property
    def connect_range_m(self) -> float:
        """该滑索架允许的最大连接距离。"""
        return ZIP_LINE_RANGES[self.name]

    @property
    def xz(self) -> tuple[float, float]:
        """导航网格使用的水平坐标。"""
        return self.x, self.z

    @property
    def xyz(self) -> tuple[float, float, float]:
        """官方地图连接距离使用的三维坐标。"""
        return self.x, self.y, self.z

    @property
    def is_long_range(self) -> bool:
        """是否属于长距滑索架。"""
        return self.name in LONG_RANGE_ZIP_LINE_NAMES


@dataclass(frozen=True)
class ZipLineLink:
    """两个滑索架之间的无向可连接边。"""

    first_id: str
    second_id: str
    distance_m: float
    max_range_m: float

    def other(self, node_id: str) -> str:
        """返回边的另一端节点 ID。"""
        if node_id == self.first_id:
            return self.second_id
        if node_id == self.second_id:
            return self.first_id
        raise KeyError(node_id)


@dataclass(frozen=True)
class ZipLineStep:
    """规划路线中的一次滑索移动。"""

    entry: ZipLineNode
    exit: ZipLineNode
    distance_m: float

    @property
    def link_id(self) -> str:
        """稳定标识一次有向移动，便于日志与测试。"""
        return f"{self.entry.node_id}->{self.exit.node_id}"


class ZipLineGraph:
    """按地图保存的滑索节点与无向连接边。"""

    def __init__(self, nodes: Iterable[ZipLineNode], links: Iterable[ZipLineLink]):
        self.nodes = tuple(sorted(nodes, key=lambda node: node.node_id))
        self.links = tuple(
            sorted(
                links,
                key=lambda link: (
                    link.first_id,
                    link.second_id,
                    link.distance_m,
                ),
            )
        )
        self._nodes_by_id = {node.node_id: node for node in self.nodes}
        links_by_node: dict[str, list[ZipLineLink]] = {node.node_id: [] for node in self.nodes}
        for link in self.links:
            links_by_node.setdefault(link.first_id, []).append(link)
            links_by_node.setdefault(link.second_id, []).append(link)
        self._links_by_node = {node_id: tuple(node_links) for node_id, node_links in links_by_node.items()}

    def __bool__(self) -> bool:
        return bool(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)

    def node(self, node_id: str) -> ZipLineNode:
        """按 ID 取节点；不存在时抛 ``KeyError``。"""
        return self._nodes_by_id[node_id]

    def links_for(self, node_id: str) -> tuple[ZipLineLink, ...]:
        """返回节点的所有可连接边。"""
        return self._links_by_node.get(node_id, ())

    def for_map(self, map_id: str) -> ZipLineGraph:
        """筛选同一地图的子图；``map_id`` 为空时原样返回。"""
        map_id = str(map_id or "").strip()
        if not map_id:
            return self
        nodes = [node for node in self.nodes if not node.map_id or node.map_id == map_id]
        node_ids = {node.node_id for node in nodes}
        links = [link for link in self.links if link.first_id in node_ids and link.second_id in node_ids]
        return ZipLineGraph(nodes, links)

    def summary(self) -> dict:
        """返回可直接写日志的简要统计。"""
        by_name: dict[str, int] = {}
        for node in self.nodes:
            by_name[node.name] = by_name.get(node.name, 0) + 1
        return {
            "nodes": len(self.nodes),
            "links": len(self.links),
            "by_name": by_name,
        }

    @classmethod
    def from_mark_payloads(
        cls,
        payloads: Iterable[Any],
        *,
        map_id: str = "",
        distance_tolerance_m: float = 1e-6,
        shared_facility_points: Iterable[dict[str, float]] | None = None,
        include_shared_zip_lines: bool = True,
        shared_radius_m: float = SHARED_ZIP_LINE_RADIUS_M,
    ) -> ZipLineGraph:
        """从 ``mark/list`` 响应构造滑索图。

        只读取 ``data.markTemplates`` 与 ``data.saveMarks``。节点按 ``id`` 去重；
        同一地图且同一层级（层级都为空时不做限制）的点才会尝试连接。
        """
        wanted_map = str(map_id or "").strip()
        nodes_by_id: dict[str, ZipLineNode] = {}

        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            templates = {
                str(template.get("id")): str(template.get("name") or "").strip()
                for template in data.get("markTemplates") or []
                if isinstance(template, dict) and template.get("id") is not None
            }
            for mark in data.get("saveMarks") or []:
                if not isinstance(mark, dict):
                    continue
                template_id = str(mark.get("templateId"))
                name = templates.get(template_id) or ZIP_LINE_TEMPLATE_IDS.get(template_id)
                if name not in ZIP_LINE_RANGES:
                    continue
                pos = mark.get("pos")
                if not isinstance(pos, dict):
                    continue
                x = _finite_float(pos.get("x"))
                y = _finite_float(pos.get("y"))
                z = _finite_float(pos.get("z"))
                if x is None or y is None or z is None:
                    continue
                node_map_id = str(mark.get("mapId") or wanted_map or "").strip()
                if wanted_map and node_map_id and node_map_id != wanted_map:
                    continue
                node_id = str(mark.get("id") or "").strip()
                if not node_id:
                    node_id = f"{node_map_id}:{name}:{x:.3f}:{y:.3f}:{z:.3f}"
                nodes_by_id[node_id] = ZipLineNode(
                    node_id=node_id,
                    map_id=node_map_id,
                    level_id=str(mark.get("levelId") or "").strip(),
                    name=name,
                    x=x,
                    y=y,
                    z=z,
                )

        nodes = sorted(nodes_by_id.values(), key=lambda node: node.node_id)
        if shared_facility_points is not None:
            nodes = _mark_shared_zip_line_nodes(nodes, shared_facility_points, shared_radius_m)
        if not include_shared_zip_lines:
            nodes = [node for node in nodes if node.name not in SHARED_ZIP_LINE_NAMES]

        links: list[ZipLineLink] = []
        tolerance = max(0.0, float(distance_tolerance_m))
        for index, first in enumerate(nodes):
            for second in nodes[index + 1 :]:
                if first.map_id != second.map_id:
                    continue
                if first.level_id and second.level_id and first.level_id != second.level_id:
                    continue
                distance = math.dist(first.xyz, second.xyz)
                max_range = (
                    ZIP_LINE_RANGES[LONG_RANGE_ZIP_LINE_NAME]
                    if first.is_long_range and second.is_long_range
                    else ZIP_LINE_RANGES[ZIP_LINE_NAME]
                )
                if distance <= 0.0 or distance > max_range + tolerance:
                    continue
                links.append(
                    ZipLineLink(
                        first_id=first.node_id,
                        second_id=second.node_id,
                        distance_m=distance,
                        max_range_m=max_range,
                    )
                )
        return cls(nodes, links)
