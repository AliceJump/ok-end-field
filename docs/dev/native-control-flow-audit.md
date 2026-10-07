# 原生控制流节点核对（2026-10-07）

任务6逐类核对。证据来自SkillTimingStore和native_record加载的SHA256校验快照，不使用节点名称推断引擎行为。分类(c)表示仍缺执行语义，严格模拟拒绝计价；不表示已实现。静止单敌且在命中范围内，也不能证明所有目标、控制流和数值条件恒成立。

## InterruptAction+Data：保留未解析（c）

佩丽卡战技chr_0004_pelica_normal_skill中，节点同时有attacker/defender选择器、immobilizedTime=1、overrideSuperArmorLimit=-1，defender绑定tar目标组。它不是单独改变角色自己的硬直窗口。快照中的目标控制字段可能影响敌方动作及后续命中；现有SkillTiming的duration/handoff/actionable仅给角色调度边界，未绑定该目标组或控制有效性，不能据此宣称时序已覆盖。

缺口：解释目标选择器、受控对象的中断/免疫以及控制持续时间；确认对后续事件和命中条件的影响。全快照检索199次是节点出现次数，并非199个程序。回归直接取校验快照中的节点，核对字段并证明严格模拟保留unresolved、拒绝动作且不改变资源/状态。

## JumpToAction+Data：保留未解析（c）

跳转直接指定destFrame=4，conditionAction也是独立动作序列。SkillTiming只保存角色时间边界，未保存程序计数器、跳转条件和重复执行次数；扁平执行可能重复或遗漏后续事件，固定handoff不能替代跳转语义。缺口：条件评估、帧级跳转与终止规则、事件重入及次数限制。校验记录chr_0024_deepfin_combo_skill，全快照检索67次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。
