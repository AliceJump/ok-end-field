# 原生动作执行进度（2026-10-04）

本轮从数据审计进入了部分机制执行，尚未完成整个机制模拟。本文记录分支当前代码及数据的核验范围，不把部分执行器视为完整排轴。

## 已落实的输入与状态

- 固定基准覆盖32角色，分别保存元素、普攻、战技、连携、终结技及通用伤害加成。原生命中读取自己的伤害标签；弭弗开天的猛击标签不继承战技加成。管理员P3为用户指定，普通六星P0、四五星P5，战斗天赋按各槽最高阶。余烬80级/R9，其余沿用现有90级/R12固定配装。
- 当前通用快照包含531份 SkillData/BuffData，逐份回编码与原始字节一致。Unity资产导入340份，其中33份 CharacterData（管理员男女两份）及26份标签配置；其余包括 SkillSetting、角色投射物及能力实体配置。资产JSON树、原始/回编码对象及加密包分别有摘要和来源记录；解包入口、客户端路径、研究脚本不进入仓库。
- SkillPatchTable覆盖366项技能的12级参数、消耗和冷却；原有两个技能有独立解码交叉核验，不能把这个核验范围扩写为全部366项。753项时序索引增加效果起始帧摘要，来自已校验的完整记录。
- `CombatWorldState` 保存角色属性与能量、敌人破防及附着、共享SP、效果实例、原生buff、角色EntityBB、计时器、未结束事件和逐击伤害。破防池20秒刷新/到期来自原生buff；自然到期不发布消费事件。实际消费层数按动作记录，下一动作的触发输入单独保留。
- `CombatRuntime` 在按键前创建预测分支，施放被接受后提交，拒绝或超时丢弃；新HUD样本按时点覆盖预测SP，随后发生的回复仍继续执行。死亡和主控观测同步到待提交分支。

## 已验证的动作链

弭弗断云/追形/开天的100/50/50 SP消耗读取等级参数，断云的50 SP返还读取动作。追形消费至少3层才生成开天就绪；2层不解锁，0层只建立第一层破防。就绪buff按原生期限到期，开天实际移除就绪状态。其他未解释的动作节点仍列入 `unresolved`，这条链不能据此宣称所有伤害与空间行为已准确计价。

骏卫 CharacterData 在 `OnBeforeAddedBuff` 检查猛击/碎甲标签及敌人破防层数，把层数保存在骏卫自己的 `EntityBB_noguard_count`，创建1至4档的连携标记。它监听队友触发的敌方事件，不能只监听骏卫本人的攻击。标签ID与query枚举取自原生数据，当前只执行已绑定的具体标签查询。各标记的6秒期限来自原生buff。队友消费4层、破防池已清空后，骏卫连携仍通过标记和保存值执行该档返还；当前基准的完整档位返还为35 SP。错误buff、角色目标、0层和取消施放均不产生标记。连携整体可用窗口、优先级和重复触发接受规则尚未完整模拟。

角色钩子仅注册全部节点已绑定的即时序列。其他角色的未知条件进入诊断，不把检查失败视为条件满足；没有触发全队四个连携的假设。该入口还需扩展到其他事件及原生连携调度。

## 原生公式核验范围

以下核验使用时序快照中固定的 GameAssembly 和 metadata 输入摘要。RVA、方法ID及字节窗口摘要用于标识本次分析对象；仓库不含解密密钥或客户端提取程序。

