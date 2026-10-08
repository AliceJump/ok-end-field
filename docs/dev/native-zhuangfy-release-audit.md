# 庄方宜天地造化的原生生产链（2026-10-08）

本批核准数据来源和逐技能门槛，尚不启用新的增幅执行绑定。完整原生程序仍2/111；32固定面板、128基础报价、14模型释放加成及2实例攻击绑定均不变。已知基础18%不能让未知的追加计数自动变成0，也不能将普通战技cast第0帧替代原始标记帧。

## 选中天赋与来源

默认六星零潜、满级天赋的天地造化是`chr_0030_zhuangfy_talent_1_2`。选中参数为base_rate=0.18000000715255737、enhance_rate=0.019999999552965164、duration=5。`chr_0030_zhuangfy_talent1`原文件三项BB均为0；它们是待等级覆盖的值，不能按0%/0秒计算。

本次从已经完整读取并逐字节回编码核准的5569份SkillData/BuffData中选出8份完整记录，再比对当前安装VFS的文件MD5及原始SHA256。存于`assets/data/character_mechanics/20261008/zhuangfy_release.json.gz`，index保存压缩包摘要、同版原生输入和记录数。研究代码、密钥及客户端二进制仍只在本地忽略目录，不随提交发布。读取器拒绝摘要、构建域、记录数或byte_identical/current-VFS核准标记不符的数据。

## 每个技能的关系

| 单位 | 原生关系 | 已核准数据 / 尚未执行 |
| --- | --- | --- |
| 普通攻击 | 未发现本次基础/追加标记生产块 | 不按普攻次数增加天地造化 |
| 战技惊霆诀 | `chr_0030_zhuangfy_normal_skill`的timeline[16]在6～7帧创建基础标记；姿态替换`normal_skill_ult`的timeline[10]在5～6帧创建 | 两者都先CheckSquadInFight；失败路径再检查smart_target数量compareType=3/minNum=1。两条创建是互斥路径，不能相加或无条件cast0触发 |
| 连携技 | 未发现本次基础/追加标记生产块 | 不由释放连携补造雷击次数 |
| 终结技 | 本次追加记录来自姿态战技的`normal_skill_ult_abilityrange`，不是终结cast直接生产 | 不把姿态替换战技改称终结技；替换、区域目标和青霆剑数量仍需实际状态 |

接受者的OnAddedBuff=9检查ID `buff_chr_0030_zhuangfy_talent1`，随后创建`..._base`，对象为Source=1、count=1，继承三项选中BB及skillCastInfo。基础标记是0.10000000149011612秒的短buff，基础增幅承载buff则读取BB.duration=5秒；不可混用两份寿命。

基础承载buff的Stack=2、maxStackCnt=1、useMaxStackCntKey=false意味着新层替换旧层，不能使用保留旧动态rate的plain Refresh或独立叠层。OnEnable=5执行EnhancedAction，subType=6、rate=base_rate、duration=duration、asChildBuff=true；enhancingList只监听`..._mark`，operationType=1、value=enhance_rate。

追加mark的两份真实producer位于`normal_skill_ult_abilityrange`：timeline[5]的12～64帧TickIntervalAction，间隔原生float32=0.20000000298023224，先比较tick_index与EntityBB_SwordNum，失败路径Jump到64帧；timeline[6]在69～70帧另有一次mark创建。缺目标、剑数量、tick执行/Jump顺序时不推定mark总数，不能用伤害目标数或DamageHit条数替代这条生产链。

EnhancedAction字段中虽然有`buff_common_affixes_enhance_pulse_zhuangfy_talent1_child`，但overrideChildBuffId=false。本批保存这份被引用的完整记录，不将该字符串视为实际选中的子buff；KeywordActionWithSubType的实际选择和增强监听实现继续待核准。

## Buff添加事件的代码证据

当前GameAssembly和metadata摘要与已提交skill_timings索引一致。窗口长度用于SHA校验，可能含邻接函数，不能当作完整函数长度。以下只确认列出的局部调用及分支，不推广为完整事件模拟。

| 方法 | RVA / 窗口字节 | SHA256 |
| --- | --- | --- |
| AbilitySystem._AddBuffInternal（56498） | 373aad0 / 6500 | a9cb42d8e7625c26f7d6381e4b0c1f101f3b3acdb032412d08825628a7aba7b4 |
| AbilitySystem.AddBuffFinal（56499） | 3739fd0 / 4500 | 9874219eafb2b18ab54b3f63206558cbf1c4fb3f4c382174f0e263bff41b7ff4 |
| Buff.BuffContainer.CreateBuff（60779） | 373ba40 / 9000 | 705aad865b3c58abb0f8dc6b6218e2333fcdacbd5c23c94396d148b272135f63 |
| Buff.BuffStackingGroup.StackBuff（60757） | 373af10 / 7000 | e56b137b7d31f8d836acaa60dad044243dd8cea060a31c6aa8193c2528837062 |

