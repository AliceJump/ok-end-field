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
未知 family 只有 private key。路径查询和迁移的数据层见下文，业务调用接线仍在后续阶段。

`LearningRecord` 将动作统计与归一化 `TargetPrior` 分开。动作采用可合并 Welford 均值/M2，
样本不足时不提供方差估计。legacy 数据的方差标为 unknown，不按零方差使用；开始收集
新样本时保留 legacy 数量，单独建立可验证的新统计。high/medium 复用还需至少三个已验证
低方差样本且无连续失败；medium 动作减半，low 仅提供目标提示，invalid 不读取先验。

`LearningStore` 使用独立 schema v1、内存索引、同进程锁、唯一临时文件及原子替换。
写失败不提交内存状态，损坏条目跳过，超限按更新时间淘汰。多个同进程实例会刷新文件变化，
更新操作串行化；此格式不承诺多进程并发合并。默认路径为忽略目录
`tmp/context_learning_cache.json`，不改变用户任务配置。
同一规范化路径还维护进程内写入代数，成功 replace 后才递增，保证时间戳与大小相同的替换
也会使其他实例的缓存失效。只有确认文件不存在时才从空记录初始化；未知 schema、读取或
stat 失败时保留原文件、禁止覆盖，下次访问重新尝试。合法 schema 内的损坏条目仍逐条跳过。

`create_learning_session()` 是流程会话入口。调用者提供同一 active clock 域的时间，按
接受的输入记录包含 replay 与 correction 的总量，并提供输入前的单调 frame ID。取得
输入后的真实新帧对中证据，即通过 `confirm_alignment()` 提交样本并结束会话，不等待到达。
学习只覆盖 Initial Align，后续 `navigate_until_target()` 不接收学习会话、不读写持久动作先验，
导航动作或到达失败不反向修改已经自验证成功的对中样本。一个会话只能领取一次 Action Prior。
`confirm_alignment()`/`fail()`/`close()` 均为终态；异常和停止时调用者应
在 finally 中 close，未确认的样本不会自动写入。搜索、额外步行和不完整输入污染本次
动作样本，可信度只降不升。profile 的轴约束同样约束 replay 与记录。

`deny_learning()` 为嵌套、执行上下文局部的禁止作用域。内层稳定入口不能解除父级禁止；
退出或停止会恢复权限，但此前 pending 会话永久失效。原 motion/yaw 缓存也在读写入口
执行这一保护。战斗主入口、触发任务确认进入战斗后的逻辑、战后寻路和战后 retry 覆盖内部重新传送与滑索，既不读取
历史动作，也不 replay 或写样本；手动传入的 initial_move 同样无效。实时 ellipse/normal
与实时 yaw 校准仍可运行。`battle`/`post_battle*` 流程身份自身也拒绝创建学习会话。
未检测到战斗的普通触发轮询不改变学习 epoch，不会让正常导航会话无故失效。

本阶段尚未把新会话接入送货等稳定入口；不将基础设施测试视为实际流程学习已经启用。

## 路径前缀共享与迁移

`RouteLearning.lookup(context)` 先查完整 private，再查同 family/prefix/profile/yaw 的共享池。
没有目标框、ROI 或检测结果也能返回历史动作；high/medium 的可靠性规则与流程内核相同。
共享池从各来源路线的独立统计临时计算，不把聚合结果再写回来源。重复查询不会重复计数。
只有共同前缀可以共享，分叉后即使距离再次相同也不重合。family 是大区、小区、物理起点、
行进方向、路线类型；捕获尺寸、相机策略、运动版本、轴和起始朝向还须精确匹配。

明显冲突的新来源留下与该共享身份绑定的拒用标记，退出共享并继续收集 private；即使它的
private 暂不足三个样本，也不能重新套用被拒绝的共享模型。private 达到资格后可独立复用。
已有来源互相冲突时不聚合成零位移。未知方差的旧估计与新验证统计分开；有干净新来源时，
旧估计不参与强动作统计。阈值为保守初始策略，尚未经过游戏现场定标。

`nearest_yaw()` 只给出候选朝向。调用者必须实际转向、读取真实朝向，再用实际 bucket
调用 `lookup()`；该方法不会自行移动或把请求的朝向当作校准成功。拒用共享的路线不能借
另一个朝向绕过隔离。共享池每次查询按更新时间最多使用 32 个来源；写入时默认保留 128 条
完整配置路线，连同其各段/朝向记录一起淘汰，整体仍受 store 的 4096 条限额约束。

`src/data/navigation_contexts.py` 根据 canonical 送货目标反查小区域，但要求调用者明确提供
已经验证的入口锚点。配送出站架与传送后去接单的架子分开，未知入口、淤积点和歧义名称
维持 private。配置键相同或距离相同都不能证明物理入口稳定。

`migrate_zip_line_cache(path, store)` 原子导入 v4 快照，原文件保留；导入标记与全部来源在
同一次 replace 中保存，随后从磁盘复读核验。失败不报告完成，重试不重复计数，已有新样本
不被旧数据覆盖。无法容纳完整导入时不落盘。合法源数据中的坏条目单独跳过。
旧样本次数只用于加权旧均值估计：v4 超过 20 次后为 EMA，不能视为原始样本的精确总体均值。
旧方差标为 unknown；新成功样本另起统计，并保留 legacy 数量。v3 校准后的 suffix 单独
使用 `legacy-v3-suffix` 运动版本，不与 v4 完整动作混合。

旧文件没有滚轮/相机 profile，默认导入 `legacy-unknown`，保持 private。只有调用者提供
可靠的 profile resolver，才允许 v4 进入已知 profile；family resolver 还须另外确认物理
起点。迁移不能改变捕获尺寸。当前没有自动启动迁移，也没有替换生产滑索缓存调用；下一阶段
将与 Initial Align 的新帧自验证接线一起启用，避免继续沿用旧的到达后记账。
