# 莱万汀终结：暂停的是 buff 时钟（2026-10-20）

本批补研究世界的 `PauseBuffTime` 执行，尚未把完整莱万汀终结加入线上报价。PR #489及其固定来源不变。测试使用明确给定的实例和事件，不增加画面检测，不假设真实终结已经成功产生全部状态。

## 角色与原生证据

`buff_chr_0016_laevat_show_weapon` 监听9=`OnAddedBuff`及203=`OnFinishedBuff`，检查事件中的 `buff_chr_0016_laevat_pause_ult`，随后执行 `PauseBuffTime(true/false)`。这是自身终结状态的时钟，不是全队时停，也不延长武器增伤。

等级12终结输入 `duration=15`；当前原始BuffData读取BB键duration，字面备用值却是16，不能当作“终结16秒”。该BB的真实传入与完整生产链继续核准。暂停标记为Infinity/Unique7，终结容器为stackingType5；没有把5号策略当2号Stack或4号Refresh放行。

构建：GameAssembly SHA256 `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c`；metadata SHA256 `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca`。

| 方法 | RVA | 校验窗口 | SHA256 |
| --- | --- | ---: | --- |
| PauseBuffTime.ExecuteInternal 62135 | 0x8ea09fc | 260 | ae5c96235677fee35b6dad65df966fdc2381705e6cc26fe07305cdf0d30b98dd |
| Buff.SetPaused 63849 | 0x8ed6b38 | 56 | 23284f0355c78e5b14a34aee3bbe1f63dc1f427a29121012f98d64fe8a5754e0 |
| Buff.OnTick 63807 | 0x535da20 | 1904 | 150625bc285c94cf91e2b0d77cd7e4b3d341c7190c0f4713cb24a283287df237 |

默认静态路径读取动作Data的isPaused+0x20，写自身Buff的m_isPaused+0xfa。OnTick在暂停时将所选delta置零，再用于寿命、passedTime及周期处理；不能只延后expiry却继续发周期事件。m_lifeTime+0xa0及m_triggerTimer+0x30分别与字段表核准。窗口是相邻已知方法指针边界，不宣称严格函数长度或实际IFix路径闭合。

旧快照Pause节点union tag271，新捕获为290；类型全名和全部Data字段相等，序列化tag不相等。没有将新tag/RVA套进旧解码器。15个机器码窗口、4字段、8指令锚点已独立复核；原始材料只在主仓ignored `tmp/condition-research-20261013/other-character-mechanics-20261020`。

## 模型已经执行的部分

- 编译器保留布尔开关，执行必须持有同UID的Buff根；普通Skill没有这个上下文，不按角色名找一份同名状态来暂停。
- 按显式世界时间保存剩余寿命和下一次周期触发的余量，撤销旧寿命/周期定时项，恢复后重新安排；同刻反复开关或重复通知不重复tick。
- 还剩2.5秒的实例暂停到时间10，恢复后在12.5结束；同角色武器及另一角色的buff照原期限结束。周期例在1.25暂停、时间20恢复，下一次在20.75，而非补发暂停期间的tick或重置完整周期。
- Infinity不产生假截止。显式移除、父根/动作结束仍可清理；预测分支恢复不改变原世界。已支持的普通Refresh若在暂停期间发生，更新保存的有限余量，不误当Infinity，不更换旧实例。

周期初始化或触发已到期时先保留未知：delta为零不证明已就绪的原生timer绝不会触发。优先级容器、子对象及延迟回调时钟传播不猜测。原生每tick单精度累积与显式截止也不同，执行后仍登记 `Native paused-buff tick accumulation not yet bound`。

## 尚未闭合

完整终结仍需5号叠加策略、Advanced事件ID检查、自动发布9/203、等级BB传递、技能替换和子对象时钟。专项检查真实完整回调仍报告未绑定条件，不用抽出的Pause叶节点替代整条生产链。

普通战技起始4层熔火、280%基础与条件770%，终结战技330%/370%/900%仍按此前分支审计。强化普攻四段的生成、投射物、替换及实际机会数继续核验，不把互斥分支全加。

11项新增专项覆盖上述模型时钟与边界。本批不提高完整2/111，不改变32面板、128报价或14模型释放/2原生攻击绑定。

最终26项Buff生命周期专项、72项相关生命周期回归通过；完整全仓1749项（159.214秒）通过，164测试文件/16职责组登记通过。未新增测试文件，Ruff I/F、修改源码AST与diff-check通过。固定面板/报价文件没有改动，也未做游戏现场验证。
