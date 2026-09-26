"""任务侧运行时状态客户端。

普通任务只通过本 Mixin 读取快照或请求定位采样，不直接操作 WS、小地图
里程计和融合状态。长任务执行期间 ok-script 不会并行调度 TriggerTask，
因此 ``world_pose(refresh=True)`` 会通过统一控制面请求定位所有者采样一帧。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.runtime_state.pose_provider import PoseProvider
from src.runtime_state.state_hub import RuntimeStateHub, StateSnapshot, get_runtime_state_hub
from src.runtime_state.topics import RuntimeTopic

StatePredicate = Callable[[dict[str, Any]], bool]


class RuntimeStateMixin:
    """为任务提供统一的状态发布、读取和定位采样入口。"""

    def _init_runtime_state_mixin(self, *, hub: RuntimeStateHub | None = None) -> None:
        self._runtime_state_hub = hub or get_runtime_state_hub()
        self._runtime_position_service = None

    @property
    def runtime_state_hub(self) -> RuntimeStateHub:
        """返回当前任务绑定的状态网关。"""

        hub = getattr(self, "_runtime_state_hub", None)
        if hub is None:
            self._init_runtime_state_mixin()
            hub = self._runtime_state_hub
        return hub

    def publish_runtime_state(
        self,
        topic: RuntimeTopic | str,
        value: dict[str, Any],
        *,
        source: str = "",
        ttl: float | None = None,
        now: float | None = None,
    ) -> StateSnapshot:
        """发布一个状态主题。"""

        return self.runtime_state_hub.publish(
            topic,
            value,
            source=source,
            ttl=ttl,
            now=self.active_time() if now is None else now,
        )

    def runtime_state_snapshot(
        self,
        topic: RuntimeTopic | str,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> StateSnapshot | None:
        """读取主题快照信封。"""

        return self.runtime_state_hub.snapshot(
            topic,
            max_age=max_age,
            now=self.active_time() if now is None else now,
        )

    def runtime_state(
        self,
        topic: RuntimeTopic | str,
        *,
        max_age: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """读取主题值；无数据或已过期时返回 ``None``。"""

        return self.runtime_state_hub.value(
            topic,
            max_age=max_age,
            now=self.active_time() if now is None else now,
        )

    def wait_runtime_state(
        self,
        topic: RuntimeTopic | str,
        predicate: StatePredicate | None = None,
        *,
        max_age: float | None = None,
        timeout: float = 10.0,
        tick: float = 0.1,
    ) -> dict[str, Any] | None:
        """轮询一个主题，直到满足条件或超时。"""

        deadline = self.active_time() + max(0.0, float(timeout))
        interval = max(0.01, float(tick))
        while True:
            value = self.runtime_state(topic, max_age=max_age)
            if value is not None and (predicate is None or predicate(value)):
                return value
            now = self.active_time()
            if now >= deadline:
                return None
            self.sleep(min(interval, deadline - now))

    def get_runtime_position_service(self):
        """获取共享定位任务；只在内部缓存，不启动资源。"""

        service = getattr(self, "_runtime_position_service", None)
        if service is not None:
            return service
        override = getattr(self, "_minimap_position_service", None)
        if override is not None:
            self._runtime_position_service = override
            return override
        getter = getattr(self, "get_task_by_class", None)
        if callable(getter):
            from src.tasks.localization.MinimapPositionTask import MinimapPositionTask

            self._runtime_position_service = getter(MinimapPositionTask)
        return self._runtime_position_service

    def ensure_runtime_position_service(self, *, force_start: bool = False):
        """确保共享定位任务可用，并返回可采样的所有者。"""

        service = self.get_runtime_position_service()
        if service is None:
            self.log_warning("未注册「小地图定位」触发任务，无法获取实时位置", notify=True)
            return None
        if not getattr(service, "enabled", True):
            self.log_warning("「小地图定位」触发任务未启用，无法获取实时位置", notify=True)
            return None
        needs_start = (
            force_start
            or not bool(getattr(service, "_minimap_started", False))
            or not bool(getattr(service, "minimap_position_ready", False))
        )
        if needs_start:
            service.start_minimap_position(wait_stable=False)
        if not bool(getattr(service, "minimap_position_ready", False)):
            self.log_warning("小地图定位器未完成初始化，请检查全局「Nav Config」和游戏窗口", notify=True)
            return None
        return service

    def _pose_provider(self) -> PoseProvider | None:
        """确保定位提供者可用，并返回接口对象。"""

        return self.ensure_runtime_position_service()

    def pose_turn_to_bearing(self, target_deg: float, **kwargs) -> dict[str, Any]:
        """通过定位接口执行朝向控制。"""

        provider = self._pose_provider()
        if provider is None:
            return {"ok": False, "error": "position provider unavailable"}
        return provider.turn_to_bearing(target_deg, **kwargs)

    def pose_aim_view_to_bearing(self, target_deg: float, **kwargs) -> dict[str, Any]:
        """通过定位接口调整视角。"""

        provider = self._pose_provider()
        if provider is None:
            return {"ok": False, "error": "position provider unavailable"}
        return provider.aim_view_to_bearing(target_deg, **kwargs)

    def pose_yaw_per_pixel(self) -> float:
        """通过定位接口读取旋转比例。"""

        provider = self._pose_provider()
        return 0.0 if provider is None else float(provider.yaw_per_pixel())

    def pose_heading_min_score(self) -> float:
        """通过定位接口读取朝向最低置信度。"""

        provider = self._pose_provider()
        return 0.0 if provider is None else float(provider.heading_min_score())

    def pose_can_turn(self) -> bool:
        """通过定位接口判断当前是否允许转动视角。"""

        provider = self._pose_provider()
        return False if provider is None else bool(provider.can_turn())

    def pose_send_rotation(self, dx: int) -> None:
        """通过定位接口发送转视角位移。"""

        provider = self._pose_provider()
        if provider is not None:
            provider.send_rotation(int(dx))

    def pose_rest_diag(self) -> dict[str, Any] | None:
        """通过定位接口读取静止判定诊断。"""

        provider = self._pose_provider()
        return None if provider is None else provider.minimap_rest_diag()

    def refresh_world_pose(
        self,
        *,
        frame=None,
        now: float | None = None,
    ) -> bool:
        """请求定位所有者采样一帧并发布到状态网关。"""

        service = self.ensure_runtime_position_service()
        if service is None:
            return False
        sampler = getattr(service, "sample_world_pose", None)
        if not callable(sampler):
            self.log_warning("定位任务未实现 sample_world_pose 接口", notify=True)
            return False
        sampler(frame=frame, now=self.active_time() if now is None else now)
        return True

    def world_pose(
        self,
        *,
        frame=None,
        max_age: float = 1.0,
        refresh: bool = True,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """读取最新世界坐标，默认在读取前刷新一帧。"""

        check_at = self.active_time() if now is None else float(now)
        if refresh and not self.refresh_world_pose(frame=frame, now=check_at):
            return None
        return self.runtime_state(RuntimeTopic.WORLD_POSE, max_age=max_age, now=check_at)

    def wait_world_pose(
        self,
        predicate: StatePredicate | None = None,
        *,
        frame=None,
        max_age: float = 1.0,
        timeout: float = 10.0,
        tick: float = 0.1,
    ) -> dict[str, Any] | None:
        """持续刷新并等待满足条件的世界坐标。"""

        deadline = self.active_time() + max(0.0, float(timeout))
        interval = max(0.01, float(tick))
        while True:
            value = self.world_pose(frame=frame, max_age=max_age)
            if value is not None and (predicate is None or predicate(value)):
                return value
            now = self.active_time()
            if now >= deadline:
                return None
            self.sleep(min(interval, deadline - now))


__all__ = ["RuntimeStateMixin"]
