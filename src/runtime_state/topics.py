"""运行时状态主题名。

主题名是对外契约，发布者和消费者都应引用常量，避免散落字符串拼写错误。
"""

from enum import StrEnum


class RuntimeTopic(StrEnum):
    """常用运行时主题。"""

    WORLD_POSE = "world.pose"
    WORLD_SCENE = "world.scene"
    WORLD_TARGETS = "world.targets"
    SENSOR_WS = "sensor.ws"
    SYSTEM_HEALTH = "system.health"


__all__ = ["RuntimeTopic"]
