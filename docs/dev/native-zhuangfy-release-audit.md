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

## 第十四批：已确认标记片段的接收与来源通知

`reviewed_marker_events`仅批准普通/姿态战技创建基础标记、姿态区域创建追加标记这三份原生producer中的两个plain self标记。实例必须是Unlimited、原始0.10000000149011612秒、无BB/回调/订阅/属性项、无父根或动作绑定；改对象/定义/producer则保留未知。创建完成后按原生次序发布接受者OnAddedBuff，再发布来源OnOutputBuff，传递实际标记ID、对象及明确的模型skill_type。不存在尚未执行的本角色前置事件时才允许这条受限通知；其他角色的前置事件不阻断本人路径。

选中的满级天地造化真实OnAddedBuff订阅随后创建5秒基础实例，使用18%/2%/5秒选中BB。基础标记到期不提前销毁基础实例；第二次确认基础标记按原始Stack/max1替换旧基础实例，旧截止不销毁新实例。追加mark仅发布自己的ID，不误触发基础mark监听器，不重建或延长基础实例。普通cast、重复/资源不足/死亡的动作都不补造标记。提前移除及预测副本互不污染。

这里只执行调用者已经确认门槛/帧的实际原始创建片段。完整技能的战斗/smart_target门槛、69帧区域目标检查、tick/SwordNum/Jump仍未知；没有加入cast0标记或命中数→mark数映射。inheritSourceSkillCastInfo的完整身份、前置修改、Unique/Refresh/null-result重入通知、EnhancedAction注册/rate/属性刷新/父子清理仍未绑定，基础实例创建不等于实际增幅结算。canonical粗略battle_cast规则及缺mark数诊断不被移除，也不供应零计数。

逐角色和四技能台账新增独立`marker_notification_binding`，完整producer仍not_bound；14模型释放/2原生ATK实例绑定及完整程序2/111不增加。6项新专项分别验收实际普通/姿态片段和选中天赋、两通知顺序、替换/到期/隔离、追加mark、未经核准入口、前置回调与失败动作。70项相关回归通过（14.017秒）；全仓1421项通过（109.905秒），最后补失败动作专项后9项通过。首次测试构造遗漏action_id/读取错误视图/片段选择只取到前置检查已修正；首次相关命令包含不存在的测试模块，换成真实Ability/父根模块后通过，未删除失败语义。

同版代码继续定位增强后的调用：4e23233实际是Buff.RefreshPriority（60710/RVA6043d7c，3000字节SHA256=61702efd53fbd19f2b2be08a57c96d68b82cc080a40f23c4c8db12a6834bbafd），4e2323d是Buff.OnBlackboardValueChange（60721/RVA43b0b00，3000字节SHA256=a271659bba28cdcaf110241ebe7a809eb3005edd031748f69fcf0378d819e907），不能把第一调用直接称为属性重算。后者在43b0b44继续调用43b0ca0，携带BuffData、BB及a8字段；这条下游刷新/缓存/转换链继续核准。4e23256的373e510是get_buffInstId（60654，3000字节SHA256=6d1ce97ad29ec54e33b8c9e599e5aeb3ae5165c98b9a8d94dfe7c66f0b40512e），用于后续通知，不能代替属性身份。

第十四批保存后fetch发现master前进两个提交，已保留rebase前备份并无冲突同步至3d3f8adb。同步后的全仓1436项通过（110.343秒），包括最后失败动作专项及master新增测试；仍只推送研究分支，没有接入master编排。

## 第十五批：增幅属性重载及计数域

实际Pulse模板的属性项为66=`PulseEnhancedDmgIncrease`、formulaItem=5、modifyAttributeType=0、非转换，读取动态BB.rate。默认child没有属性项，OnEnable另外创建`buff_common_vfx_char_atk_up`；其本身不能再加一份电磁增幅。模板的stackingType=0、usePriorityKey=true、priorityKey=rate，增强rate后要先RefreshPriority，组排序/启停结果不能跳过或默认所有来源相加。