CreateBuff在373bd52向来源发送206=OnBeforeOutputBuff，373c0bc向接受者发送205=OnBeforeAddedBuff；373ca9d或373cb66调用StackBuff。373d07c发送接受者9=OnAddedBuff，随后373d1f8发送来源102=OnOutputBuff。3033920/3034140分别按当前模块方法指针表解析为AbilitySystem.TriggerEvent/NeedTriggerEvent。

373cf01的非空r15分支进入373d2f4，最终跳回373cf49；空r15路径也可到373cf49，然后发送两份通知。上下文分别写入新结果+0x38和out实例+0x68，因此“无新实例”并不等于“没有buff添加通知”。Unique/Refresh、被前置处理改变、失败/旧对象、OnEnable重入和父子挂接顺序尚不能用当前简化的新增实例回调替代。

## 下一步与验收边界

逐角色、四个canonical技能及选中天赋已经登记`native_marker_release_candidates/checks/evidence`。基础BB、标记门槛/帧、追加tick与清理需求均能追到具体原生对象；本次不修改伤害定价或消除未知。

剩余依赖：AddBuff上下文身份/对象与前置事件；实际目标/战斗门槛；EnhancedAction的实际执行、动态rate传播和父层替换清理；区域tick/Jump/剑数量的真实producer。第十二批的`source.battle_lightning_hits`是显式缺输入；第十三批按下面原生mark域修正名称和运算，仍不供给默认0或最大值。暂不接master、实际识别或技力时机。

## 第十三批：确定模板选择与逐mark浮点加法

KeywordActionWithSubType.ExecuteInternal（58746/RVA41e1900，20000字节窗口SHA256=ee04ac495109ec39917fec1c637279c03aa3e46502361d2a20f45b200d4ad57c）的主执行块在41e4062后。当前metadata的KeywordActionData字段为duration+0x30、rate+0x38、overrideChildBuffId+0x40、childBuffId+0x48、asChildBuff+0x50、enhancingList+0x58；subType+0x68。41e44d1～41e44f5检查override开关，false传空child override，不读闲置childBuffId。原生枚举Enhanced=3/Pulse=6。

GetKeywordBuffName（56567/RVA41e17b0，5500字节窗口SHA256=08c7dfa97c90a84ac5ea6fa9d45810591a33e9ce4bd2d58df1204de42b2c9eb3）的Enhanced分支switch第6项跳4f93740，从RVA d06dd80读取未解析literal usage a000f90d，tag=5/index=31878。当前metadata的literal行offset=255288、数据offset=1426817、长度33，UTF8值为`buff_common_affixes_enhance_pulse`。这证明模板名，不凭配置字段猜名字。模板本身BB.child_buff_id=`buff_common_affixes_enhance_pulse_default_child`，false override保持该值。本批把这两份完整记录也按当前VFS及回编码核准，快照8→10份；它们的执行与实例传播仍未完成。

_DoApplyKeywordBuff（56570/RVA3dfaa00，3000字节窗口SHA256=fa9dbf105812d5baf9e95eacbef1b1e5440c0a6a27e32032dbbb8a8dfb918ec2）将传入float32 duration/rate转换成double存BB；相关literal分别为duration与rate。CreateBuff在373d2aa、接收/来源通知之后，调用TryEnhancingKeywordBuff（56571/RVA373abe0，8000字节窗口SHA256=b76d44d13c455e90c41f7435347fbb27f4c774beeccd21710fbc97dbbd236349）。其out-of-line循环RVA4e23096/3200字节SHA256=96f7586e808a4a96fa60b04637150ae7ddec8725e5b94dc4542cc72ed3016fc7，在buff ID匹配后GetFloat(rate)，operationType 0设置、1在4e23184 ADDSS、2 MULSS，4e231ad写回新float rate。遍历enhancingList后还有属性刷新/通知链，仍需完整执行；不能只改一份公式宣称原生加成完成。

因此canonical天地造化幅度使用`native_float32_repeated_add`：起始rate先float32，每个实际追加mark再做一次float32加法。输入改为`source.zhuangfy_talent1_marks`，与伤害hit/受击目标条数分开。0、1、3、10个显式mark的rate分别为0.18000000715255737、0.20000000298023224、0.23999999463558197、0.3800000548362732；旧double线性乘次数不能严格复现。其他加成默认linear不变。缺mark数、负数、小数、非有限数或超过消费者工作预算时返回未知，预算不是游戏层数上限，也不截断到上限。旧battle_lightning_hits键不再供应这份新输入。

本批只修已确认数值消费者和实际模板证据。canonical的battle_cast标签仍是旧模型粗略触发，不能当原生标记帧证明；不新增模型释放绑定，也不改变完整程序覆盖。真实mark计数、EnhancedAction注册、属性通知/刷新、父子清理与实际释放帧仍未闭环。导入前全部进度快照重生成逐字节一致；改后32面板/128报价/profile数值不变，仅来源摘要更新。4项新增覆盖逐mark精度、未知/旧输入、普通linear隔离、格式拒绝和实际电磁结算。
