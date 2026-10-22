"""按需读取抓包后端的滑索快照，并转换成导航使用的双向滑索图。

``/api/travel-poles`` 是完整快照接口，不属于高频坐标链。导航只在开始一次
网格规划、需要滑索图时请求一次；``/api/state`` 轮询不携带这些建筑数据。

后端保留最后一份快照供离线查看，因此调用方不能只看 ``nodes`` 是否非空：
连接已结束、尚未完成 chapter 同步、快照曾解析失败或缺少 ``updatedAt`` 时，
这份数据都不能参与路线规划。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from src.nav.zip_line_graph import ZIP_LINE_NAME, ZipLineGraph, ZipLineLink, ZipLineNode

__all__ = [
    "CAPTURE_TRAVEL_POLES_PATH",
    "CaptureTravelPoleDefaultLink",
    "CaptureTravelPoleLoad",
    "CaptureTravelPoleNode",
    "CaptureTravelPoleSnapshot",
    "CaptureTravelPoleSource",
    "build_capture_travel_pole_graph",
    "chapter_id_for_map",
    "parse_capture_travel_pole_snapshot",
]

CAPTURE_TRAVEL_POLES_PATH = "/api/travel-poles"
_READY_FLOW_STATUS = "正在解码"
_MAP_CHAPTER_PATTERN = re.compile(r"^map0*(\d+)$", re.IGNORECASE)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _id_text(value: Any) -> str:
    """把协议整数 ID 转成稳定字符串；不要从组件 ID 反推节点 ID。"""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _vector(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    xyz = tuple(_finite_float(value.get(axis)) for axis in ("x", "y", "z"))
    if any(component is None for component in xyz):
        return None
    return xyz  # type: ignore[return-value]


@dataclass(frozen=True)
class CaptureTravelPoleNode:
    """后端 ``nodes[]`` 中一条已解析的滑索节点。"""

    chapter_id: str
    node_id: str
    component_id: str
    template_id: str
    factory_map_id: Any
    position: tuple[float, float, float]
    rotation: tuple[float, float, float] | None
    default_next: str
    shared: bool
    is_deactive: bool
    power: dict[str, Any] | None
    social: dict[str, Any] | None

    @property
    def key(self) -> tuple[str, str]:
        """服务器节点键；componentId 只用于组件索引，不能替代 nodeId。"""
        return self.chapter_id, self.node_id

    @property
    def graph_id(self) -> str:
        return f"{self.chapter_id}:{self.node_id}"


@dataclass(frozen=True)
class CaptureTravelPoleDefaultLink:
    """后端 ``defaultLinks[]`` 中一条服务器配置的默认链接记录。

    其中的方向只表示服务器记录的默认下一站来源；滑索设施本身按双向通行。
    """

    chapter_id: str
    source_node_id: str
    source_component_id: str
    target_node_id: str
    target_resolved: bool
    distance_3d: float | None
    yaw_to_target: float | None

    @property
    def source_key(self) -> tuple[str, str]:
        return self.chapter_id, self.source_node_id

    @property
    def target_key(self) -> tuple[str, str]:
        return self.chapter_id, self.target_node_id


@dataclass(frozen=True)
class CaptureTravelPoleSnapshot:
    """完整滑索响应及其可信性元数据。"""

    flow: int | None
    flow_status: str
    connection_active: bool
    updated_at: float | None
    synced_chapters: tuple[str, ...]
    error: str | None
    nodes: tuple[CaptureTravelPoleNode, ...]
    default_links: tuple[CaptureTravelPoleDefaultLink, ...]

    def invalid_reason(self) -> str:
        """返回不可用于规划的原因；空串表示快照元数据完整。"""
        if self.error:
            return f"snapshot_error:{self.error}"
        if not self.connection_active:
            return "connection_inactive"
        if self.flow_status != _READY_FLOW_STATUS:
            return f"flow_status:{self.flow_status or 'missing'}"
        if not self.synced_chapters:
            return "not_synced"
        if self.updated_at is None or self.updated_at <= 0:
            return "missing_updated_at"
        return ""


@dataclass(frozen=True)
class CaptureTravelPoleLoad:
    """一次滑索加载结果。

    ``cacheable=True`` 且 ``graph=None`` 表示接口成功、当前 chapter 确实没有
    可用的滑索；``cacheable=False`` 表示 HTTP、同步状态或快照错误，不能把
    这次结果当成稳定的空图。
    """

    graph: ZipLineGraph | None
    snapshot: CaptureTravelPoleSnapshot | None
    reason: str
    cacheable: bool


def parse_capture_travel_pole_snapshot(payload: dict) -> CaptureTravelPoleSnapshot:
    """解析后端响应；字段缺失留给 :meth:`invalid_reason` 判定。"""
    if not isinstance(payload, dict):
        raise ValueError("travel-poles payload is not an object")

    raw_nodes = payload.get("nodes")
    raw_links = payload.get("defaultLinks")
    if raw_nodes is None:
        raw_nodes = []
    if raw_links is None:
        raw_links = []
    if not isinstance(raw_nodes, list) or not isinstance(raw_links, list):
        raise ValueError("travel-poles nodes/defaultLinks must be arrays")

    nodes: list[CaptureTravelPoleNode] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        chapter_id = str(raw.get("chapterId") or "").strip()
        node_id = _id_text(raw.get("nodeId"))
        position = _vector(raw.get("position"))
        if not chapter_id or not node_id or position is None:
            continue
        nodes.append(
            CaptureTravelPoleNode(
                chapter_id=chapter_id,
                node_id=node_id,
                component_id=_id_text(raw.get("componentId")),
                template_id=str(raw.get("templateId") or "").strip(),
                factory_map_id=raw.get("mapId"),
                position=position,
                rotation=_vector(raw.get("rotation")),
                default_next=_id_text(raw.get("defaultNext")),
                shared=raw.get("shared") is True,
                is_deactive=raw.get("isDeactive") is True,
                power=raw.get("power") if isinstance(raw.get("power"), dict) else None,
                social=raw.get("social") if isinstance(raw.get("social"), dict) else None,
            )
        )

    links: list[CaptureTravelPoleDefaultLink] = []
    for raw in raw_links:
        if not isinstance(raw, dict):
            continue
        chapter_id = str(raw.get("chapterId") or "").strip()
        source_node_id = _id_text(raw.get("sourceNodeId"))
        target_node_id = _id_text(raw.get("targetNodeId"))
        if not chapter_id or not source_node_id or not target_node_id:
            continue
        links.append(
            CaptureTravelPoleDefaultLink(
                chapter_id=chapter_id,
                source_node_id=source_node_id,
                source_component_id=_id_text(raw.get("sourceComponentId")),
                target_node_id=target_node_id,
                target_resolved=raw.get("targetResolved") is True,
                distance_3d=_finite_float(raw.get("distance3D")),
                yaw_to_target=_finite_float(raw.get("yawToTarget")),
            )
        )

    flow_value = payload.get("flow")
    flow = int(flow_value) if isinstance(flow_value, (int, float)) and not isinstance(flow_value, bool) else None
    raw_chapters = payload.get("syncedChapters")
    synced_chapters = (
        tuple(dict.fromkeys(str(item).strip() for item in raw_chapters if str(item).strip()))
        if isinstance(raw_chapters, list)
        else ()
    )
    raw_error = payload.get("error")
    error_text = str(raw_error).strip() if raw_error else None
    return CaptureTravelPoleSnapshot(
        flow=flow,
        flow_status=str(payload.get("flowStatus") or "").strip(),
        connection_active=payload.get("connectionActive") is True,
        updated_at=_finite_float(payload.get("updatedAt")),
        synced_chapters=synced_chapters,
        error=error_text,
        nodes=tuple(nodes),
        default_links=tuple(links),
    )


def chapter_id_for_map(map_id: str, synced_chapters: tuple[str, ...]) -> str:
    """为已知网格名选择 chapter；无法唯一确定时返回空串。

    当前网格名 ``map02`` 与后端场景表的 ``domain_2`` 对应。若未来出现新的地图名，
    只有在后端仅同步了一个 chapter 时才使用它；不做跨 chapter 的合并猜测。
    """
    map_id = str(map_id or "").strip()
    synced = tuple(dict.fromkeys(str(item).strip() for item in synced_chapters if str(item).strip()))
    match = _MAP_CHAPTER_PATTERN.fullmatch(map_id)
    if match:
        candidate = f"domain_{int(match.group(1))}"
        return candidate if candidate in synced else ""
    return synced[0] if len(synced) == 1 else ""


def _empty_result(reason: str, *, cacheable: bool, snapshot=None) -> CaptureTravelPoleLoad:
    return CaptureTravelPoleLoad(graph=None, snapshot=snapshot, reason=reason, cacheable=cacheable)


def build_capture_travel_pole_graph(
    snapshot: CaptureTravelPoleSnapshot,
    *,
    map_id: str,
    include_shared: bool,
) -> CaptureTravelPoleLoad:
    """把一次可信快照转换成现有 ``ZipLineGraph``。

    只消费 ``defaultLinks`` 来确认两个滑索架相连，不按距离、供电线或
    ComponentId 推导额外连接。确认后的设施连接按双向边供导航使用。
    未解析的目标、失效节点和未选中的 chapter 都不产生边。
    """
    invalid_reason = snapshot.invalid_reason()
    if invalid_reason:
        return _empty_result(invalid_reason, cacheable=False, snapshot=snapshot)

    chapter_id = chapter_id_for_map(map_id, snapshot.synced_chapters)
    if not chapter_id:
        return _empty_result("chapter_not_synced", cacheable=False, snapshot=snapshot)

    nodes_by_key: dict[tuple[str, str], CaptureTravelPoleNode] = {}
    for node in snapshot.nodes:
        if node.chapter_id != chapter_id or node.is_deactive:
            continue
        if node.shared and not include_shared:
            continue
        nodes_by_key[node.key] = node

    if not nodes_by_key:
        return _empty_result("empty_nodes", cacheable=True, snapshot=snapshot)

    graph_nodes = [
        ZipLineNode(
            node_id=node.graph_id,
            map_id=str(map_id or ""),
            level_id=node.chapter_id,
            name=node.template_id or ZIP_LINE_NAME,
            x=node.position[0],
            y=node.position[1],
            z=node.position[2],
            template_id=node.template_id,
        )
        for node in sorted(nodes_by_key.values(), key=lambda item: item.key)
    ]

    links_by_pair: dict[tuple[str, str], ZipLineLink] = {}
    for default_link in snapshot.default_links:
        if default_link.chapter_id != chapter_id or not default_link.target_resolved:
            continue
        source = nodes_by_key.get(default_link.source_key)
        target = nodes_by_key.get(default_link.target_key)
        if source is None or target is None:
            continue
        distance = default_link.distance_3d
        if distance is None:
            distance = math.dist(source.position, target.position)
        if distance <= 0:
            continue
        first_id, second_id = sorted((source.graph_id, target.graph_id))
        links_by_pair[(first_id, second_id)] = ZipLineLink(
            first_id=first_id,
            second_id=second_id,
            distance_m=distance,
            max_range_m=distance,
        )

    graph = ZipLineGraph(graph_nodes, links_by_pair.values())
    return CaptureTravelPoleLoad(graph=graph, snapshot=snapshot, reason="ok", cacheable=True)


class CaptureTravelPoleSource:
    """读取 ``GET /api/travel-poles``；失败时返回可诊断的降级结果。"""

    def __init__(self, port: int, *, log_warning=None, timeout: float = 2.0):
        self.port = int(port)
        self.timeout = float(timeout)
        self._log_warning = log_warning
        self._warned: set[str] = set()

    def _warn_once(self, issue: str, message: str) -> None:
        if issue in self._warned:
            return
        self._warned.add(issue)
        if callable(self._log_warning):
            self._log_warning(message)

    def _fetch(self) -> dict | None:
        url = f"http://127.0.0.1:{self.port}{CAPTURE_TRAVEL_POLES_PATH}"
        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="ignore"))
        except error.HTTPError as exc:
            if exc.code == 404:
                self._warn_once(
                    "http:404",
                    "实验技术：抓包后端没有 /api/travel-poles。请升级到 v0.1.3+，"
                    "并确认启动后端时没有使用 --no-http；本次导航继续使用普通网格路线。",
                )
            else:
                self._warn_once(
                    f"http:{exc.code}",
                    f"实验技术：读取抓包滑索失败（HTTP {exc.code}）；本次导航继续使用普通网格路线。",
                )
            return None
        except (error.URLError, TimeoutError, OSError) as exc:
            self._warn_once(
                f"unreachable:{type(exc).__name__}",
                f"实验技术：连接不到抓包滑索接口 127.0.0.1:{self.port}（{type(exc).__name__}）。"
                "请确认后端正在以 HTTP 模式运行；本次导航继续使用普通网格路线。",
            )
            return None
        except (ValueError, UnicodeError) as exc:
            self._warn_once(
                f"bad_payload:{type(exc).__name__}",
                f"实验技术：抓包滑索接口返回了无法解析的 JSON（{type(exc).__name__}）。",
            )
            return None
        if not isinstance(payload, dict):
            self._warn_once("bad_payload:not_object", "实验技术：抓包滑索接口返回的不是 JSON 对象。")
            return None
        return payload

    def load(self, *, map_id: str, include_shared: bool) -> CaptureTravelPoleLoad:
        payload = self._fetch()
        if payload is None:
            return _empty_result("http_unavailable", cacheable=False)
        try:
            snapshot = parse_capture_travel_pole_snapshot(payload)
        except ValueError as exc:
            self._warn_once(
                f"bad_payload:{exc}",
                f"实验技术：抓包滑索快照结构无效（{exc}）；本次导航继续使用普通网格路线。",
            )
            return _empty_result("invalid_payload", cacheable=False)
        result = build_capture_travel_pole_graph(
            snapshot,
            map_id=map_id,
            include_shared=include_shared,
        )
        if not result.cacheable:
            self._warn_once(
                f"snapshot:{result.reason}",
                f"实验技术：抓包滑索快照暂不可用（{result.reason}）；本次导航继续使用普通网格路线。",
            )
        return result
