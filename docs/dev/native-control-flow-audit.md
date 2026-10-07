# 原生控制流节点核对（2026-10-07）

任务6逐类核对。证据来自SkillTimingStore和native_record加载的SHA256校验快照，不使用节点名称推断引擎行为。分类(c)表示仍缺执行语义，严格模拟拒绝计价；不表示已实现。静止单敌且在命中范围内，也不能证明所有目标、控制流和数值条件恒成立。

## InterruptAction+Data：保留未解析（c）

佩丽卡战技chr_0004_pelica_normal_skill中，节点同时有attacker/defender选择器、immobilizedTime=1、overrideSuperArmorLimit=-1，defender绑定tar目标组。它不是单独改变角色自己的硬直窗口。快照中的目标控制字段可能影响敌方动作及后续命中；现有SkillTiming的duration/handoff/actionable仅给角色调度边界，未绑定该目标组或控制有效性，不能据此宣称时序已覆盖。

缺口：解释目标选择器、受控对象的中断/免疫以及控制持续时间；确认对后续事件和命中条件的影响。全快照检索199次是节点出现次数，并非199个程序。回归直接取校验快照中的节点，核对字段并证明严格模拟保留unresolved、拒绝动作且不改变资源/状态。
