# 原生事件Skill上下文与buff附着（2026-10-15）

2026-10-16续核：新客户端的Refresh4直接返回与out旧实例、BuffContainer.CreateBuff返回包装、CreateBuff创建回调/父根/Skill附着门槛已补静态证据，见[native-refresh-return-audit.md](native-refresh-return-audit.md)。空创建返回不代表没有接收/来源通知；完整重入及producer仍未执行。下文机器码与运行合同仍属于旧快照，不替换成新字段/RVA。

本批接入研究执行器的`CreateBuffAttachingSkill`和字面`CheckSkillId`子集。调用者明确提供已建立的cast身份后，创建的实际buff UID才能挂到事件中的Skill对象。本人、buff持有者、监听器所属技能以及当前主控都不能替代该对象。没有新增识别、自动OnBeforeCastSkill发布或技力时点处理；PR #489的释放编排层未改。

## 原生合同

`CreateBuffAttachingSkill.OnBuffCreated`58076/RVA4077d50/144字节窗口SHA`e696744e478cee4a7668398f88b4d23c70499d6781486285cd6ab889a65904ce`及冷块RVA4f6e246/54字节SHA`993864f762b817311cd4b29a0f85bc9c5661b0a079bfda014e6568de32dd77b3`，从事件上下文取`CastSkillContext+30.skill`，传本次返回的buff handle给`Skill.AttachBuff`。字段由MetadataRegistration.fieldOffsets核名，不借源角色/技能字符串猜对象。

`CheckSkillId.ExecuteInternal`91622/RVA64aa25c/760字节SHA`87bca9f1e465e2db019d20d8a10854b23593ff2bac5f58af4e304127e65f40cb`优先读CastSkillContext.skill.skillId；按列表逐个GetValue/String.Equals，任一匹配即true。没有该事件上下文时还有环境Skill回退路径，本批未绑定这一路，保持缺输入；动态字符串BB也未绑定。字面多ID是OR，不推定多个技能同时释放。

窗口均使用已知方法指针边界或选定冷块，不冒充严格函数长度、IFix替换路径或现场执行。实际DLL/metadata与原先固定构建哈希一致。完整原始输出只在主仓库忽略目录`tmp/condition-research-20261013`；入口`probe_yvonne_methods.py`、`probe_action_order.py`和`action-order-code-proof.json`，不提交原始机器码/独立脚本。

## 执行链

`CombatWorldState.dispatch_native(..., cast_action=已开始cast的action_id)`验证该scope对应已登记且尚未被同Skill新cast取代、未确认结束的真实模型Skill。传未知、旧cast或已结束scope时，保留诊断并拒绝这次回调。它不自动认为所有start都是原生事件的正确发布阶段。

回调期间临时绑定`_native_cast_context`，退出或异常时恢复；嵌套事件必须独立提供自己的上下文，否则清空，不借外层cast。该上下文进入预测状态签名并沿用fork隔离。

字面CheckSkillId读视图仅由当前有效上下文重算。复用的buff BB每次先清掉旧值，传入的数值payload/默认值不能伪造技能身份。上下文不存在时保持未知；有效上下文对应其他ID时为false。回调内普通buff创建仍走现有UID、父根、独立时限、Unique等合同；只有有效新返回实例才附着，Unique重加不把旧对象挂到另一个cast。原生Refresh4的返回、重设父根和重新附着尚未批准，本批拒绝该策略。

Skill附着与动作/父根清理并行存在。动作OnEnd、天然期限、父根结束先移除实例时，Skill名单里的旧UID不会误伤后来同名实例；确认CastEnd再清名单。handoff/换主控不会自行补CastEnd。无上下文的创建保留`Native skill-attached buff lacks CastSkillContext`，不能把单次缓存buff无条件当作永久技能收益。

## 实际来源范围

当前本地快照中有两处CreateBuffAttachingSkill：

| 来源 | 实际定义 | 本批结果 |
| --- | --- | --- |
| 汤汤`chr_0027_tangtang_ultimate_skill_1` | `buff_chr_0027_tangtang_ultskill_vfx`，Stack2/max1，名义6秒，动作0～123帧且autoFinishByAction=true | 创建节点可编译；明确事件Skill输入时挂准确UID；4.1秒动作清理与确认CastEnd均可结束。buff原始2330字节SHA`46b81fd3199789b47b3aaa609dce57e4f20f93113053a50aa6788c42aa452b80`。该buff是表现子项，不由此添加终结伤害、场地或完整程序能力 |
| 伊冯`buff_chr_0017_yvonne_talent_1_valid` | 检查attack5后创建valid_up，Refresh4/Attacker NormalCalcZone | 字面技能ID门槛已读真实模型cast；Refresh4和攻击方modifier继续明确未知，不发放50%收益、不新增flat计数替代实例 |

