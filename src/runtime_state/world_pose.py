"""世界坐标主题的稳定数据契约。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.runtime_state.state_hub import RuntimeStateHub
from src.runtime_state.topics import RuntimeTopic

WORLD_POSE_TTL_SECONDS = 2.0


def build_world_pose(
    state: Mapping[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """把小地图定位内部状态转换为稳定的对外坐标契约。"""

    return {
        "x": state.get("x"),
        "y": state.get("y"),
        "z": state.get("z"),
        "heading": state.get("heading"),
        "heading_score": state.get("heading_score"),
        "map_id": state.get("map_id"),
        "position_trusted": bool(state.get("position_trusted", False)),
        "trust_reason": state.get("trust_reason"),
        "anchor_set": bool(state.get("anchor_set", False)),
        "rest": bool(state.get("rest", False)),
        "odom_ok": bool(state.get("odom_ok", False)),
        "odom_reason": state.get("odom_reason"),
        "sync_needed": bool(state.get("sync_needed", False)),
        "sync_checked": bool(state.get("sync_checked", False)),
        "sync_redundant": bool(state.get("sync_redundant", False)),
        "just_synced": bool(state.get("just_synced", False)),
        "sync_residual": state.get("sync_residual"),
        "sync_seq": int(state.get("sync_seq") or 0),
        "distance_since_sync": float(state.get("distance_since_sync") or 0.0),
        "sample_t": state.get("sample_t"),
        "ws": state.get("ws"),
        "ws_xyz": state.get("ws_xyz"),
        "error": state.get("error"),
        "dmap_px": state.get("dmap_px"),
        "world_delta": state.get("world_delta"),
        "source": source,
    }


def publish_world_pose(
    hub: RuntimeStateHub,
    state: Mapping[str, Any],
    *,
    source: str,
    now: float,
):
    """发布一帧世界坐标，并返回状态信封。"""

    return hub.publish(
        RuntimeTopic.WORLD_POSE,
        build_world_pose(state, source=source),
        source=source,
        now=now,
        ttl=WORLD_POSE_TTL_SECONDS,
    )


__all__ = [
    "WORLD_POSE_TTL_SECONDS",
    "build_world_pose",
    "publish_world_pose",
]
