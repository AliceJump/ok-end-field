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

## 学习内核与权限边界

`src/core/learning/` 不依赖 Task、Qt 或识别器。`WorkflowContext` 使用流程、阶段、大区/
小区、入口锚点、canonical target ID、入站路线、捕获/相机 profile 和可选起始 yaw 建立
版本化身份。`RouteContext` 同时提供完整 private key 与包含当前段目标的 prefix key；
未知 family 只有 private key。本阶段只定义这些身份，尚未启用前缀查询和旧缓存迁移。

`LearningRecord` 将动作统计与归一化 `TargetPrior` 分开。动作采用可合并 Welford 均值/M2，
样本不足时不提供方差估计。legacy 数据的方差标为 unknown，不按零方差使用；开始收集
新样本时保留 legacy 数量，单独建立可验证的新统计。high/medium 复用还需至少三个已验证
低方差样本且无连续失败；medium 动作减半，low 仅提供目标提示，invalid 不读取先验。

`LearningStore` 使用独立 schema v1、内存索引、同进程锁、唯一临时文件及原子替换。
写失败不提交内存状态，损坏条目跳过，超限按更新时间淘汰。多个同进程实例会刷新文件变化，
更新操作串行化；此格式不承诺多进程并发合并。默认路径为忽略目录
`tmp/context_learning_cache.json`，不改变用户任务配置。

`create_learning_session()` 是流程会话入口。调用者提供同一 active clock 域的时间，按
接受的输入记录包含 replay 与 correction 的总量，并提供输入前的单调 frame ID。只有
输入后的真实新帧对中证据，加上更晚的新帧到达/交互证据，才可提交样本；一个会话只能
领取一次 Action Prior。`confirm()`/`fail()`/`close()` 均为终态；异常和停止时调用者应
在 finally 中 close，未确认的样本不会自动写入。搜索、额外步行和不完整输入污染本次
动作样本，可信度只降不升。profile 的轴约束同样约束 replay 与记录。

`deny_learning()` 为嵌套、执行上下文局部的禁止作用域。内层稳定入口不能解除父级禁止；
退出或停止会恢复权限，但此前 pending 会话永久失效。原 motion/yaw 缓存也在读写入口
执行这一保护。战斗主入口、触发任务确认进入战斗后的逻辑、战后寻路和战后 retry 覆盖内部重新传送与滑索，既不读取
历史动作，也不 replay 或写样本；手动传入的 initial_move 同样无效。实时 ellipse/normal
与实时 yaw 校准仍可运行。`battle`/`post_battle*` 流程身份自身也拒绝创建学习会话。
未检测到战斗的普通触发轮询不改变学习 epoch，不会让正常导航会话无故失效。

本阶段尚未把新会话接入送货等稳定入口；不将基础设施测试视为实际流程学习已经启用。