| 原生方法 | 核验结论 | 方法ID / RVA / 字节窗口SHA256 |
| --- | --- | --- |
| `Attributes.GetPhysicalAndSpellInflictionEnhancedValue` | Linear为A×x，InverseProportion为A×x/(B+x) | 60539 / `0x603702c` / 1400字节 `c452e53e7a559cedf585a7edd47c0ebf1039dd9a125e202264ef4c4601804fb4` |
| `ReadSkillSettingData.ExecuteInternal` | 调用端按1+上述增量乘原生表行，并读取技艺强度 | 59349 / `0x4247890` / 2500字节 `34c86f12d585f299b640cb951d5f025630a8ce0e32d51bffffaf399b79ec9f2f` |
| `SimpleCalcBBAction.ExecuteInternal` | 已核验Add和Mul路径；其他运算仍需继续核对 | 59920 / `0x3df8c50` / 1100字节 `5219be2bf03ed0ce7e2089d5940fd5694917e6afdcb9f8af3502acc627375985` |
| `ObtainUspInNormalSkill.ExecuteInternal` | SkillCastInfo非返还SP消耗×buff的ratio×自身/队友系数，再按接收角色计算能量增幅；不读取旧usp_self/everyone字面值 | 59018 / `0x3c651c0` / 9000字节 `50de1132ce3aa9d99a67f3e46edbf510953e5a1f87cbededabb83ac9428dd7e8` |
| `BattleManager.GainAtb` | Return将实际入账量加入全队m_returnedAtb；SP溢出不进入返还池 | 61786 / `0x3b22910` / 6000字节 `01c2cedb44fd3e9ad8491f1f69b4d83d63d6027fe2f542eadca8a98b6c34bbe0` |
| `BattleManager.SpendAtb` | 消耗先扣m_returnedAtb，输出本次非返还SP消耗；Skill._ApplyCost将其存入施放信息 | `0x3b203a0` / 6500字节 `1ce747c0c024b82be12a627faf92472dd0bd2031437d0e34718425cd482b8e6b` |
| `ObtainCostAction.ExecuteInternal` | 能量回复先按接收者增幅，再乘百分比容量，最后乘coefficient；SP区分Gain/Return及主控source限制 | 59013 / `0x3b23580` / 12000字节 `ab71666ed2f8e324381c0f72f1159bee1f9cf2a653e5af3924d10352dc879941` |
| `BattleFormula.CalculateUltimateSp` | 仅正的基础回复量且未ignore时乘接收者能量获取率；负基础值不放大 | 60627 / `0x3c65630` / 1800字节 `0c017f4173b893109c1c2c21bce5bae00371925c314c15493b4968b20d0c2c1b` |

返还池属于全队，不是“本次消耗减本次返还”。例如初始没有返还池时，断云100SP施放信息仍记录100；返还50后，追形优先消费该50，非返还消耗为0。默认能量读取各动作在施放时保存的值，事后返还不追溯改写。搜索状态也包含返还池，避免将同SP、不同能量潜力的路径合并。资源目标保留Source/Owner/MainCharacter/Target/Context/MainTarget枚举；未绑定上下文和InstantSearch仍明确报缺口，不能全部改成施放者。尚未把这组目标解析扩展到所有buff持有者与场地实体。

目标组现在保存对象身份而非只有数量。已绑定的敌人/主目标/角色全队/来源选择器、复制与合并目标组及ForEach均按对象执行，重复对象合并后只出现一次，空组不会变成一个敌人。ForEach结束或失败时恢复父目标，查询buff及施加/移除buff按实际接收者处理。伤害动作读取自己的目标组，未知目标组不会产生虚构命中；未知空间、排除、筛选规则仍报告缺口。角色钩子的完整性检查递归检查循环与监听子节点，不能因未知动作藏在循环内就注册它。该阶段新增五项实际目标/接收者回归，全仓992项通过。

原生非简单伤害计算读取 `AtkScaleCalculation`，不误用处于非激活分支的字面倍率。条件分支先保存一次判断结果，再消费或修改状态；未知条件阻止后续序列。动作参数按已选技能等级、最高阶天赋和实际潜能应用Add/Mul/Overwrite；缺少数值不自动按0补算。原生buff的全部堆叠政策、上下文持有者、动态继承和物理异常暴击资格尚未全量核验，不能用当前执行器结果替代完整伤害真值。

## 还未完成的关键工作

天赋/潜能附加技能与buff现在有独立的常驻生产者入口。按固定基准选择48条AddBuff/AddPassiveSkill记录，引用均在已校验快照中找到；47条成功注册，萤石天赋2的`probability`与二潜Add操作重叠，完整原生覆盖/刷新顺序尚未确认，明确报`Unverified attached skill blackboard precedence`并暂不注册该项，不能把20%+10%的数值推算当作已核验执行。类型枚举AddPassiveSkill=1、AddBuff=5来自metadata字段默认值，不以描述猜测。

