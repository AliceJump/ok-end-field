# 动作绑定buff寿命（2026-10-07）

接续mechanism-agent-tasks.md任务1～8后的长尾执行，不改变技力观测/返还时机策略。先验证安装客户端的DLL/metadata SHA256与skill_timings/20261002/index.json完全一致，再从metadata字段偏移和对应机器码窗口核对；外部研究脚本、客户端路径及原始提取物不提交。

## 核对结论与执行边界

CreateBuffAction.Data的autoFinishByAction在偏移72，inheritSkillIdList在80，finishWithNextSkillIfNotInherited在88，asChildBuff在89。ExecuteInternal分别检查子buff标志并调用SetBuffParent、检查动作标志并保存创建实例列表，二者不是同一生命周期。

OnEnd先检查autoFinishByAction，再走继承/结束原因分支，普通清理逐个处理创建实例。其外置分支检查结束原因7、技能ID是否在继承列表、finishWithNextSkillIfNotInherited，可能调用_TryAttachCreatedBuffsToNextSkill；转交成功时不走普通清理。因此不能只看“自动结束”标志就统一删除。空inheritSkillIdList无法匹配该分支，本阶段只执行这部分。

SetBuffParent保存父根并加入父对象子列表；支持的父类型不止Buff。父清理方法逐个移除有效子实例。首批只执行动作绑定；后续Buff父对象范围见下文，不把子buff统统挂在施放角色上。

本轮支持满足全部条件的创建：根SkillData原生timeline窗口、autoFinishByAction=true、asChildBuff=false、继承技能列表为空、可执行NativeBuffProgram、触发间隔为非正的已知数值（不产生周期tick）。结束截止时间取创建节点所属窗口_endFrame；角色handoff只是调度边界，不当作该节点自然结束时间。提前切同一角色的下一动作则按创建动作ID取消其实例；队友动作不取消。buff自身expires仍按原duration保存，不将其计时值夹到节点截止时间。旧实例的队列事件不能影响新实例；新状态字段进入fork和搜索身份。

以下继续保留缺口：正间隔周期buff（tick与节点结束同刻的引擎顺序未核验）、Buff回调的创建（缺原生OnEnd窗口）、跨技能继承/转交、父子根、canonical效果别名缺实例身份、未知堆叠/修饰器。模拟声明固定原生时间线场景，不据此证明实际命中、受击中断或真实技能资源确认时机。

## 核验窗口

长度为本轮读取的字节窗口，不宣称整个函数恰好该长度。OnEnd的外置分支另列，避免只看连续函数入口漏掉继承逻辑。

| 方法/窗口 | ID / RVA | 长度 | SHA256 |
| --- | --- | --- | --- |
| CreateBuffAction.ExecuteInternal | 58062 / 0x373e6a0 | 9000 | bdedeff062c8cb311dae71d10dd1c4824a8eb5cc1a673a0aa0d9439640c54bef |
| CreateBuffAction.OnEnd | 58064 / 0x373da10 | 9000 | e61f7547530bcfeec54549b818ddeda455490d0873748537e9e25544b28154af |
| OnEnd外置继承分支 | 0x4e23dac | 350 | 86a48d119f73728bb723d4feaf13c9bfad0c98f26c8c98f6c0de8dfc69f7f17c |
| _TryAttachCreatedBuffsToNextSkill | 58066 / 0x5fe12b0 | 9000 | cab98259bf5e3968b3d676557bf023c4ee75489ecf0b39d0a771918b4a4ffcdb |
| Buff.SetBuffParent | 60747 / 0x3dfa770 | 9000 | c29523505ad0198b4fab043a718a2dcc4239155a5451d2cf9f29c5d388361626 |
| Buff._RemoveAllChildrenBuff | 60748 / 0x3736120 | 9000 | e3c517ef9c7342f7813f300596f5900021e9dbef6208bd55cd6249de6709ad3a |
| Buff.MarkFinish | 60691 / 0x37376d0 | 9000 | bbb1a17c20f04c0d1b5117d11d73738575692aa9a3b4b5857fbbc7e601603ce2 |
| Buff.OnFinish | 60701 / 0x37365a0 | 9000 | 5b2994e58044d3d28e0ff17f6155ec5b9678497681fb63e81a200807792794f0 |

## 验证与实际覆盖

回归覆盖节点截止时间晚于handoff、自然到期先于节点结束、切招取消、旧截止事件与新实例隔离、队友不取消、预测世界隔离、真实buff资源继承，以及子buff/技能继承/正间隔仍拒绝绑定。原生测试以已验证BuffData与明确窗口构造，不将拼接测试冒充未修改的实战技能。

审计仍为2/111个完整程序树。原“子buff/动作绑定寿命”影响程序数从59降到23；这里只减少该种阻塞，不表示新增36个完整或可计价程序。新增周期时序和其他实例诊断仍严格阻塞。5次实测fork约0.31ms，四个合法战技约2.37/1.49/2.92/1.03ms，当前搜索11.22ms且no_complete_plan，不能作为完整展开预算验收。全仓结果见mechanism-flow-progress.md。

