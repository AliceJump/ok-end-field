# 动态属性与释放加成核验（2026-10-07）

## 2026-10-08第六批：原生四维转攻击的取整

重新核对本地安装版GameAssembly与metadata，两者SHA256仍分别为c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89和0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e，与已提交skill_timings索引一致。旧研究合同只作为线索，本次重新反汇编实际方法后才修改消费者。

Attributes._GetOtherAttributeFinalScalar（method60549/RVA3548800，4000字节窗口SHA256=6e7fd3cdb9b6455f2b51c2ccba052eb77ad471ce8ac0746a1305db461f4edca6）在属性2=Atk分支调用四份GetAtkFinalScalarFromX，结果与1相加。这些方法先GetValue(39/40/41/42)，再GetValue(76/77/78/79)系数属性，保留前者、调用floor入口1b01f0，再MULSD乘后者。四份128字节窗口：

| 方法 / RVA | SHA256 |
| --- | --- |
| 60525 Str / 4260670 | 1803438d7624af3697ef2adc14e8b8b26f4021405a83cb5ca0058fb73179c90b |
| 60526 Agi / 42606f0 | a1fc96955f577b511158c8037d2c62121f68ebd1400bf66f68b21fcadb490f77 |
| 60527 Wisd / 4260570 | 158aacf98d8c3b91ad17382cbfe48c1dd1cbf57a9c196ccb3794dbe6c48420f4 |
| 60528 Will / 42605f0 | 3501c114b04c8f05854bcc852324c703f266d6c75810bf21c02b8b952c0e533c |

floor入口1b01f0（288字节窗口SHA256=1ffa80c5c79678146cf55fadd83e7b009313764bd2a80bb09ce0145e7585e110）按CPU能力选择1c2af0或标量分支。1c2af0的ROUNDSD立即数9代表向负无穷取整并抑制精度异常；32字节窗口SHA256=5d0c4e12ea81404fb7271e4bd2d4243961e9c6518c30eac8a576eb2033d9f0bd。标量分支清除小数位，对有小数的负值再减1，并非朝零截断。

GetStrAtkFactorFromMainSubAttribute（60529/RVA3f65e70，512字节窗口SHA256=dbf77fecdb69781c0220edb92114d58dd3cef6d535f2971d7c98dbcfd00bcc4f）检查主/副属性39，读取两个BattleConst getter，以ADDSS合并后CVTSS2SD。当前档案主、副属性互异。解包BattleConst.bytes SHA256=43b64dd35f3930568a446eda519a2edb9c7c4dc172a90d03ab3e52be360789d8；已解码BattleConst.json SHA256=394b8f8dc94d3c7368d06429d16a5290f55c77e4ce1722df37160103129fec5b。主/副系数保留float32原值0.004999999888241291及0.0020000000949949026。以上字节长度是校验窗口，不能当作完整方法长度；原始客户端与研究脚本继续本地忽略。

固定生成器保留四维原始总值，attribute_basis升为schema2并明确attack_conversion=floor_final_main_sub；旧连续口径不被静默接受。固定属性系数、显示ATK及全部128报价从同一合同重算。动态最终差值先按来源汇总，再比较floor(total+sum(delta))-floor(total)，刷新、各层到期和显式移除均重新求值。不会把每份小额buff单独floor，也不对delta自身floor；智识缩放增幅仍读取原始小数智识，不因攻击转换而被整数化。

与上一提交相比，只有5人显示ATK变化：安塔尔3440.2→3439.2、黎诺4697.2→4693.8、诀6843.5→6842.6、余烬3352.6→3351.3、庄方宜7377.0→7372.9；19/128报价的显示伤害变化，其他109份显示报价不变。32人的等级/潜能/配装、四维显示与原始总值、暴击、伤害/增幅桶、伤害行及其归属不变。余烬仍80级/技能9级/原推荐五星武器。旧damage_baseline.json与master编排未修改。

本批只修已确认最终四维到攻击的消费者，不实现原生四维百分比/转换producer，也不执行系数属性76～79自身的动态修改。黎风智识/意志天赋刷新及赛希FinalNonConverted智识producer仍未知；13释放绑定、2原生buff属性绑定、16行归属及2/111完整程序口径均不提高。5项新增专项和77项相关回归通过，全仓结果见进度文档。

## 验收范围