附加技能通过Ability.Enable挂载SkillData.buffs并注册passiveEventActions，包括时间轴为空的余烬天赋。生产者不消耗SP、能量、动作锁和冷却，不推进玩家技能阶段；常驻scope保存本角色的BB，子buff继承当次值并保留实际Source/Owner，重复初始化不重复挂载。事件订阅只接收实际发布给拥有者的事件及其payload；死亡拥有者不执行，未绑定的条件、属性/伤害/治疗/护盾processor和事件目标仍报告缺口。普通passiveSkillType=0不读取序列化残留的toggleBuffs；类型1切换被动仍未实现。Unique/Unlimited不读取Stack政策才使用的maxStackCntKey，避免用未激活字段误判缺少参数。

原生入口证据：TalentUtil.TryGetCharTalentAttachedSkillCreateOptions 6928/RVA0x61edbec/7000字节SHA256 `e1b7a29d69836778fb17286593b7063a36dd86c7019333c6c4d28897f59e1a95`；CharMiscFeature.RefreshTalentPotentialAttachedSkill 56747/0x353bc20/6500字节 `4ef208aa4d31f37ac678922dbb9027eec2455e9836e9c08f825d019058582014`；Ability.Enable 55810/0x30fbde0/6500字节 `0599d401b1a44da49964402a3853c527500307f76ff3894ea95e6e22aba317e6`；Ability._AddPassiveBuff 55825/0x30fbef0/7000字节 `8cbfd2e301e1e86cb5966e7ed441fa6a2d893613543be7b45d2d328cb022fca4`；Skill.Create 55887/0x37355a0/7500字节 `b403d4e30b5f1530f01ee0d41f7a8da364b0bc6d72df04f5c01a89caf92bc056`。这些窗口只支持上述入口与分支结论，不支持推断整个天赋或所有BB合并顺序已经完成。

技力返还时机与HUD变化阈值确认释放，按用户最新要求后置。下一步继续事件目标/标签及buff条件和processor，不因注册被动入口就宣称完整机制模拟已完成。

本阶段八项专项验证被动入口选择、空时间轴挂buff、启动幂等且不消耗动作资源、潜能等级选择、重叠BB诊断、未解析条件阻止挂载、事件接收者与payload、预测scope隔离；全仓1018项通过，修改文件Ruff I/F、语法/JSON解析、敏感路径与差异空白检查通过。

buff执行新增独立实例与BB快照，Source与Owner分别保存。已接OnBuffStart、OnBuffEnable、DuringBuffEnable、OnBuffTrigger、OnBuffFinish及已发布事件的订阅；默认终结能量现直接执行原生buff开始动作，不再在CreateBuff按名字补一份公式。周期事件支持waitFirst、触发次数限制和独立到期，原生OnTick先更新周期计时再处理到期，因此同一截止时点的末次触发仅执行一次。移除/栈溢出取消实例后续事件，Unique重复添加保留原实例，Stack分层到期、满层移除最早实例，Unlimited新建实例。动态继承从inputValueKey读取本次实际BB值，随后不会随生产者BB改变。周期、结束和订阅动作均使用接收者的独立上下文。

生命周期核验标识：OnStart60698/RVA0x3fa5260/9000字节SHA256 `3acf329987a4c34cd2c835c1731411234501c97f2308e6f26090e3de28898c9e`；OnEnable60699/RVA0x3739a90/4000字节 `cdcb7877461295ea9aeb4aae90e255da733fa2ef9fccb12114f6abccbd534bd9`；OnTrigger60703/RVA0x41f1c80/3500字节 `161134b93e358edb08c2e4633aadc0f79720cd23aeb6261b1cf2bdd88a11e52b`；OnTick60704/RVA0x3106030/15000字节 `2e2612a5cdbe038bec483c473c05feb26dc998698b02a93c193cb462b2495a76`；StackBuff60757/RVA0x373af10/13000字节 `94144f9b51e85b3ea463460484b9f7bdde92f06e872880dd3f454adfc49c6649`。只绑定上述已核验分支，其他堆叠政策、共享stackingKey、buff时间暂停、ignite/timeline及未发布的事件仍明确报缺口。原生提弗洛斯普攻返SP的buff开始动作已验证动态继承并执行。32角色当前能编译101个动作，尚未完整计价；原生资产与JSON标签两种编码已统一读取，未知标签继续阻止对应条件。

buff专项八项覆盖持有者、继承快照、期限末次触发、触发次数、移除、独立层期限/溢出、Unique、结束及订阅解绑、预测隔离和真实原生开始动作。该阶段全仓1000项通过，变更文件Ruff I/F与差异检查通过。