同版metadata字段偏移确认：Buff.m_enhanceCnt=0xa8、m_priority=0xac、attributeMask=0x150、blackboard=0x160、owner=0x178、source=0x188；BuffData.attributeModifier=0x38。追加mark次数是另一域，不能把它当m_enhanceCnt或当前Unlimited实例数。当前两个marker通知也没有生产这个属性重载计数。

OnBlackboardValueChange在43b0b44调用`Buff._ModifyAttributesModifier`（60722/RVA43b0ca0，6000字节窗口SHA256=309885182dcabd1ae60542601a30a90ec78026e391e2d2a30534c3e356563ef8）。后者在43b0dd8调用`Attributes.AttributeModifierLoader.LoadAttributesModifier`（60584/RVA2db1c00，6000字节SHA256=f323aa798407d68f7bdec951b5f9a7ff3b87c487d57d3d855b1ca2642bbd7b9d），传入实际BB和m_enhanceCnt，返回的mask在43b0de4写回Buff。随后43b0e88调用`Attributes.MarkAttributesDirty`（60541/RVA347a9d0，6000字节SHA256=6d1f9778b1b7eb66ce17484aaac786557585569631702105e5d75a87007622a2），它遍历mask并标记对应属性，而不是直接返回新面板。

Loader在2db1cff调用GetFloat(param)，2db1d36将实际整数计数转float32，2db1d43做MULSS，再在2db1d47转double交给修饰项层。先前增强循环的每mark ADDSS修改rate，和这里的param×m_enhanceCnt必须分开；不能再乘追加mark数、用最大层数或默认1弥补未知。loader的八份加法/倍率列表、old/new mask、转换、缓存依赖和父子BB通知均仍需完整执行；_ModifyAttributesModifier的外部分支4fbc964也未完整核准。

这些来源已加入选中天赋的`keyword_attribute_refresh_evidence`，四技能继续引用该角色候选，full producer保持未绑定。新增专项从实际模板/子记录与原生枚举核对属性66、非转换、动态rate、child无属性与rate priority，保持未知的计数和转换边界。没有修改任何面板、报价、绑定数或执行逻辑；完整程序仍2/111。

本批额外固定方法证据的已核准构建域；即使索引、manifest、payload一起换成另一构建也拒绝混用旧方法证明。1项新专项及marker/幅度/全角色台账相关28项通过（8.513秒）。没有执行逻辑变更，不重复第十四批已完成的全仓1436项。

## 第十六批：补齐属性增强计数的初值证据

`Buff.Reset`（60689/RVA2db0360，16000字节校验窗口SHA256=8af7c178774dd51b3918c106acd7bec783dc8e4187ea10595bfc84bbb2aad60d）在2db151c明确将m_enhanceCnt设为1，随后2db162f把同一字段交给属性loader。该位置在本方法主执行块及首次ret之前，不能因窗口包含邻接代码而误归属。这里补清楚初值来源，不往当前模型或最终伤害输入塞默认计数。

`Buff._Enhance`（60716/RVA347aa90，5000字节SHA256=27177ca431e24596bb51f1c203c9304de95d1571d9edf926bde958ed2e799f46）在347aad4先增加m_enhanceCnt，347aae8执行buff event=6，再于347abe9调用属性loader、347ac66标记属性脏。不能省略中间回调、BB读值/覆盖和后续其他通知。TryEnhancingKeywordBuff的mark→ADDSS修改rate与这条buff计数增加仍是两条独立链。

第十五批的“初始化未知”已更新为有来源的Reset=1；实际模板创建、增强回调/优先级组、缓存转换及父子清理继续未知。逐角色候选保留初值、更新次序、方法摘要；现有专项继续检查不转成最终计数/完整producer。不增加执行绑定、面板、报价或完整程序覆盖。