用户要求先完善数据流，不接master。证据明确的“施放某类型技能时产生加成”可以补；不好确认的条件、时点和次数暂不确认。这里只接离线模型成功释放和已确认最终面板属性变化的消费链，不宣称原生动画时点、完整技能程序或识别接管已完成。

## 已确认最终属性变化

固定生成器新增attribute_basis，保存从原生主/副属性标注和现有固定面板计算得到的四维未舍入值。原有32个panel、128份skills报价和profile逐项与父提交比较，全部保持原值。FixedDamagePanel仍持有固定攻击白值、攻击百分比、固定攻击值及固定属性系数。

TimedDamageState.apply_final_attribute_delta接收生产者已确认的最终属性差值和明确寿命；这不是原生AttributeModifier百分比/转换公式的解释器。每个actor/key/source为一个实例，重复输入刷新该实例，独立层须提供独立key。到期和显式移除只撤回各自差值，预测深拷贝和phase_signature保留这一状态。无明确时长或permanent、非有限数和错误属性域拒绝；未知amount保留未知。

最初实现按连续差值增加攻击系数；2026-10-08第六批按同版机器码修正为“原生系数×变化前后最终属性floor之差”，详见下方。仍不重乘已有属性系数、不改固定攻击值。CombatWorldState的source属性和原生属性查询读取同一有效视图，ATK读取同步更新；基础属性字典和固定面板不被改写。未转换armed/final属性使用独立键，不能由面板四维变化自动推算。

黎风固定天赋已经把智识/意志转攻击计入attack_percent，但原生转换/快照/刷新方式尚不确认。本批将这两个动态依赖写入basis；相关属性变化时拒绝估价，避免只更新主副属性系数、漏改天赋攻击加成却返回完整数值。其他属性转换、生命/防御派生及全角色原生四维生产者仍未执行。

## 已核准的释放规则

证据来自canonical技能描述及已绑定等级行、选中配装的weapons.json Rank 9描述和equipments.json套装描述。damage_release_rules只识别已逐项核准的技能/武器/套装；不以on_cast标签或“包含施放”自动批准所有规则。

| 来源 | 受益与强度来源 | 生命周期/边界 |
| --- | --- | --- |
| 安塔尔终结技 | 全队电磁、灼热增幅；沿用选中等级及潜能修正 | 12秒，同一来源刷新；模型成功终结技释放生产 |
| 赛希终结技 | 全队寒冷、自然增幅；沿用选中等级及P5修正 | 12秒，应用时快照；未转换智识缺失时保持未知 |
| J.E.T. 压制·太空物理学 | 装备者法术伤害+33.6%，分别由战技/连携释放生产 | 各15秒；两种独立、各自不叠加；当前艾维文娜、埃特拉配装 |
| 遗忘 夜幕·耻辱 | 终结释放法术伤害+67.2%；连携释放+33.6% | 各15秒，独立生效且各自不叠加；当前佩丽卡配装 |
| 熔铸火焰 夜幕·嘶鸣烈火 | 终结释放后装备者普通攻击伤害+210% | 20秒；只加normal标签；当前莱万汀配装 |
| 清波3件套 | 连携释放时装备者所有技能伤害+20% | 15秒，2层独立计时；按实际装备件数验证；当前汤汤、狼卫配装 |

当前台账共13个模型释放加成绑定：安塔尔2、赛希2、两位J.E.T.装备者共4、佩丽卡2、莱万汀1、清波两位装备者各1。这个数不是完整技能覆盖率。武器的常驻法术/元素增伤、暴击率及套装常驻词条仍只算在固定面板，条件释放段单独登记，不重复加入固定加成。清波只作用normal/skill/combo/ultimate标签，不扩展到物理异常等其他伤害。

模型生产者在CombatWorldState.start通过合法性检查并扣除费用后执行。失败释放、相同action_id重复确认和其他技能类型不生产加成；真实命中不是触发前提，也不会凭释放补造命中/场地/概率结果。该模型边界不等于实际游戏内的起手帧、释放后帧或buff创建帧。master编排及连携识别没有接入。

## 赛希输入域的原生证据

已提交的SkillTimingStore记录：buff_chr_0011_seraph_atk_buff的OnStart只有一条StoreAttributeValue，attributeType=41=Wisd、storeAttributeType=1=FinalNonConverted、targetSource=1=Source。OnStart随后按wisd_max限幅并加atk_up，再执行EnhancedAction。现有原生读取器明确区分FinalNonConverted与BaseNonConverted，不拿转换后的面板值填空。