技能替换现在保留原生技能槽及独立替换句柄。SpecificTime按实际到期恢复指定技能，Infinite保留至被替代，FinishByAction在所属动作结束或buff作用域结束时恢复。同槽新替换先捕获当前技能作为默认恢复目标，再结束旧句柄，随后应用新替换；旧句柄的排队事件不再改写新技能。冷却继承按完成比例换算到新技能的冷却长度，恢复普通/连携技时再次继承当前进度；原生终结技恢复路径没有该冷却复制。未绑定的冷却程序在改变状态前报告缺口。计时事件与选择状态都进入预测分支和搜索身份。

原生替换引用沿技能及buff依赖递归发现，当前32角色编译111个动作，包括庄方宜终结姿态战技/连携、卡缪追猎及洛茜后续连携；新增动作只能在对应槽实际被替换后选择。管理员共享进度ID与男女CharacterData资产别名单独处理，洛茜的combo_1命名也不再被当成角色ID。弭弗旧就绪状态仍保留，带输入缓存覆盖的替换尚未执行，不能把它记为完整原生替换链。

替换核验窗口：ChangeSkill.ExecuteInternal57795/RVA `0x5fd6a24`/12000字节SHA256 `3ca90299060e698372d3671fbe9e2464282094062bad063952728e634d996618`；OnEnd57796/`0x5fd6c28`/1800字节 `51e3a9e59ab218833504dc50808e8decbea32eed2a65cb4a1a29f1337c78923e`；AbilitySystem.ChangeSkill `0x5fba178`/8500字节 `5949fee9ebfc74a1305a4c7bab0a3f6279f45a0c8a475409a206d6c9521bbe4b`；ClearChangeSkill `0x5fbb0ac`/1400字节 `dcc4bc03c839f3a1738c50121c31a4073551f14193b2a12d31ec6136887e78ee`；Handle.Revert `0x5fc0d58`/4200字节 `4f356c2e79b795e68d76b3e0ac674658292227226ee52b161f027a692fb62aec`；冷却进度 `0x2fa9c30`/800字节 `605584a2e2422628696c664b51e845b168ddb3734cc284655c861a5635d09557`，写入比例 `0x60783d0`/1700字节 `8d62f7bf9bdc6e84e10e81216522d046a5d2788cf4eb28d181cb25ef58a70d36`。SkillSlot/LifeTimeType枚举从metadata导入并保留原始编码证据。专项10项通过，覆盖真实洛茜6秒替换、3角色目录引用、期限/作用域、连续替换、冷却及预测隔离。

替换阶段全仓1010项通过，变更文件Python/JSON解析、Ruff I/F、敏感路径及差异检查通过。

标签执行阶段把原始四字节和Unity的tagId归一到同一个有符号32位ID。26份配置有6956个显式名称，原生Build建立父节点后共6962个；UTF-8 CRC32与本地GameplayTagPredefineTable的179个既有ID逐项一致。实际子标签可匹配父标签，反向不匹配；HasAny、HasAll、ExceptAny、ExceptAll分别保留任一、全部及其否定语义，0为无效标签而非一个新节点。按标签查询保留实际目标，BuffCount累加层数，BuffIdCount计算不同buff ID数量；未核实多目标选择先后时拒绝把全队相加。普通无回调buff也发布标签并受其到期状态约束。

物理异常事件现在传入实际敌人目标，回调临时覆盖current/trigger并在结束或异常后恢复；buff的Source、Owner及持久BB不随事件目标改写。移除破防标签硬编码后暴露的弭弗回调误查自身问题已经修复，追形消费3层解锁、2层不解锁与骏卫消费前保存层数的回归保持通过。洛茜CharacterData中两种编码的标签条件现在可以注册；这只说明条件可以执行，法术反应事件生产及完整连携可用调度仍有缺口。

核验窗口：GameplayTag构造74614/RVA0x2fee6e0/1300字节SHA256 `04a90fc190a44acacc0864dca3f68822b16aa141d681545fe2221a5bebaa8f9d`；CRC字符串入口0x2fedec0/1600字节 `866a0b29dd38c991e4d10a160ce9c0627acd7c6cdd83cc9b9ec31197181f6e68`，父子匹配74713/0x6275b84/1800字节 `058037345b67dfccf9ebc3d0999fb14ec66a62e4d5cc6e4dde543f5177677ac9`；查询74709/0x3629720/1800字节 `fc2dbec7f486fb1c1e020b752c3775c628aae8be68256c681f4721cecec7421f`；层数56515/0x44dd690/6500字节 `160b9a727157f270df2a79f76830173bcf20ca1380e41610fa3f599e39b5a821`；不同ID计数60804/0x6040464/6000字节 `55b1d3dc5f0ee3b85dab192379c26b30c22000da559f7e164024a3887146d3ce`。BuffStackNumType枚举保留metadata默认值证据。专项46项与全仓1027项通过，Python/JSON解析、Ruff I/F、敏感路径及差异检查通过；技力返还时机及HUD阈值确认按用户要求后置。

