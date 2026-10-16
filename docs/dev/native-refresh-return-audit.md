# 新客户端 Refresh 返回值与创建回调边界

2026-10-16。本批核查当前客户端的默认静态实现，关闭 Refresh4 返回值向 CreateBuff 调用者传播的一个证据缺口；没有启用新的原生 producer、伊冯50%增伤或额外识别。旧运行快照继续使用原有版本合同，不直接导入新客户端字段、机器码地址或数值。

## 来源与版本

| 输入 | SHA256 |
| --- | --- |
| GameAssembly.dll | `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c` |
| global-metadata.dat | `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca` |

主仓库忽略目录中的 `probe_current_refresh_return.py` 从这对文件重新定位方法和字段；`verify_current_refresh_return.py` 用独立 PE 节映射核对全部保存窗口、分派表、常量和关键指令。原始资源、脚本、日志与机器码只保存在主仓库 `tmp/condition-research-20261013/native-build-ab00f1c4/refresh-return`，不进入本分支。

新 `BuffContext` 的 `buff+0x38`、`buffId+0x48`、`layer+0x50`、`skillCastInfo+0x58`，以及 `AddBuffContext.oriBuffInst+0x70` 已由当前 metadata 核名。不能按旧构建的 `skillCastInfo+0x50` 或 `oriBuffInst+0x68` 解释新对象。

## Refresh4 有实例路径

当前 `BuffStackingGroup.StackBuff` 为63861/RVA`589e3d0`。13项分派表位于`589eb04`，SHA`a9fa030d4a611355a6af4387ae72e72588967927a91ca022962ed754a656c26e`；索引4实际进入`589eace`，不是借用旧 RVA。

1. `_GetFirstUnFinishedBuff` 找到现有对象；没有旧对象时转普通创建路径。
2. 存在旧对象时，`758829f` 冷块分配临时参数对象，将它传给旧对象的寿命更新函数。
3. `75882b6` 将 **out 参数写成旧实例**。经`758871b`写屏障返回主块`589eab3`，该处 `xor eax,eax`：**直接返回值为空**。

因此存在三个不同身份：旧持有实例、用于计算刷新参数的临时对象、本次实际创建返回值。不能将空返回解释为没有刷新，也不能把 out 旧对象解释成新创建、重新启用或换了来源的对象。

寿命更新函数的当前选定窗口为RVA`6b2c330`，208字节SHA`81a0de430e79fb2a69265f0b6c01830b9ecb46542bb16d1d41c22b14f94b975a`。字段核名为 `m_duration+0x20`、`m_lifeType+0x24`、`m_lifeTime+0xa0`。有限寿命时比较临时对象的 duration 与旧 remaining 减 float32 epsilon；epsilon 的原字节`acc52737`为`9.999999747378752e-6`，不换成精确 double 阈值。新 duration 足够大则用新值，否则保留旧 remaining，再调用旧实例的 `_SetLifeTime`。若任一输入为 Infinity，冷块`7776056`把旧实例 lifeType 写为1后返回；不是按普通有限秒数刷新。该 helper 的元数据方法名未唯一绑定，不以旧函数名补全其身份。

这里只核静态寿命路径；模型的 authored clock 不等同于原生每 tick 的单精度 delta 累积。

## 返回到 CreateBuff 调用者

当前 `BuffContainer` 的入口实际名为 **CreateBuff**（63883/RVA`589c4a0`），不是 AddBuff。该方法接住 `StackBuff` 的直接返回，同时把原 out 参数继续向上传递；尾部只把直接返回包装为 handle，没有把 out 旧对象提升成新返回。

`AddBuffFinal`59320、`_AddBuffInternal`59319、`AddBuffByAbilityAction`59317的默认返回路径继续传递这个 handle。包装函数RVA`5b6c4e0`明确对空对象写入空指针及零实例标识。因此，在本次核查的普通 Refresh 路径中，旧对象的寿命已更新，但创建调用者拿不到一个新的有效 handle。

`CreateBuffAction.ExecuteInternal`60973中，`5c0a3c1～5c0a4e7`先验证直接返回 handle，才执行虚槽17的 OnBuffCreated、来源参数合并、asChildBuff 的 SetBuffParent，以及 autoFinishByAction 的本动作句柄登记。Refresh 返回空时跳过这段；后续日志仍可读取 out 旧对象，不能将日志能看到旧对象误当作父根或 Skill 已重新附着。

`CreateBuffAttachingSkill.OnBuffCreated`60987/RVA`8e7b200`的默认路径从事件上下文取`CastSkillContext.skill+0x30`并调用`Skill.AttachBuff`。普通 Refresh 没有新 handle，因此不能仅凭 out 旧对象重新执行这次附着。此结论只覆盖本次直接调用路径；不否定其他明确事件另外创建、移除或重新绑定对象。

## 空返回仍可能产生通知

`BuffContainer.CreateBuff`不以新对象非空作为所有通知的前置条件：接收方事件9在`589d72b`，来源事件102在`589dc8e`，都在其需要订阅的分支内；上下文区分新返回 `buff` 和 out `oriBuffInst`。两事件之后仍会调用 `TryEnhancingKeywordBuff`。

因此，刷新不会重新走本次创建回调，不代表没有 OnAddedBuff/OnOutputBuff 通知或其他订阅影响。不能把 Refresh 支持简化为“延长时间后立即返回”；完整前置事件、重入、关键词增强、通知期间移除/替换及 dispatcher 排序仍需分别执行和验证。事件9/102的完整语义不能仅靠本文件的位置替代已有枚举及发布合同。

## 实现边界与验证

研究执行器现有阿列什 plain Refresh 特例继续保留。通用带回调/父根/动作继承的 Refresh，以及伊冯 valid/valid_up、实际冻结与去重 marker 的 producer，仍未授权为完整运行链。已存在实例的伤害消费者不等于这些生产者完成。

本批25份代码窗口、6组字段、1份常量、18处关键指令锚点及两份研究脚本 AST 核验通过；完整原生程序覆盖仍2/111。只有证据文档变化，不重复运行无关全仓，也不宣称当前客户端实战适用性或生命周期已完整接管。