因此已修正canonical两条终结增幅的input为source.native.final_nonconverted.41，独立报价、审计、释放模型共用这一份定义。专项从原生记录及枚举反查输入域，不仅比对手写期望。面板智识存在而该域缺失时，释放确实记录增益实例，但其强度无法估价；不得用0或面板智识替代。已知输入下的限幅、潜能修正和应用快照仍按原统一公式执行；完整原生EnhancedAction与帧级时序不在此批完成。

## 暂不确认清单

| 对象/链路 | 当前缺口与处理 |
| --- | --- |
| 全角色动态四维 | 原生AttributeModifier、百分比叠层/转换顺序、RefreshBuffAttrModifierValue及实际producer未统一执行；只有确认后的最终差值可消费 |
| 黎风四维转攻击 | 动态转换/快照/刷新不明，相关属性变化拒绝完整估价 |
| 赛希终结技 | FinalNonConverted智识生产者未知；实际buff创建/EnhancedAction时点未执行，不补默认值 |
| 庄方宜战技天赋 | 原有battle_cast定义存在，但雷击命中次数、增幅累加及重置输入未闭环；本批不把未知次数填0 |
| 秋栗P3 | 已接实际原生创建块及buff实例攻击加成，见下方续作；完整终结技能其他节点、真实中断/识别仍未完成 |
| 孤舟终结技 | 第九批已从原生OnBeforeCastSkill核准25秒/112%并接成功模型释放，见native-guzhou-release-audit.md；完整原生事件发布/父根清理仍未完成，法术异常消费分支未自动触发 |
| 碾骨、50式应龙 | 下次技能增益的层数消费、跨角色触发/指代与早期清理还需逐项确认，不当成普通持续buff |
| 赛希满血晶体、卡缪血翼、弭弗连携脆弱 | 实际治疗结果、目标/投射物附着及命中producer未闭环，不能由cast推定 |
| 洁尔佩塔、提弗洛斯、黎诺场地/姿态 | 场地实例、进出目标、提前退出及跨技能清理尚未全接；通用接口不等于实际producer |
| 伊冯暴击层、概率免疫、额外攻击 | 实际次数/条件/消费未确认，保留原缺口 |
| 识别、技力时机、master接管 | 按用户要求后置，本批不实施 |

完整原生程序仍沿用此前2/111的边界；不能把13个释放绑定、数值回放或通用属性接口算成新的完整程序。

## 验证

新增18项专项覆盖成功/失败/重复释放、技能类型、元素/对象/标签、选中配装、刷新与独立计时、清波件数、实际原生智识域、已确认属性差值、到期/早移除、未知输入、来源快照与预测隔离。相关75项回归通过；最终代码全仓1351项通过（176.604秒）。Ruff I/F、全部修改/新增Python与JSON解析、敏感标记扫描和git diff --check通过。固定产物和审计逐字节重生成一致，32个panel、128份报价及profile与父提交完全相同。

无属性变化时直接读取固定属性，武器/装备按文件版本复用解析，避免重复扫描大文件；这只减少实现中的重复工作，不能据此宣称完整搜索性能或实战验证已经达标。没有游戏现场或master接管验证。

## 2026-10-08：秋栗三潜实例加成

逐项核对chr_0019_karin_ultimate_skill原生0～150帧创建块：先检查potential_3>0，再通过CharacterTeamFinder给队员创建buff_chr_0019_karin_potential_3，继承atk，autoFinishByAction=true。BuffData是lifeType=1的永久实例，不能读取其未启用的10秒duration；实际寿命由创建节点结束或显式实例清理决定。原创建块SHA256随技能对象为f977103403fb597c5925827195a3547b08b90b7a613fc4dee2ba60ae376ba379，buff对象为6c2f613243cd0e5beb6242c60c20ba84b232197b6645a26aeafb78b45d4a3f35。三潜描述与选中参数atk=0.10000000149011612互相核对，默认四星满潜会启用，P2不会创建。

本批只批准这份确定的非转换Atk条目（attributeType=2、formulaItem=6、modifyAttributeType=0、param=atk），不把6推广成任意属性的通用解释器。原始创建块、条件、全队选择器和窗口不改；离线实例创建时快照继承值，按原生float32输入进入攻击百分比项。计算为白值×(1+固定攻击百分比+实例攻击百分比)+固定攻击值，再乘当前属性系数，避免把固定攻击值一起放大。