1. 全角色被动事件、EntityBB/SkillBB/BuffBB作用域和上下文选择器；同一角色的其余原生buff堆叠、重复触发、监听解绑及技能输入缓存。
2. 投射物、子技能、场地实体和持续伤害的完整生命周期；法术附着、燃烧/导电/腐蚀/冻结/碎冰等反应的真实伤害与计时。
3. 原生追加攻击、暴击层、普攻次数、治疗与护盾、条件武器/套装生产者，以及具体敌人的防御、抗性、失衡窗口。
4. 将 `plan_action_sequence` 真正接管动作选择，替换旧排序和阶段SP储备；当前 `TimedCombatLogic` 只提交已确认动作和读取部分替换状态。不可宣称已经能完整比较“余烬100SP的边际收益”。

序列搜索对包含 `unresolved` 的结果拒绝计价，已实现低直接伤害生产者在有限搜索中保留路径的逻辑；这些是可执行测试能力，尚不是完整运行排轴。当前搜索只声明单个敌人在命中范围内的场景，没有从按键成功推导所有实际命中。

## 验证

### 原生法术附着入口

`SpellInfliction+Data` 现在保留原生 source、target 和 isExtra，执行实际目标组，空组不施加，未绑定组和角色接收者明确报缺口。发送 OnCharBeforeOutputSpellInfliction / OnEnemyBeforeTakeSpellInfliction 后更新敌方池，再发送两个 After 事件；回调按触发目标读取状态，保留原来的来源与持有者。

角色对敌人的池为 `buff_common_energy_shard_attached_{fire,pulse,cryst,natural}`，不是 `buff_common_enemy_spell_*_attached`。后者标签属于 `Skill/Enemy/Common/SpellInflictOnChar`，代表敌人对角色的附着，默认10秒；前者属于 `Skill/Character/Common/SpellInflict`，默认20秒、最大4层、EnhanceAndRefresh=8。本次未提交的研究曾选错这两个方向，已通过原生 GetEnergyShardAttachedBuff 和 SpellInfliction 静态标签映射纠正。原生身份/标签查询及 FinishBuff 消费现在都读取同一个敌方附着池，消费/过期不会留下另一份影子计数。

世界推进时间现在调用 EnemyCombatState.tick，修复附着池此前始终不递减的问题；快照记录实际过期时间，预测分支独立。保留实际施加、叠层、刷新、上限及异元素清池状态；原生附着增强回调、法术爆发与异元素反应的完整伤害/免疫/生命周期仍明确标为未解析，不能据此提前通过伤害规划的完整性检查。没有用文字倍率替代未执行的原生回调。技力时机工作仍后置。

原生证据（客户端版本与输入摘要沿用本文件前述记录）：

| 原生入口 | 核对结果 | RVA / 字节窗口 / SHA256 |
| --- | --- | --- |
| AbilitySystemUtils.GetEnergyShardAttachedBuff，方法57220 | 0/1/2/3返回fire/pulse/cryst/natural对应energy shard池 | `0x3ea6890`，163字节，`970f4b55e0677972850ba96e35324db6232f0bd8b9279a9959739c41c0ab9fea` |
| SpellInfliction.ExecuteInternal，方法60013 | source/target解析、Before→buff转移→After顺序；同元素与异元素分支分别执行 | `0x3ea8220`，16000字节，`493507c4158070becd0c3469e10c52dbd2cb2003c3e39a1a4b258198edfe0230` |
| SpellInfliction静态初始化，方法60016 | 四种SpellInflict子标签与0/1/2/3双向映射 | `0x3ea6460`，2600字节，`ac5c6990b18413a2341db21a9454ba8579051476a3728225cc0442057159977e` |
| AbilitySystemUtils.GetSpellStatusBuff，方法57221 | 12种异元素入口，buff名称为新元素/被消费元素；寒冷→自然进入natural_cryst | `0x47eb600`，234字节，`5ffc7e282e4370ff85ceeaf23fd14e00e51adf627d8fad5d02751621c83e8133`；分支块`0x4c08d04`，750字节，`bbb22bc5457d7a89e1a868c406369c3d2eea6f8bad6bfdafc882e820fb9ae73a` |

