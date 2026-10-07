# 动作绑定buff寿命（2026-10-07）

接续mechanism-agent-tasks.md任务1～8后的长尾执行，不改变技力观测/返还时机策略。先验证安装客户端的DLL/metadata SHA256与skill_timings/20261002/index.json完全一致，再从metadata字段偏移和对应机器码窗口核对；外部研究脚本、客户端路径及原始提取物不提交。

## 核对结论与执行边界

CreateBuffAction.Data的autoFinishByAction在偏移72，inheritSkillIdList在80，finishWithNextSkillIfNotInherited在88，asChildBuff在89。ExecuteInternal分别检查子buff标志并调用SetBuffParent、检查动作标志并保存创建实例列表，二者不是同一生命周期。

OnEnd先检查autoFinishByAction，再走继承/结束原因分支，普通清理逐个处理创建实例。其外置分支检查结束原因7、技能ID是否在继承列表、finishWithNextSkillIfNotInherited，可能调用_TryAttachCreatedBuffsToNextSkill；转交成功时不走普通清理。因此不能只看“自动结束”标志就统一删除。空inheritSkillIdList无法匹配该分支，本阶段只执行这部分。

SetBuffParent保存父根并加入父对象子列表；支持的父类型不止Buff。父清理方法逐个移除有效子实例。当前世界还缺该父对象身份和释放链，asChildBuff继续未解析；不把子buff统统挂在施放角色上。

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

## 验证与实际覆盖

回归覆盖节点截止时间晚于handoff、自然到期先于节点结束、切招取消、旧截止事件与新实例隔离、队友不取消、预测世界隔离、真实buff资源继承，以及子buff/技能继承/正间隔仍拒绝绑定。原生测试以已验证BuffData与明确窗口构造，不将拼接测试冒充未修改的实战技能。

审计仍为2/111个完整程序树。原“子buff/动作绑定寿命”影响程序数从59降到23；这里只减少该种阻塞，不表示新增36个完整或可计价程序。新增周期时序和其他实例诊断仍严格阻塞。5次实测fork约0.31ms，四个合法战技约2.37/1.49/2.92/1.03ms，当前搜索11.22ms且no_complete_plan，不能作为完整展开预算验收。全仓结果见mechanism-flow-progress.md。