加成直接从活着的native_buff_instances读取，不复制成另一份持续buff；每个受益者有独立UID，节点结束、取消、显式移除、旧实例替换和预测隔离沿用已有清理。source.ATK读取同步更新；未转换armed/final输入仍不由此推算。每次技能handoff不当作加成截止，旧节点截止也不能移除新实例。缺失快照输入返回未知。其他原生属性modifier和RefreshBuffAttrModifierValue继续保持未绑定。

9项专项从真实创建块验证原生P3条件、全队/死亡队员、快照、固定攻击不重复、与确认后四维系数共用基础、节点结束/提前移除/取消/旧时间线/队友动作/预测隔离及未核准条目拒绝。原相关78项与审计22项通过；全仓结果见角色数据流进度。审计独立登记1条native_buff_attribute_damage_bindings，不改变原有13条模型释放绑定，也不将完整终结技或完整程序覆盖宣称完成。

## 2026-10-08：陈千语扶摇、黎风负山

复查当前实际固定武器Rank 9描述：扶摇夜幕·青云有常驻“战技和终结技造成的物理伤害+42%”及“对处于失衡状态的敌人造成的伤害+98%”；负山效益·山川在我有“对处于破防状态的敌人造成的伤害+56%”。原固定解析器没有识别前者的双技能/物理联合过滤，后两个条件句未进固定面板，也未进入独立运行规则。

固定配装新增damage_bonus的物理:skill和物理:ultimate联合键。元素和技能标签必须同时匹配，不扩展到法术战技、普攻、连携或物理异常；同一标签重复不重复加成。这里只由固定生成器补扶摇已核准条款，不改旧damage_baseline.json或master调度。陈千语战技非暴击/期望13667.8/15342.1→18983.1/21308.5，终结技109823.5/123276.9→125265.8/140610.8。两项仍沿用原未核准的伤害行聚合范围，修好武器加成不等于两技能完整执行。

条件部分由两条独立常驻规则按命中时目标predicate求值，不创造计时buff：扶摇查当前STATUS_STAGGER，负山查当前STACK_SHRED>0，不看已消费层数事件。CombatWorld在明确消费者之后，以实际命中目标重新读取，避免动作原目标或起手快照污染；消费清空、到期和换目标立即改变结果。独立报价没有本次目标输入时返回未知，不借用注册时的假人状态。只有装备者获益，伤害增幅进入damage_bonus加法项，不当作敌人脆弱。

数据台账单列target_state_bonus_bindings=2及陈千语fixed_weapon_bonus_filters；17份源摘要加入规则合同，其他126技能报价、全部profile、32人攻击/四维数值不变。负山触发后的全能力+22.4%仍未执行，不能将常驻目标条件闭环当作该武器动态四维完成。7项专项及相关59项通过，全仓结果见角色数据流进度。原生完整程序覆盖仍2/111。

## 2026-10-08：佩丽卡P5固定终结暴击

原生CharacterPotentialTable/chr_0004_pelica/potentialUnlockBundle/4的chr_0004_pelica_potential_5，描述为终结技暴击率+30%，参数crit=0.30000001192092896。modifier无activeCondition，modifyType=3，指定chr_0004_pelica_ultimate_skill的skillBbModifier（bbKey=crit、modifyType=1）。真实终结伤害节点读取同一crit：InstantModifyAttribute/modifyTargetSide=0，attributeType=9、formulaItem=5、modifyAttributeType=0。测试反查这些原始字段，不能以描述 alone 扩大到其他技能或普通全局暴击率。

固定面板新增crit_rate_bonus.ultimate，选中P5才写入，P4没有。每次伤害由真实标签读一次，重复标签不重复添加；与动态暴击率相加后统一截断至0～1，can_crit=false不乘期望。显示暴击19%不变，终结有效49%，非暴击55873.1不变，期望61181.0→69562.0。其余127报价、32人攻击/四维、等级及潜能档案数值不变，P5登记为固定来源。新增固定合同进入18份源摘要。

