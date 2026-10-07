# 原生控制流节点核对（2026-10-07）

任务6逐类核对。证据来自SkillTimingStore和native_record加载的SHA256校验快照，不使用节点名称推断引擎行为。分类(c)表示仍缺执行语义，严格模拟拒绝计价；不表示已实现。静止单敌且在命中范围内，也不能证明所有目标、控制流和数值条件恒成立。

## InterruptAction+Data：保留未解析（c）

佩丽卡战技chr_0004_pelica_normal_skill中，节点同时有attacker/defender选择器、immobilizedTime=1、overrideSuperArmorLimit=-1，defender绑定tar目标组。它不是单独改变角色自己的硬直窗口。快照中的目标控制字段可能影响敌方动作及后续命中；现有SkillTiming的duration/handoff/actionable仅给角色调度边界，未绑定该目标组或控制有效性，不能据此宣称时序已覆盖。

缺口：解释目标选择器、受控对象的中断/免疫以及控制持续时间；确认对后续事件和命中条件的影响。本轮根技能及补充记录检索199次是节点出现次数，并非199个程序。回归直接取校验快照中的节点，核对字段并证明严格模拟保留unresolved、拒绝动作且不改变资源/状态。

## JumpToAction+Data：保留未解析（c）

跳转直接指定destFrame=4，conditionAction也是独立动作序列。SkillTiming只保存角色时间边界，未保存程序计数器、跳转条件和重复执行次数；扁平执行可能重复或遗漏后续事件，固定handoff不能替代跳转语义。缺口：条件评估、帧级跳转与终止规则、事件重入及次数限制。校验记录chr_0024_deepfin_combo_skill，本轮根技能及补充记录检索67次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。

## Curve结构核验续作：可解码不等于可求值

本段补充前述CurveEvaluateFloat缺口，不改变其未解析分类。MemoryPack.AnimationCurveFormatter.DeserializeKeyFrame逐个读取28字节，按UnityEngine.Keyframe字段保存time/value/inTangent/outTangent/weightedMode/inWeight/outWeight；它没有Beyond.FKeyframe的tangentMode字段，不能套用研究包中32字节的FAnimationCurve合同。Deserialize读member=3、先preWrapMode再postWrapMode、带符号key count及连续关键帧；-1数组、空数组与FF空曲线独立。Lazy icall字符串分别确认set_preWrapMode、set_postWrapMode、SetKeys，与字段保存顺序一致。

新增native_curve_data数据解析器和audit_native_curve_data.py只读审计。时序与补充快照仍由各自哈希校验加载；解析后的曲线再编码必须逐字节一致。225/225个Curve节点全部通过，53份不同payload；811个关键帧中weightedMode=0有809个、=1有2个，两个wrap字段共450次均为8，无穷切线出现96次。管理员样本的五个点time为0/4/6/8/50，包含无穷切线；同节点的curveTemplate=Linear不能覆盖useCustomCurve=true时的实际曲线。顺序、模式、正负零和无穷切线均原样保留，不猜测排序/插值或将异常数值补零。

ExecuteInternal从实际Ability BB读取inputValue，再调用Data.GetCurve和Unity.AnimationCurve.Evaluate；结果与输出键旧float值比较，只有差值超过原生阈值才AssignDynamic。GetCurve的useCustomCurve分支直接返回customCurve，与模板查找独立。因此仅补Hermite数学式仍不足：需核验Unity运行时的加权/无穷切线/边界求值、输入float口径、旧输出读取/更新阈值，以及全部BB消费者。当前解析器无Evaluate方法，不接入数值表达式，编译器继续报告CurveEvaluateFloat未解析，严格模拟仍拒绝未知程序。