汤汤专项只抽取真实创建节点，外层回调/跨角色对象用于验证事件上下文与持有者分离的通用合同，不冒充汤汤实际终结监听了其他角色技能。伊冯仍缺真实冻结事件/去重marker、15秒许可Refresh与ComboController、valid_up重新附着及攻击方结算；没有把部分消费者接通描述成天赋已能自动生产。

## 同帧时间轴核查

TimelineActionProcessor.Load55994/RVA3733830/480字节SHA`f80a9e0e740cfd2c3a7099d5f083c2ffb2398a5d3b229bfaeb76215f4c393259`依输入枚举ToAction并追加内部列表。`_TickInternal`56000/RVA3103db0/2368字节SHA`d75b74b3416396ff5c9114d7b4a19edd336759d77f857a6b455d26d511eb0824`按保存索引遍历窗口，Sequence内按动作列表依次执行；Sequence.Init57351/RVA30c7a10/624字节SHA`e5886e026f5ab1fc0cf472180191b9da945cbc88f66b89ac527cadf145e7a9b4`按数据数组创建启用动作并追加，不以serverActionIndex重排。

伊冯attack5的已解包列表第6项是21～23帧伤害窗口（动作index9/13），第8项是21～24帧清valid（index20）。当前模型保持同时间戳的原始序列顺序，编译结果先已有可解析伤害、后清许可；新增回归锁定这两项的`.7s`顺序。不把两个原生DamageAction当成两个保证命中，也不把ActionContainer的事件优先级规则套到所有timeline。同帧Interrupt/Jump、输入加载前处理、实际推进delta/ForceSync等完整路径未闭合，不声称所有现场排序已模拟。

## 验收边界

本轮只关闭“已给定有效cast上下文→字面ID门槛→受支持创建→确切Skill附着/清理”这一段。所有OnBeforeCastSkill等真实发布阶段、尤其技力扣除/返还关联，仍按用户约定不扩大。固定面板、128报价、14模型释放/2原生ATK及完整程序2/111不因本批提高；尚未做现场验证。测试与保存结果记录在数据流进度首段。

新增11项测试，23项该文件专项（10.440秒）、80项生命周期相关（24.005秒）、全仓1489项（153.329秒）通过。AST5份、Ruff I/F与diff-check通过；重生成damage-data-flow审计与保存JSON完全一致，完整程序复测仍2/111。本批在既有TestSkillAttachedBuffLifetime.py增加用例，未新增测试文件；此前研究分支未登记的CI职责缺口仍保留，不宣称CI分组全通过。

## 后续Refresh返回值核查

普通Buff.StackBuff的Refresh4已有实例分支RVA4e23764/38字节SHA`5e16a850c714d4165485e110d4aac611880d26ce7c6c96f526a943d9e25d3f03`：分配一份临时参数对象给旧实例刷新，写out参数为旧实例，直接返回值为0。不得把这里的0等同“没有刷新”，也不能将out旧实例当成本次新建对象。240字节RVA44406b0窗口SHA`dc5333b01fef35b88be085a252cee796ee616e942470ba3814dbdd20a0fe31ac`核到新duration与旧life的单精度epsilon比较，调用_SetLifeTime；其中后半对齐不是另一条业务行为。

SetBuffParent60747/RVA3dfa770/224字节SHA`792c23a24c60b2cbb6d08e151516ad3ddca88cf2e29cf9d541b90ff15d277554`会把handle登记到目标根并写自身parent指针；这不能证明Refresh分支实际会调用它。CreateBuff.ExecuteInternal58062/RVA373e6a0/6720字节SHA`37faf559c1087fe46ced9648eee6ac12b40cf1b9b0e11ff977ee3f58d7522563`的最终可见父根处理受到AddBuffByAbilityAction返回handle有效性门槛控制；实际返回handle与新/旧out参数的选择、OnBuffCreated虚分派完整桥接仍需继续核准，因而本批继续拒绝技能附着Refresh4。不以函数名称或返回0猜清理链。