新增9项回归覆盖真实佩丽卡原生动作、实际接收者、空组/错误目标、多敌人组、来源回调、20秒刷新和4层上限、ID/父标签/不同ID计数、原生消费及预测隔离。全仓1069项通过，变更文件Python解析、Ruff I/F、敏感路径与差异检查通过。

随后绑定 `CheckSpellInflictionType` 的四元素位掩码，读本次原生事件的类型，不读施放后已被消费的附着类型。原生方法91646，RVA `0x43cfab0`，2400字节摘要 `b65ba7c992c92a8213a4f6e143cceacc986e6ede01c7ed51047386d8f794d41a`，在 `0x43cfb57` 读取context类型、`0x43cfb5d` 测试对应bit。All=15与空掩码均保留原语义；缺少事件类型不执行后续生产者，非空savedKey的条件副作用尚未绑定，明确拒绝。IfElse内部停用的条件也按isEnable跳过，不被误认为一个真实限制。新增3项覆盖莱万汀的原生条件结构、四元素/All/空掩码、禁用条件、缺失事件与savedKey；全仓1072项通过。

原生伤害入口不再仅因为PhysicalInfliction标签就硬编码禁止暴击。BattleFormula.CalculateDamage（方法60623，RVA `0x3543c00`，6500字节SHA256 `1af816a9aeb24fc8973e70efc568fbb735be88fd504ab0632fbdadf56aa2241d`）在 `0x3543cf7` 读取攻击方属性索引9的CriticalRate，随后调用随机判定；成功时在 `0x3543ede` 分支读取索引10的CriticalDamageIncrease。索引来自AttributeType的metadata字段165370/165371。已检查的猛击、碎甲原生DamageUnit没有附加damageProcessors；默认计算路径使用来源面板的暴击期望。原生反应表路径同步执行该规则，旧文字倍率回退仍保留原有非暴击假设，不把旧数据扩大为已核验范围。

这项核验只修正按标签禁止暴击的假设，尚不代表全部伤害processor、来源属性快照、反应属性标量及特殊DamageType已经执行。回归覆盖0%、50%、100%暴击率对应300/375/450期望伤害，以及真实弭弗物理异常DamageAction的编译标记。全仓1073项通过，修改文件Python解析、Ruff I/F、敏感路径与差异检查通过。

标签阶段随后rebase到远程master `ad9d1afe`（包含#440与格式更新）。已经合入master的父分支提交不重复重放，仅重放本分支的语义归一及机制执行提交，保留master新加的部分队友识别、敌人占位探针和按键反馈逻辑。两份本分支所需的原生读取计划/回编码输入档案保留原字节，供数据重建及核验使用。懒加载完整记录的损坏测试仍在实际读取记录时检查摘要，不强迫读取时序目录就提前解压整份数据。

补全原先未知的队友槽位时，机制目录增量接入新增角色的面板、技能、事件和被动，保持同一战斗世界、epoch、已有资源观测、私有状态、冷却、死亡及待确认动作；预测分支同时接入新角色，随后确认不会覆盖掉补全结果。识别前的动作历史没有证据时明确保留未解析诊断。真实弭弗/骏卫/余烬部分队伍的补全回归与运行时专项72项通过，全仓1060项通过；技力时机专项仍按用户要求后置。

新增状态执行、运行提交和原生动作链的31项测试通过。角色钩子后全仓947项全部通过；此前7项回归已处理。分支随后rebase到远程master `c2fc0fd3`，保留#441的装备槽位校验、快照完整性及状态到期修正，并按合法装备槽位重算固定面板；缺失配件仍保留null，不虚构补齐。恢复旧排轴测试的导入和循环期望回归，合并翻译目录并重新编译MO后，全仓982项全部通过，排轴/翻译专项51项通过。变更Python文件的Ruff I/F检查及差异空白检查通过。固定面板重算输入来自仓库快照，客户端解包只在外部研究目录中进行。