## Buff父对象释放链

继续核验同一客户端哈希后，CreateBuffAction.ExecuteInternal在0x373fa17检查asChildBuff，通过actionEnvironment取得父根，再调用SetBuffParent。Buff回调编译路径与运行时Buff实例UID相对应，不用角色ID或buffId替代父根；同名不同层、不同持有者和预测世界均保存各自的关系。

Buff.MarkFinish在0x3737787调用OnFinish，随后0x373779a调用_RemoveAllChildrenBuff；后者对有效子实例调用MarkFinish。因此先执行父结束回调，再清理子实例。结束回调内新建的子buff也在本次清理中移除；已提前结束的子实例、旧到期队列和重复移除不再触发回调。自然到期、显式移除、栈溢出和已绑定动作截止都通过相同实例清理入口。回调上下文以try/finally恢复，父根正在结束时仍可用于创建子实例，已彻底结束的旧根不能留下新子buff。

本批仅绑定Buff回调创建的子实例，要求asChildBuff=true、autoFinishByAction=false、空继承列表、可执行的Unlimited/Stack实例、已知非正触发间隔。子实例保留自身duration和BB快照。Skill/GlobalBuff父根、Unique重复挂接、动作绑定与子关系并存、技能继承、周期子buff和canonical别名仍保留明确诊断；不因此放行已有modifier或其他未知节点。

新增8项回归包含父回调读取仍存在的子实例、递归孙实例、栈溢出只清理对应层、提前到期、显式移除永久子实例、BB捕获、跨持有者、fork隔离、结束回调新建子实例和无父根/Unique拒绝。另以未修改的buff_chr_0007_ikut_atk_buff_talent创建节点和buff_common_vfx_char_atk_up记录验证编译绑定；测试提供父duration，原记录的属性modifier缺口仍保留，未声称整个技能可计价。

完整程序树仍2/111。一般child/action-bound诊断影响程序23→22，另有2个程序明确报告子实例独立性缺口，不能据此宣称净覆盖增加。全仓1257项通过，之后新增显式移除回归并完成8项专项验证；事件按需输入3项及Ruff I/F通过。

## Unique子实例与继承的进一步核验

Unique（枚举7）的跳转表目标为0x373b304，先查询未结束实例并清零rbx；已有实例时跳转0x4e23843，将空创建结果写入输出并返回0。CreateBuffAction在0x373f95d检查创建结果有效性，无效时跳过后续SetBuffParent。因此Unique重加保留首次实例、BB、自然期限和父根，不重新执行开始回调，也不挂到第二个父对象。这与“返回旧实例后重新挂接”的行为不同；不能据SetBuffParent单独推断重加转移父根。

在原Buff父对象范围内进一步支持Unique子实例。两父对象的结束先后均有回归：第二个父先结束不移除旧子实例，第一个父先结束则结束子实例，第二个父不能延长或重新赋值。真实buff_chr_0035_liino_potential的子创建订阅及Unique叶子编译已验证，已有stacking priority缺口仍保留。父子专项共11项，加原生命周期和共享堆叠共26项通过；全仓1261项通过（65.068秒）。完整程序仍2/111，一般寿命诊断22个程序，子实例独立性诊断2→1；没有新增完整程序。

| Unique核验窗口 | 长度 | SHA256 |
| --- | --- | --- |
| Unique分支0x373b304 | 123 | 506c7e427ce1eaba631d58d5481bc1b036aac7ae5f554deb928cfee7d7ab71d9 |
| 已存在实例返回分支0x4e23843 | 22 | cb4c6344d8783493d37c3271ed122918797eafd49790b113e5e0aa5e49ad87f7 |
| CreateBuff创建结果检查0x373f95d | 60 | 9e81142bfd879216a7ba65069f63b4ecbb64871a81a5b5de6e05c78196cf22c5 |

跨技能继承仍不执行：OnEnd外置分支读取actionEnvironment.context的结束信息，要求结束原因7且目标技能ID在继承列表。该匹配分支中finishWithNextSkillIfNotInherited=false直接保留实例；为true时调用_TryAttachCreatedBuffsToNextSkill，转交失败才回到普通清理。方法通过创建动作owner的AbilitySystem.activeSkillMap查找真实目标Skill，逐个有效实例调用Skill.AttachBuff，后者仅加入该Skill的m_buffsDuringSkill（偏移144）。因此下一步必须绑定结束原因/目标技能、真实技能对象与其释放链；同一角色开始任意下一动作不等于已证实这条原生链。不能只将旧节点截止改成下一动作handoff。

继承补充窗口：OnEnd分支0x4e23dac/220字节SHA256 `5819478582714efd2371eaf56fe3e6128f4557e24f6d69c59d1932dd8cf035bd`；Skill.AttachBuff 0x467b710/600字节 `2ff7706b929919ddefac71197f228cadb3110bdaa033ec6894c1d82e5490f5cf`。后者为读取窗口，实际入口在0x467b76c返回，窗口包含后续邻接代码，不把600字节当完整方法长度。
