# 骏卫士气实例的保留与生效选择（2026-10-15）

本批只补研究执行器的实例容器。已确认的创建输入可进入真实`CreateBuffAction`→独立实例→到期/父来源清理链；超过上限的实例停用并保留，而不是套用普通Stack2删除旧层。没有接通80技力触发、团队终结后续的真实发布或士气双属性结算，也不改变PR #489的释放层。

## 来源与选用参数

`buff_chr_0029_pograni_talent1`原始442字节记录SHA256为`bbb46352b2e17df9998116a5e84dd12fd1b4bf0432fd762b7ce487f38580aec0`。定义为普通Buff `HighPriorityWithMaxStack=11`，不能与GlobalBuff自己的Stack2枚举混用。没有生命周期动作、订阅、周期动作、标签或非零/动态优先级；最大层数读`max_stack`、时长读`duration`。

六星P0、满级天赋实际选中`chr_0029_pograni_talent_1_2`：80技力门槛、20秒、攻击`0.07999999821186066`、法强8、本人/队友最大层数参数均3。第二天赋`chr_0029_pograni_talent_2_2`提供10秒期间。原始SkillData的100技力、10%/10、本人5层默认BB不替代选用等级/潜能；测试显式供给选用参数，不制造当前三层状态。

原始创建节点`asChildBuff=true`、`autoFinishByAction=false`，继承时长、攻击、法强和本人最大层数；实例必须保留其真实父根。`inheritSourceSkillCastInfo=true`的完整cast上下文仍未执行；当前这份无回调的容器不借它推断技能伤害或消费。

## 原生核准

同版DLL/metadata仍匹配现有快照的`c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89`和`0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e`。方法窗口边界取下一已知方法指针；选定冷块/泛型窗口不冒充严格完整函数或IFix补丁执行。

| 方法/分支 | 已核行为 | 窗口SHA256 |
| --- | --- | --- |
| StackBuff 60757/RVA373af10，case11冷块4e239a6/546字节 | 分配、插入有序名单；最多max个未结束实例开启，其余关闭；不MarkFinish溢出项 | `d1af0070ff371a34f1f85cee96dab07ebf63044c171a0e1b9a4a6530d91125d6`（冷块） |
| Buff.CompareTo 60686/RVA352a0f0/336字节 | priority降序、剩余life降序、近似相同后uint instanceUid升序 | `2a7407a0f7c290a227df87e331a18c595dd0f09542e140f8657b6f3d6e61395f` |
| RemoveBuff 60759/RVA4094ba0/96字节 | 从容器移除后调用Refresh | `beb1fbcab9d28d56a70ba39b1a0aca73ee3c6fd6ef2620450130e4eaf72673b1` |
| Refresh case11/RVA4e23482/466字节冷窗口 | 按已有顺序重新选择未结束实例，不重新排序或刷新幸存者时长 | `4d8b6d08ab55f645e1072ac3313817955b3fde4609e1a168d01f1de4cef8ea0f` |
| Buff.OnTick 60704/RVA3106030/4240字节 | 停用跳过开启期间的触发动作，但仍进入生命时间SUBSS/到期检查 | `2fad6b8436e059e8ff541c78a4e7c9b481f25b6c88cb37e6668f1b867e635440` |
| GetBuffCountById 60802/RVA3b4f810/1248字节 | 排除finished并合计enhanceCnt，不以enabled筛选 | `fc88ddf4e4a2617ac27ceb57f21d887521169bd7aa4e8e1f84736e09ad9a0404` |

MetadataRegistration.fieldOffsets核准`Buff+a0=m_lifeTime`、`+ac=m_priority`、`+fc=instanceUid`，`+b0=m_isEnabled`、`+b1=m_isFinished`、`+a8=m_enhanceCnt`。CompareTo常量epsilon是单精度`9.999999747378752e-6`；只有超过epsilon才比较浮点大小。已读插入窗口`3eed590`：新对象依次比较、负比较结果处插入，否则追加。768字节窗口SHA`f8816459260eb497650c128af582aceade14802118638b4e4c925282bf563ddc`包含邻接方法开头；只采用已核的插入主路径。

因此，四份未结束实例可以只三份生效；层数快照仍可读4，不能把它直接乘攻击8%。第4份短时实例可等待前面的实例提前结束后重新生效，也可能在停用期间自己到期。

## 执行边界

`NativeBuffProgram.retained_priority_layers`仅由上述ID和核准的无动作、非周期、零优先级结构启用。未知优先级、周期/回调、动作继承和未核准最大层数变化仍拒绝；父根清理沿用现有独立实例身份，其他父根的幸存实例可重新开启。固定优先级相同的实例采用float32剩余时间差和epsilon插入；移除后保留原顺序。

`enabled`只控制实际加成/订阅消费者；`native_buff_count`继续计未结束实例。当前士气属性2/formula6与属性87/formula5仍保留完整`attributeModifier`未知，未只接攻击并丢掉法强。原生每tick的delta流、float32累计减时和epsilon到期边界没有完整输入，本模型仍使用既有战斗世界的名义到期队列，并明确留下`Native retained-layer tick accumulation not yet bound`诊断；不声称与现场逐帧精确等价。

## 逐角色、逐技能台账

| 骏卫单位 | 本批能执行的部分 | 仍缺 |
| --- | --- | --- |
| 普攻 | 已确认创建输入对应的士气容器可供后续读取 | 不从普通攻击推定80技力累计、满层或实际收益 |
| 战技 | 原始CreateBuff节点能保留独立父根、确认后的20秒输入及层上限 | 实际回复80技力的触发发布/返还时点；完整战技语义 |
| 连携 | 共享容器的保留/到期/清理适用于已确认来源实例 | 连携实际producer和消费；沿用master执行，无新识别 |
| 终结 | 已确认10秒/20秒实例能混合保留和选择 | 终结后续的实际团队接收、事件时序和双属性结算 |
| 满级天赋/P0 | 选用参数与原始默认值分开；保留两个属性输入 | 不自动发布80技力达标、队友终结后续或当前层数 |

独立脚本、完整机器码、字段/方法JSON和日志只在主仓库忽略目录`tmp/condition-research-20261013`。这里只提交可审核合同和产品代码/测试，不提交原始研究产物。

## 验证

新增7项测试，包含原始创建节点、父来源清理、20/10秒混合、保留层计数、生效上限、epsilon近似相同、预测隔离、未知最大层数/优先级保留，以及停用实例不污染既有属性消费者。61项相关测试通过；全仓1478项通过（137.373秒），末尾结构门槛/计时诊断校准后专项再次核验。32角色/128技能数据流审计与保存JSON完全一致，14模型释放/2原生ATK绑定和完整原生程序2/111均不提高。历史CI职责未登记项仍保留，本批未新增测试文件。没有游戏现场验证。
