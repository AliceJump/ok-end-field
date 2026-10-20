# 莱万汀终结：暂停的是 buff 时钟（2026-10-20）

本批及续批补研究世界的 `PauseBuffTime`、事件身份判断与限定5号容器执行，尚未把完整莱万汀终结加入线上报价。PR #489及其固定来源不变。测试使用明确给定的实例和事件，不增加画面检测，不假设真实终结已经成功产生全部状态。

## 角色与原生证据

`buff_chr_0016_laevat_show_weapon` 监听9=`OnAddedBuff`及203=`OnFinishedBuff`，检查事件中的 `buff_chr_0016_laevat_pause_ult`，随后执行 `PauseBuffTime(true/false)`。这是自身终结状态的时钟，不是全队时停，也不延长武器增伤。

等级12终结输入 `duration=15`；当前原始BuffData读取BB键duration，字面备用值却是16，不能当作“终结16秒”。该BB的真实传入与完整生产链继续核准。暂停标记为Infinity/Unique7，终结容器为stackingType5；续批仅对核准的此容器绑定“延长剩余寿命”，没有当2号Stack或4号Refresh。

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

首批尚缺的Advanced字面ID判断及5号容器在下述续批已绑定；自动发布9/203、等级BB传递、技能替换和子对象时钟仍缺。首批只抽取Pause叶节点的验收已由真实完整条件回调回归替代，仍不等于完整姿态生产链。

普通战技起始4层熔火、280%基础与条件770%，终结战技330%/370%/900%仍按此前分支审计。强化普攻四段的生成、投射物、替换及实际机会数继续核验，不把互斥分支全加。

11项新增专项覆盖上述模型时钟与边界。本批不提高完整2/111，不改变32面板、128报价或14模型释放/2原生攻击绑定。

最终26项Buff生命周期专项、72项相关生命周期回归通过；完整全仓1749项（159.214秒）通过，164测试文件/16职责组登记通过。未新增测试文件，Ruff I/F、修改源码AST与diff-check通过。固定面板/报价文件没有改动，也未做游戏现场验证。

## 续批：真实事件ID与5号容器

`CheckBuffIdInContextAdvanced`字面分支先取事件BuffContext的ID，然后依次解析StringBlackboardRef并比较；任意匹配为true。该分支在tag配置读取之前结束，因此不要求BuffData已载入，也不查目标当前还持有多少层。空列表或没有BuffContext为false。动态引用、非空blackboardKey的匹配字符串输出继续未绑定。

`dispatch_native(..., buff_event_id=...)`明确提供这次事件的身份；CharacterData回调及原有已审核marker producer同样传递。每次条件求值从该上下文重新生成，数值payload、旧私有BB、当前持有者和已消失的状态都不能伪造身份。嵌套新事件默认没有外层身份，finally恢复外层；异常也恢复。真实9/203的条件加Pause完整序列已可编译、执行，不再只测抽出的Pause叶节点。

当前 `Conditions.CheckBuffIdInContextAdvanced.ExecuteInternal`96629/RVA0x9371a9c，3500字节选定窗口SHA `4f92a8f3e35e341cce23d42023af6e1ab967f58eff879d6cf194330977a59e9c`。直接比较调用已映射为System.String.op_Inequality，匹配时退出循环；tag配置读取属于checkType1另一路。选定窗口含函数外区域，不称严格函数长度。

5号分支由当前StackBuff分派表导向0x75882be，先取第一份未结束实例，再分配临时入参并调用 `_SetBuffDurationExtend`63867/RVA0x8ef28f4：单精度 `old.remaining + incoming.duration`，任一Infinity则保持Infinity。224字节选定窗口SHA `4396de45201547374887b780adfc7ecb07c743338badcb201c65923e9c646f14`。返回旧out而没有新创建handle；因此不重新OnStart、不更换旧BB/source/父根、不重置周期timer或把旧对象挂到新Skill。

运行时只对审核的show_weapon、无共享/优先级/添加冷却等未知策略、单次count1启用。显式BB输入的期间能延长；没有证据的隐式duration传递仍诊断未知，避免直接创建16秒假姿态。暂停期间延长的是保存余量；动作绑定截止仍保留原实例的截止，不随新调用重挂。

例如原本时间3结束，时间2再加2秒后在5结束，而不是4或3；重复开终结本身仍包含显式清旧状态，不能据容器算术推断“每开大都追加15秒”。

连携另有重要来源：0～57帧的ChannelingAction31，非逐帧、间隔约0.1秒、次数不限、目标本人；其tick显式给show_weapon及ult_end各加约0.1秒。它与强化战技0～115帧的动作绑定pause_ult不同。重复channel、Jump/Interrupt及真实delta尚未执行核准，不能把57帧直接宣称为稳定墙钟补时，亦不能只执行一次0.1秒就算完整连携。

续批独立校验7机器窗口、12锚点、11角色记录。新增4项字面事件测试、9项容器及真实producer边界测试；53项相关通过，完整全仓1762项（172.737秒）通过。32/128数据流审计与已保存JSON逐字节一致，程序树复测仍2/111。没有启用完整姿态、隐式BB或新实战识别。
