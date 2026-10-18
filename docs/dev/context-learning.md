# 导航对中与上下文学习

Issue #543 的运动模型与学习权限独立。`NavigationMixin.align_ocr_or_find_target_to_center()` 的
`move_profile` 描述目标坐标语义：`hud_direction` 使用椭圆归一化、中心减速与外圈加速；
`world_projection` 使用普通屏幕投影对中。

当前导航 Feature 默认为 HUD，OCR 默认为世界投影；方向型 OCR 需显式指定 HUD。
YOLO 始终使用普通对中，角色名字 OCR 显式使用世界投影。`navigate_until_target()` 可通过
`nav_move_profile` 透传给途中实时修正。OCR 专用检测/移动 callbacks 保持原适用范围。

两种 mover 的计算位于 `src/interaction/target_movement.py`；滑索保留自己的 geometry 参数。
实际鼠标发送经过任务实例，激活被拒绝时不报告已接受的位移。目标短暂丢失时仍使用同一
运动模型及轴约束，但旧框不能成为成功证据。实际成功仍由当前帧检测与原导航状态机验证。

战后 HUD 方向标也使用椭圆；战后 YOLO 使用普通对中。运动 profile 不代表允许读取或写入
学习经验。本次运动模型改造没有扩展经验缓存的适用场景。