这是固定计算数据流核验；原生编译器仍拒绝DamageAction的damageProcessors，未据此解除整个终结程序诊断。未来实现InstantModifyAttribute时必须辨识已纳入固定面板的同一来源并去重。没有虚构原生暴击命中结果、回调或释放时点，也未接入master。4项专项与55项相关回归、全仓1371项通过（89.239秒），产物逐字节重生成一致，Ruff I/F、解析、敏感标记和diff检查通过。

## 2026-10-08：当前目标天赋与伊冯冰点

chr_0017_yvonne_talent_2_2选中最高阶参数inflict_up=0.20000000298023224、status_up=0.4000000059604645；attachSkill指向chr_0017_yvonne_talent_0并覆盖两参数，该Skill.buffs继续继承至buff_chr_0017_yvonne_talent_0。原生buff的三组Attacker条件：寒冷HasAny且冻结ExceptAny→inflict_up；冻结HasAny且寒冷ExceptAny→status_up；寒冷/冻结HasAll→status_up。两个tag身份分别为1cdba15d（Skill/Character/Common/SpellInflict/CrystInflict）和55af885b（Skill/Character/Common/SpellStatus/Frozen）。三个处理器均为InstantModifyAttribute、modifyTargetSide=0、attributeType=10、formulaItem=5、modifyAttributeType=0。

canonical拆为寒冷且不冻结、冻结两条互斥hit-time CRIT_DAMAGE规则，参数仍直接引用选中被动。只有自身受益，全部伤害元素按描述可匹配；仅改变暴击项。冻结单独存在和与寒冷同时存在都只加40%，没有两条相加60%，也不创造额外暴击命中/治疗事件。原生buff仍报告尚未执行的攻击方条件处理器，完整Ability和原生处理器解释留在原缺口；未来不能同一来源重复加入。

萤石chr_0022_bounda_talent_1_2和佩丽卡chr_0004_pelica_talent_1_2显式evaluation=hit；缺当前target输入不借注册时标准假人状态。CombatWorld统一读取源石结晶/缓速/失衡/破防/寒冷/冻结predicate，每次真实模型命中先完成显式消费，再从实际受击目标重读。换目标、冻结到期返回寒冷20%、消费清空、队友不受益、旧event输入被覆盖与预测隔离均验收；不代表这些状态的全部原生生产者或画面识别已完成。

本地导入输入先重生成到tmp，8份快照逐字节等同现有版本后才更新canonical规则与正式快照。补充原始对象摘要/回编码验证和原进度输入不变；选中被动规则15条仅比前版多冰点两个分支。32面板、128报价及profile数值完全不变。6项新增专项、68项相关回归及全仓1377项通过（96.806秒），原生完整覆盖仍2/111。

## 2026-10-08：阿列什P3确认结果后的实例攻击与刷新

最高选中潜能chr_0024_deepfin_potential_3明确成功钓起珍鳞后全队攻击+15%、10秒、不叠加，参数atk_up=0.15000000596046448、Duration=10。原生连携相应片段先FindTarget到team，再CreateBuff继承atk_up与大小写敏感的Duration→duration；buff_chr_0024_deepfin_potential_3是lifeType=0、duration读BB、负周期、无回调/订阅、stackingType=4，属性项与秋栗同为已核准非转换ATK百分比。buff对象SHA256=548a9a85effb17b21d86d64c6cda8b3446954cd0a0ab84a7ff4748dd7e46cdc0；原连携对象=563fd8ee269d098ce95839e7de56d0f164b668662fe599a271dfa11c347a6b03。

确认结果后的实际实例进入ATK与source.ATK消费链，不复制canonical第二份buff；重复效果不增加层，保留旧UID/来源/BB/攻击快照，仅按原生Refresh规则更新时长。旧截止、提前移除后再创建、较短新时长、约1e-5阈值和预测隔离见native-buff-lifetime-audit.md的机器码证据及专项。这里只批准阿列什这个无周期/回调/根绑定实例；其他类型4和陈千语/佩丽卡的类型8不据此放行。

珍鳞结果的随机生产与完整连携仍未执行，绝不把每次cast当作钓起珍鳞。审计单独写明buff_instance_ATK_and_Refresh_only及rare_fish_outcome_and_full_combo_not_bound；原生buff属性绑定数1→2仅是两种已核准属性条目，不能当完整producer数。32面板、128报价数值未变，程序树复测仍2/111。7项新专项及44项相关回归通过，完整仓库验证见数据流进度。
