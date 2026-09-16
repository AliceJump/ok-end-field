# -*- coding: utf-8 -*-
"""小地图定位常驻任务。

该 TriggerTask 是融合定位状态的唯一生产者：持续采样小地图里程计、消费地图 WS、
执行静止校准，并把最新快照暴露给导航、物品导航和实时位置测试任务。

关键约束：

- 定位实例必须常驻，不能因执行器短暂暂停或暂无消费者而清理 WS；
- 所有消费者都应调用 ``minimap_position(frame=...)``，把同一帧交给该实例；
- 导航只读取 ``position_trusted`` 为真的绝对坐标，重锚后必须等待静校准。
"""

from ok import TriggerTask

from src.core.BaseEfTask import BaseEfTask
from src.icons import Icons
from src.tasks.mixin.minimap_position_mixin import MinimapPositionMixin


class MinimapPositionTask(MinimapPositionMixin, BaseEfTask, TriggerTask):
    """统一维护小地图融合定位，供导航和其他实时任务消费。"""

    requires_foreground = True

    def __init__(self, *args, **kwargs):
        """注册触发任务元数据并初始化常驻定位状态。"""
        super().__init__(*args, **kwargs)
        self.name = "小地图定位"
        self.description = "持续融合小地图里程计、朝向和地图坐标，静止时自动校准"
        self.icon = Icons.Navigation
        self.trigger_interval = 0.5

        self.default_config.update({
            "_enabled": True,
            **self.minimap_position_default_config(),
        })
        self.config_description.update(self.minimap_position_config_description())
        self._init_minimap_position_mixin()
        # 定位服务是常驻生产者，不能因为执行器暂停或暂无消费者而主动断开 WS。
        self._map_ws_consumer_idle_timeout = 0.0
        self._last_status_key = None
        self._last_status_log_at = 0.0

    def post_init(self):
        """首次升级时，把旧寻路任务里的非默认定位配置迁移过来。"""
        executor = getattr(self, "_executor", None)
        if executor is None or not hasattr(executor, "get_all_tasks"):
            return
        defaults = self.minimap_position_default_config()
        for task in executor.get_all_tasks():
            if task is self or not getattr(task, "config", None):
                continue
            for key, default in defaults.items():
                old_value = task.config.get(key)
                if old_value is None or old_value == default:
                    continue
                if self.config.get(key) != default:
                    continue
                self.config[key] = old_value
                self.log_info(f"已迁移定位配置 {key}: {old_value!r}")

    def run(self):
        """采样一拍定位并发布节流后的状态；本任务不结束自身。"""
        if not self.in_world():
            return False
        self.start_minimap_position(wait_stable=False)
        frame = self.next_frame()
        if frame is None:
            return False
        state = self.minimap_position(frame=frame, now=self.active_time())
        x, z = state.get("x"), state.get("z")
        status_reported = False
        if state.get("just_synced") and state.get("sync_residual") is not None:
            residual = state["sync_residual"]
            self.log_info(
                "静止校准误差 "
                f"{float(residual['dist']):.3f}m "
                f"(dx={float(residual['dx']):+.3f}, "
                f"dz={float(residual['dz']):+.3f})"
            )
            self.info_set("小地图校准", f"{float(residual['dist']):.2f}m")
            status_reported = True
        if x is not None and z is not None:
            if not state.get("position_trusted", True):
                status = "待校准"
            else:
                status = "静止" if state.get("rest") else "移动"
        else:
            status = f"未锚定({state.get('odom_reason') or 'no_position'})"
        status_key = (
            str(state.get("map_id") or ""),
            bool(state.get("anchor_set")),
            bool(state.get("position_trusted", True)),
            status,
        )
        now = self.active_time()
        if (
            not status_reported
            and (
                status_key != self._last_status_key
                or now - self._last_status_log_at >= 30.0
            )
        ):
            text = (
                f"({float(x):.1f}, {float(z):.1f}) {status}"
                if x is not None and z is not None else status
            )
            self.info_set("小地图定位", text)
            self._last_status_key = status_key
            self._last_status_log_at = now
        return False

    def on_destroy(self):
        """任务销毁时停止 WS 客户端和本地位置源。"""
        self.stop_minimap_position()
