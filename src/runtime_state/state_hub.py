"""线程安全的进程内最新值状态网关。

本模块只保存各主题的最新快照，不负责采样、识别或任务调度。状态采用
latest-value 语义：新值覆盖旧值，消费者按最大时效读取；超过时效时返回
``None``，避免把旧状态伪装成实时状态。
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.runtime_state.topics import RuntimeTopic

TopicKey = RuntimeTopic | str


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """一次状态发布产生的不可变信封。"""

    topic: str
    value: dict[str, Any]
    sequence: int
    published_at: float
    expires_at: float | None
    source: str

    def is_fresh(self, now: float) -> bool:
        """判断快照在当前时刻是否仍在有效期内。"""

        return self.expires_at is None or now <= self.expires_at

    def copied_value(self) -> dict[str, Any]:
        """返回状态的深拷贝，避免消费者修改总线内部数据。"""

        return copy.deepcopy(self.value)


class RuntimeStateHub:
    """按主题维护最新状态快照。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._snapshots: dict[str, StateSnapshot] = {}
        self._sequences: dict[str, int] = {}

    @staticmethod
    def _topic_name(topic: TopicKey) -> str:
        return topic.value if isinstance(topic, RuntimeTopic) else str(topic)

    def publish(
        self,
        topic: TopicKey,
        value: dict[str, Any],
        *,
        source: str = "",
        now: float | None = None,
        ttl: float | None = None,
    ) -> StateSnapshot:
        """发布一个主题的新快照，并返回其信封。"""

        topic_name = self._topic_name(topic)
        published_at = time.monotonic() if now is None else float(now)
        expires_at = None
        if ttl is not None:
            expires_at = published_at + max(0.0, float(ttl))
        with self._lock:
            sequence = self._sequences.get(topic_name, 0) + 1
            self._sequences[topic_name] = sequence
            snapshot = StateSnapshot(
                topic=topic_name,
                value=copy.deepcopy(value),
                sequence=sequence,
                published_at=published_at,
                expires_at=expires_at,
                source=str(source or ""),
            )
            self._snapshots[topic_name] = snapshot
            return snapshot

    def snapshot(
        self,
        topic: TopicKey,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> StateSnapshot | None:
        """读取主题快照；不存在、过期或超过 ``max_age`` 时返回 ``None``。"""

        topic_name = self._topic_name(topic)
        check_at = time.monotonic() if now is None else float(now)
        with self._lock:
            snapshot = self._snapshots.get(topic_name)
        if snapshot is None or not snapshot.is_fresh(check_at):
            return None
        if max_age is not None and check_at - snapshot.published_at > max(0.0, float(max_age)):
            return None
        return snapshot

    def value(
        self,
        topic: TopicKey,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """读取主题状态的深拷贝。"""

        snapshot = self.snapshot(topic, max_age=max_age, now=now)
        return None if snapshot is None else snapshot.copied_value()

    def clear(self, topic: TopicKey | None = None) -> None:
        """清空一个主题，或清空全部主题。"""

        with self._lock:
            if topic is None:
                self._snapshots.clear()
                self._sequences.clear()
                return
            topic_name = self._topic_name(topic)
            self._snapshots.pop(topic_name, None)
            self._sequences.pop(topic_name, None)

    def health(self) -> list[dict[str, Any]]:
        """返回各主题的序号和发布时间，供调试面板或日志使用。"""

        with self._lock:
            snapshots = list(self._snapshots.values())
        return [
            {
                "topic": snapshot.topic,
                "sequence": snapshot.sequence,
                "published_at": snapshot.published_at,
                "expires_at": snapshot.expires_at,
                "source": snapshot.source,
            }
            for snapshot in sorted(snapshots, key=lambda item: item.topic)
        ]


_HUB = RuntimeStateHub()


def get_runtime_state_hub() -> RuntimeStateHub:
    """返回进程内共享状态网关。"""

    return _HUB


__all__ = [
    "RuntimeStateHub",
    "StateSnapshot",
    "get_runtime_state_hub",
]
