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

剩余依赖：AddBuff上下文身份/对象与前置事件；实际目标/战斗门槛；EnhancedAction的子选择、动态rate修改和父层替换清理；区域tick/Jump/剑数量的真实producer。现有`source.battle_lightning_hits`仍是显式缺输入，不能凭本次台账供给默认0或最大值。暂不接master、实际识别或技力时机。