| 核验读取窗口 | RVA | 长度 | SHA256 |
| --- | --- | --- | --- |
| AnimationCurveFormatter.DeserializeKeyFrame | 0x34306a0 | 1500 | a1c39cc54f4f4cf571ae9c1550d67c2c45854711805db5b5dfd09f094355c648 |
| AnimationCurveFormatter.Deserialize | 0x34301d0 | 1600 | 461ea7c502dd2d6c0981596a6bdd1aa9001d21d964cc199f47ec774bfa98f1f6 |
| CurveEvaluateFloat.ExecuteInternal | 0x3ae5fb0 | 1700 | f540ac5fe3126bcdee755f6e4523f438f963a766ceaeed1f12fe68976383ec7d |
| CurveEvaluateFloat.Data.GetCurve | 0x3ae7660 | 500 | 5974c214ebb5cc8857ad75689d4fb67616798a201ef046d0f842bff11fce0391 |
| GetCurve自定义返回分支 | 0x3ae7abb | 20 | 9f3304d9a53e446d63003abfdafefeafdcc0816f5163fb680e805d30ff440ae2 |
| Unity.AnimationCurve.Evaluate managed入口 | 0x2f87100 | 180 | 930f436e6c0a48dab50482b352da3fb80adabb496685db4b44f8ef0d6a0abe5e |

这些是固定版本的读取窗口，非完整方法长度；Evaluate入口继续跳到Unity icall，不能把该180字节当求值算法。新增9项解析/审计专项通过（1.465秒），覆盖真实管理员曲线、null/空、截断及错布局拒绝、模式与无穷切线、精确往返和结构审计不冒充执行。完整程序树仍2/111，数据报告在忽略文件tmp/native_curve_data_audit.json；全仓结果见流程进度。

## ComboCacheAction+Data：保留未解析（c）

mappingDataList指定cmdType=3、skillId=chr_0019_karin_normal_skill、cacheTime约0.3秒、cacheEndByAction=true。这些字段描述特定输入映射及动作绑定缓存，而非单个动画时长。现有can_start/SkillTiming未建模输入排队、缓存终止和映射动作的选择，不能证明缓存忽略后下一招合法性相同。缺口：命令枚举、缓存窗口/取消及消费后技能选择。校验记录chr_0019_karin_combo_skill，本轮根技能及补充记录检索262次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。

## TemporaryUnlockAction+Data：保留未解析（c）

节点含blockManualLock=false、compareTarget=false、disableLockAimPriority=30和targetSettings。锁定优先级及目标选择仍未绑定到模拟世界；当前场景不能证明所有动作绑定的smart/main/guard目标相同。SkillTiming未记录锁定恢复事件。缺口：选择器语义、锁定/恢复时机，以及目标变化对后续事件的影响。校验记录chr_0019_karin_combo_skill，本轮根技能及补充记录检索41次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。

## CurveEvaluateFloat+Data：保留未解析（c）

样本把input_angle映射为cam_angle，但useCustomCurve=true，curveTemplate=Linear不等于实际自定义曲线就是线性；快照另有curve_profile原始字节。其他样本还读取owner_mainchar_distance和enemy_turn_distance。摄像机专用数据流可能可忽略，但必须证明全部消费者只属于表现，不能按输出键名或单个样本全局放行。缺口：解析曲线点/插值/边界行为并追踪所有BB消费者；在数值或条件分支中继续严格阻塞。校验记录chr_0019_karin_normal_skill，本轮根技能及补充记录检索188次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。

## CheckDistanceCondition+Data：保留未解析（c）

条件包含source/target选择器、distance=4、lessThan=true、containsHittableObj=false和includeTargetRadius=false。单敌在命中范围内不能推出所有source/target距离都满足4米；源码保留条件分支及取反，未知检查必须阻断后续序列。管理员还将SaveTargetDistance结果送入CompareFloat，不能替换成恒真。缺口：带来源的距离/半径观测或明确场景输入、比较边界、条件分支与命中裁定。校验记录chr_0019_karin_normal_skill，本轮根技能及补充记录检索152次。

回归读取校验快照中的实际节点，严格模拟拒绝计价且原世界snapshot保持不变。出现次数仅为审计样本节点数，不能当作影响程序数。
