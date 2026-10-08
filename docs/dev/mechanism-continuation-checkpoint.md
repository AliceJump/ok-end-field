# 机制模拟续接检查点（2026-10-05）

> 2026-10-08最新授权已改为实际接入释放确定增益：TimedCombatLogic的step调用ReleaseBurstPlanner，基于当前可用终结/战技、共享SP和实际乘区收益选爆发起手并调用按键；不依赖全原生诊断闭合，不新增命中识别。7角色8条来源绑定：安塔尔双增幅、秋栗P3动作窗口攻击、自身遗忘/孤舟/熔铸火焰及两份J.E.T.战技；仅接受释放后提交、刷新/到期/过滤/预测隔离保留。当前主控基础普攻只作报价尾项，不推定命中或强化倍率；已确认最终四维差值消费可更新系数，但未知四维producer未补。12新增、最后相关100项/全仓1449项通过（7.821/111.173秒），32角色初始化核验8绑定/7角色；原32/128、14/2、2/111不变。旧“只统计/不接编排”指令已被本次用户授权替代；不向master推送，不新开PR，连携/技力时机不改。详见release-burst-integration.md；所有可用队员进入候选，避免常规子集排除受益角色。未做游戏实战，保留数据缺口和automation-2。

> 2026-10-08用户收敛为终结开启的团队爆发增伤：buff-integration-inventory.md/json优先列全队增伤/增幅/攻击及敌方脆弱。安塔尔2条团队释放候选；赛希、梨诺、洁尔佩塔、秋栗、艾维文娜、噗切娜10条团队待补记录（9条不同来源规则），保留属性/光环/场地/动作结束/命中条件。个人窗口另列莱万汀、佩丽卡、庄方宜3条候选及伊冯2条待补。全部为现有62条的子集，不能再相加；洁尔佩塔同源分支不叠两份。原7条释放候选拆为5条终结优先、2条战技后置；6目标条件、2原生消费者、5连携暂缓、2属性输入暂缺、40生产链待验收的边界保留。终结有个人能量/动画成本，后续战技仍计公共技力；不以完整原生模拟或其他后续为首批前置条件。仅统计/文档，不接master编排/识别、不增加执行能力；保留automation-2并更新优先范围。

> 2026-10-08第十六批：同版Buff.Reset在2db151c明确初始化m_enhanceCnt=1，普通_Enhance先加一→buff event6→属性重载→MarkAttributesDirty。初值与回调次序加入逐角色原生候选，不当追加mark计数，也不向当前模型填默认最终值。模板创建/中间回调/优先级组/转换与父子清理仍未执行；32/128、14/2及完整程序2/111不变。相关28项通过；最近全仓仍同步master后的1436项。详见native-zhuangfy-release-audit.md，继续数据流，automation-2保留。

> 2026-10-08第十五批：Pulse模板属性66/PulseEnhancedDmgIncrease在root、动态rate、默认child无属性、rate优先级及实际BB重载路径已核准。Buff+a8是m_enhanceCnt，不能当追加mark数；LoadAttributesModifier先GetFloat、计数转float32、MULSS、转double，返回mask后MarkAttributesDirty。候选台账单列属性刷新证据；初始化/优先级组/转换缓存/父子通知与外部分支继续未知，不启用收益或默认计数。方法证据新增固定构建域核验，拒绝索引与快照自洽地换成未复核的新构建。1新增及相关28项通过（8.513秒），最后全仓仍第十四批同步master后的1436项；纯证据不重复无关全仓。32/128、14/2和2/111不变，automation-2保留，下一步继续真实增强计数/缓存和父子清理。

> 2026-10-08第十四批：仅已确认的庄方宜普通/姿态/区域原始片段创建plain self标记后，发布OnAddedBuff→OnOutputBuff；真实满级天赋监听器创建5秒基础实例。替换/旧截止/到期/提前移除/预测隔离及追加mark不误刷新均验证。前置回调/改定义/未经核准producer保留未知，完整门槛、castInfo、EnhancedAction/计数/属性传播与父子清理仍未绑定，不供应0或最大mark数。逐角色/四技能台账单列受限notification绑定，14/2与2/111不提高，32面板/128报价不变。70项相关及全仓1421项通过（109.905秒），最后失败动作专项后9项通过。原生后续调用已精确定位为RefreshPriority→OnBlackboardValueChange，下游43b0ca0待核准。见native-zhuangfy-release-audit.md；automation-2保留。

> 2026-10-08第十三批：核准Enhanced=3/Pulse=6模板literal及overrideChildBuffId=false分支，实际走Pulse模板/默认child，快照8→10份。原生mark增强先GetFloat(rate)、每次ADDSS后写回，天地造化幅度改为native_float32_repeated_add，输入source.zhuangfy_talent1_marks，不由旧hit数填充；其他linear规则不变。缺计数/无效计数保持未知，真实发布/模板实例传播/通知和清理仍未执行。4项新增、77项相关及全仓1416项通过（169.727秒），最后模板/子对象证据补齐后21项通过。32面板/128报价/profile数值不变，只更新来源；14/2与2/111不提高。下一步继续AddedBuff上下文/实际marker发布与EnhancedAction；tmp/keyword-full-58746.txt、keyword-apply-56570/56571.txt、keyword-rva-4e23096.txt、keyword-literals-proof.log保留同版证据。原生循环还调用属性刷新/通知，不能因幅度消费者改好宣称生产闭合。四维百分比、黎风/赛希producer及其他队伍台账待办仍在，automation-2保留。

> 2026-10-08第十二批：庄方宜天地造化八份原生记录按当前VFS/回编码核准，满级18%/2%/5秒覆盖原始零BB。普通/姿态战技基础mark在6/5帧，需战斗或smart_target门槛；区域技能12～64帧tick及69帧末段另产追加mark，不能按伤害次数/最大剑数填充。OnAddedBuff→基础Stack/max1→OnEnable EnhancedAction链及每技能台账已补；原生AddBuff的接收/来源通知、空新结果分支也定位，完整发布/增强/清理继续未知。本批未增加执行绑定，32/128、14/2及2/111不变。3项新增、37项相关通过（13.264秒）。见native-zhuangfy-release-audit.md；本工作树tmp/buff-add-60779.txt、buff-stack-60757.txt、keyword-58746.txt保留当前机器码；KeywordAction窗口尚未覆盖完整主函数，不能据已见局部或overrideChildBuffId闲置字符串认定实际子buff。继续AddBuff上下文及EnhancedAction；原生四维百分比/转换、黎风/赛希producer仍缺。未重复全仓，最近为第十批1406项。automation-2保留。

> 2026-10-08第十一批：碾骨/50式应龙的原生套装等级、装备名称及九份父/检测/pending/伤害buff核准；仅艾维文娜4件与大潘/萤石各3件启用，三位碾骨散件不启用。30号施放事件先积层，下次指定技能snapshot→乘实际层数→创建同cast buff→消费全部pending。SkillAffix实体引用可超过动作结束，未用普通定时buff替代。逐角色/24技能（含散件未激活）已登记来源、生产/消费类型及未执行项。3项新增与37项相关通过；32面板/128报价、14/2条绑定和2/111完整程序不变。见[native-next-skill-set-audit.md](native-next-skill-set-audit.md)。

> 2026-10-08第十批：取得原始AttributeMetaTable十行及逐字段字节证据，确认四维raw default=0且范围0～100000，57/58和76～79默认0但边界开关关闭。重新核准FieldMeta→CreateDefault→CreateFrom/Reset初始化，逐角色审计检查固定四维范围，raw default不填入当前armed/final。百分比组件的原生加乘顺序已定位，实际基数/转换/缓存producer仍待办；不启用负山、不消除黎风/赛希未知。32面板/128报价及14/2条绑定不变，完整程序仍2/111。见[native-attribute-components-audit.md](native-attribute-components-audit.md)。3项新增、20项专项及全仓1406项通过（176.013秒）。

> 2026-10-08第九批：孤舟三份原生记录与当前VFS/字节往返核准，OnBeforeCastSkill=30检查终结类型7后创建25秒/112%战技电磁伤害buff，纠正“后”不一定cast finish。已登记离线成功释放、过滤/刷新/早移除/到期与fork，13→14条释放绑定；固定44.8%不重复，32面板/128报价不变。消耗法术异常56%/20秒/2层及0.1秒marker仍需真实消费producer，不按连携名字触发；完整原生事件/父根/命中仍未完成，覆盖复测2/111。4项新增、53项相关及全仓1403项通过（92.556秒）。读native-guzhou-release-audit.md。下一步继续四维百分比组件；tmp/native_attribute_components.py和disassembly保留当前机器码研究，不能只凭旧合同套原生求值；AttributeMetaTable条目已有但初始化/实际值待确认。automation-2继续保留。

> 2026-10-08第八批：补当前VFS装备/技能表及5569份字节往返验证记录；负山wpn_lance_0012→sk_wpn_lance_0012→两份四维BaseMultiplier属性buff已追到Rank9，0.2240000069141388/15秒覆盖原文件旧默认。监听206=OnBeforeOutputBuff、检查原始NormalSkill=2，两个独立tag/marker；不按cast或已成功施加触发。只增加证据候选与黎风四技能台账，实际事件、百分比基数、Refresh回调/未知max_stack、黎风转换刷新未完成。完整程序仍2/111，32面板/128报价、13条释放和2条实例绑定不变。3项新增、17项相关及全仓1399项通过（91.907秒）。下一步读native-weapon-attribute-audit.md；新资源及研究输出在本工作树tmp，不修改主工作区。automation-2继续保留。

> 2026-10-08第七批：原生伤害标记条件0/1/2已接明确事件输入消费者；3/4未知，余烬mode3诊断仍保留。陈千语天赋三份独立mask（战技256/终结512/连携8192）及佩丽卡P3 OnOutputBuff=102/Conduct来源已定位，原始父/子、参数、对象和8个技能核查写入台账。仅候选，增强/刷新实例、事件发布与通知仍未执行；完整程序复测2/111。6+1新专项、相关49及最终28、全仓1396项通过（94.008秒）。下一步读native-enhanced-attack-audit.md，不按cast、技能名或最大层数补触发。

> 2026-10-08第六批：四维转攻击的当前原生floor顺序及BattleConst单精度主副系数已重新反汇编核验。固定与动态消费者统一，basis为schema2/floor_final_main_sub；保留四维小数，增幅缩放输入不被取整。5人显示ATK、19份基础报价改变，profile/四维/其他乘区不变。并非原生百分比/转换producer或系数属性动态生产完成；黎风与赛希缺口保留。详细证据见damage-attribute-release-audit.md。原生buff增强重算及真实命中producer继续待办，完整程序仍2/111。

> 第六批验证：5项新增及77项属性/伤害相关、29项TimingDps专项、最终全仓1389项通过（120.954秒）；固定产物/审计重生成、解析、Ruff I/F、敏感标记及diff检查通过。余烬旧攻击与战技基准断言已按新合同更新，不改变80级/R9/五星武器口径。

> 2026-10-08第五批：阿列什P3已批准确认珍鳞后创建片段的ATK实例消费及无周期/回调/动作根的Refresh=4清理。原生short枚举和时长刷新机器码见native-buff-lifetime-audit.md；并非每次连携触发，真实珍鳞结果producer仍待办。陈千语/佩丽卡EnhanceAndRefresh=8为旧实例增强并刷新，不能套独立计时层，属性重算与命中/导电producer继续核验。32面板/128报价不变，程序树复测仍2/111。

> 2026-10-08第四批：伊冯冰点目标条件暴伤两条互斥分支，萤石缓速/佩丽卡失衡hit求值和全目标predicate刷新已核验。原生寒冷/冻结TagQuery三条分支确认同时存在时只加40%，不推定真实暴击回调。全仓1377项通过，选中被动规则15条，32面板/128报价数值不变。原生Ability/条件处理器和完整程序仍未闭环。

> 2026-10-08第三批补佩丽卡P5固定终结暴击过滤，选中潜能→固定类型项→逐命中暴击桶→独立报价已连接。全局暴击不提升，只有终结期望改变。18份源摘要，127份其他报价和32人攻击/四维不变。该原生InstantModifyAttribute仍未执行，未来处理器实现须避免固定项重复，完整覆盖仍2/111。

> 2026-10-08续作已补秋栗三潜原生buff实例的攻击加成消费链，真实创建块条件/全队目标/继承值和节点截止保留，不用handoff或buff未启用的10秒时长。单独审计1条native buff属性规则；全角色四维producer和庄方宜追加计数/EnhancedAction等仍未闭环。继续用户的数据流范围，证据见damage-attribute-release-audit.md；完整原生覆盖仍2/111。

> 同日第二批核准扶摇常驻物理战技/终结42%与扶摇失衡目标98%、负山破防目标56%。前者归固定面板元素/技能联合过滤，只改变陈千语两份报价；后两者按实际命中目标的当前状态计算，消费破防后不看消费事件维持增益。负山动态四维继续未知。审计17份来源、2条target_state_bonus_bindings，其他126报价/全部profile及32人攻击/四维数值不变。

> 2026-10-07用户最新要求：先不接入master，先完善数据流，并按角色/每个技能核查完整性。优先动态四维属性与加成事件/次数/清理，明确释放技能即产生的加成可以补，不好确认的暂不确认。automation-2已更新范围并恢复五小时续作。最新入口为[角色与技能数据流核查](damage-data-flow-progress.md)，覆盖下方旧续作顺序及[加成接入计划](damage-bonus-master-integration-plan.md)。32角色/128技能已生成独立审计，基础报价可重算但不等于语义完成；第一队16技能的倍率行归属已核准，其中5项基础/替换/条件倍率混算按明确来源拆分，事件生产、天赋/潜能和剩余语义继续核查。

> 本轮已修正被动归属，并接通确认后的最终四维差值消费/清理和13个模型释放加成绑定。赛希终结增幅改读原生FinalNonConverted智识，缺失不借用面板值；其他未知转换/次数/时点继续暂不确认。证据及逐项清单见[动态属性与释放加成核验](damage-attribute-release-audit.md)。固定面板/报价未变，完整原生覆盖仍2/111。

分支：`codex/effect-semantics-normalization`。用户要求阶段提交并推送，不新开 PR；master 已同步到 `ad9d1afe`。本次按用户额度提醒保存进度，不表示完整机制已经完成。技力返还时机和 HUD 阈值识别继续后置。

## 已接入

- `8418d449`：TimedCombatLogic 可尝试有界机制搜索。只接受完整、可估价且立即合法的战技首动作；未解析机制、过期观测、搜索超时/预算不足都回退旧调度。未做游戏现场验证。
- `693de6e9`：原生 StoreAttributeValue，区分 armed/final 的 non-converted 属性与实际来源/持有者，支持原生 float32 输入及 floor 顺序。属性 57/58 未提取到默认值，基准面板不会虚构为零。
- 本次：导电 buff 的 Defender / NormalCalcZone 伤害处理器真正参与后续命中。按实际受击目标、元素与各 buff 独立 BB 读取值；同侧相加，攻击方与受击方相乘，合并乘区非负截断。到期、移除、共享组替换和预测隔离沿用已有实例生命周期。
- 所有 buff 定义入口统一报告尚未执行的属性/治疗/其他处理器，避免 SpellInfliction 或被动入口遗漏诊断。支持没有生命周期动作、只有处理器的 buff。

## 原生证据

`DamageScaleProcessorConfig.asset` 已补解包并校验原对象与回编码字节一致；仓库只保存已验证数据及摘要，客户端路径、密钥和临时研究文件不提交。对象 SHA256 `595b29f2408a22333eb73a02b9e33fc83a8e17414e69446d27f4f0439ae881c4`，树摘要 `9b37295e36af7358ff46f68b789784adae3d15ec28c3e032abebfb2598f59ee3`，来源信息见 common_mechanics 的 index.json。资产总数从 340 增至 341。

两份 Defender 枚举均保留 metadata 的字段编号、默认值偏移与原始压缩字节。DamageScaleProcessor.ProcessDamagePackDataInternal 方法60962（RVA `0x40132b0`，6000字节 SHA256 `ea630f94d460b32e78eb1385b35509f0cb5e60600f5deb93bb1f9131de74d13d`）先读取 BuffBB 的 float，再转换 double 调用 ModifyDamageScaleZone。

方法60928（RVA `0x4013470`，6000字节 SHA256 `ec9b4c71baaf7cb6eb320f50f420554e5fc103a6eb61582e5d98fb79101475cf`）按配置的 isMultiplyZone 决定加法或乘法；Defender 加法分支 `0x4bc0906`/70字节 SHA256 `51af36d44d04d0392441728ef5de94ec91446cf7727225fd8147022079f4330f` 跳回同一 addsd 路径，读取 Defender 数组。构造方法60927（RVA `0x3200850`，6000字节 SHA256 `fa6fcccadcc6e92da3538b49efc63fa53aa42f5fbc26dcb17fe614febce15d54`）将两侧乘区初值填为1。

方法60931（RVA `0x3549290`，6000字节 SHA256 `1809eea9b7f0988568809a45e1ce9a78b3e7988ceabf6306500b140c7f4a4f87`）在 NormalCalcZone 增加攻击方属性项后继续读取 Defender 数组，乘两侧并截断为非负。CheckDamageType.ExecuteInternal 方法91484（RVA `0x649ff8c`，6000字节 SHA256 `97e60c27528b719e1f86bad6319e799684e37d48f52cd8749a8a7888ba897fde`）读取事件伤害包的元素。

导电生产链先创建效果再执行初始伤害，因此初始命中也经过刚创建的导电乘区；缺少属性57时，不再允许这次法术伤害绕过缺失输入继续估价。专项测试显式提供场景属性0，不修改正式角色基准。

## 下次从这里继续

用户最新范围：一个队伍一个队伍补数据类可获得证据的缺口，实际识别类不要补。先读[逐队台账](mechanism-team-audit.md)，当前第一队弭弗/骏卫/余烬/卡缪；本批已补卡缪OnConsumeBuff/OnAbsorbBuff的高级事件标签判断，目录诊断3→1，仍有余烬DamageDecorateMask和本队动作/数据生产链缺口。真实事件判断规则与其生产/分派分别验收，不增加画面识别、逐击检测、角色阶段检测、HUD阈值或gap恢复。全仓1313项通过，覆盖仍2/111，第一队未完成。

随后补第一队共享CharacterTeamFinder+MainCharacterValidator选择规则：活着的队员中选当时main_control，空/死亡/非成员主控不回退施法者；其他过滤与空间点未知保持。四个基础战技静态原因12/5/8/5→11/4/7/4；7项新增专项及43项相关回归通过，程序树覆盖仍2/111。接下来继续余烬受伤条件、共享输入/控制流和各人的buff/实体依赖；只补有证据的模型数据与执行，不实施实际识别。

最新续作顺序覆盖下方原生节点研究断点：先读[实际阻塞诊断与计划](mechanism-blocker-plan.md)，从代表队诊断/生产者消费者依赖推进，接着闭合真实动作和连续观测，再验证收益/预算并推广全角色。f6ccd531下只读复测32个单角色开场没有完整可用战技，四人队先被3条连携条件目录诊断、再被战技缺口挡住；仅7次失败探测的约13ms不能当完整搜索预算验收。真实CastEnd/Disable、控制流等缺口保留，按队伍依赖优先级补，不再把单个接口或解码当作接管里程碑。

2026-10-07最新入口：先读mechanism-flow-progress.md和native-buff-lifetime-audit.md的最新续作段。已支持动作窗口清理、Buff父子含Unique首次父根、持久Ability子挂接及显式确认停用后的子孙/旧时间线/订阅清理。槽映射替换/恢复不会直接停用旧Ability；不能把本次cast或handoff当作根结束。完整程序覆盖仍2/111。

最新续作已接入选中被动Skill的Enable期SkillData.buffs独立名单、当前BB重新挂载及_FinishPassiveBuff按UID清理，真实余烬名单诊断在此范围内关闭。全仓1284项通过，完整程序仍2/111。下一批继续定位战斗里的Skill.Disable生产者，补Skill.m_buffsDuringSkill和真实CastEnd原因/目标技能的继承释放链；普通主动Skill的附带buff、完整Enable副作用也仍未覆盖。以下属性/反应/伤害处理器缺口也继续保留；ComboCache、Curve、Interrupt、Jump、目标、投射物/场地实体、搜索完整预算及现场验证均未完成。仅阶段性提交，不删除本会话续作automation-2，不新开PR或推送master。

继续新增Skill挂接名单及明确CastEnd输入下的跨技能转交。编译器保留非周期可执行实例的原始继承参数；真实弭弗comboprocess回归通过。Skill.CastEnd只清理时间线结束前复制的旧名单，不能清空新自继承实例。真实结束原因/目标/时点和目标activeSkillMap对象的生产者仍未接入，严格排轴保持诊断，覆盖仍2/111。已定位_DetachSkillInternal→Skill.Disable/Remove，下一步追上层；普通Skill.Disable名单清理仍待接入。CurveEvaluateFloat资源也已开始核验：必须区分UnityEngine.Keyframe的28字节布局与Beyond.FKeyframe的32字节布局，不能套用研究包已有FAnimationCurve合同；未验证求值算法前继续未知。

最新已推送effd1c3b，Enable名单894ddb42在其祖先。之后完成Curve结构解码/回编码，225/225节点、53份payload逐字节往返通过；读数据的脚本是scripts/skill-data/audit_native_curve_data.py。详见native-control-flow-audit.md的Curve续作段。下一步仍需Unity Evaluate运行时算法（包含无穷切线/加权/边界），旧输出GetFloat和float差值阈值、全部消费者；仅有结构数据不增加执行覆盖。所有未闭环事项及automation-2保留，不因用户询问进度或阶段保存而视为完成。

1. 补属性57/58的实际默认值/生产者来源，不能把未知数据填0。
2. 冰冻的 child buff 寿命、碎冰触发及物理伤害增量；先定位实际子buff，再执行完整归属/销毁链。
3. 腐蚀的 AttributeModifier、RefreshBuffAttrModifierValue、独立 tick 与减抗上限；燃烧攻击快照、子buff和持续触发。
4. OnSpellAbnormalStartFinish、附着增强、免疫及实际不同来源的反应回调仍有诊断，尚不允许完整反应估价。
5. 其他原生攻击方/乘区/复杂条件处理器继续逐项验证；防止与已有 canonical 天赋和装备面板重复叠加。
6. 消除真实队伍的未解析机制后再扩大搜索覆盖。当前多数真实队伍仍回退旧调度，不能报告整套机制已全面投入战斗。

本地外部研究目录保留 DamageScaleProcessorConfig 的解析树、原始/回编码对象、bundle 绑定证据、枚举及方法反汇编；新增研究脚本和日志保持忽略。续接先读本检查点和 native-combat-execution-audit，再检查远程分支与 master 差异。

本阶段全仓1137项测试通过（59.822秒）；修改文件的Python/JSON解析、敏感路径检查、Ruff I/F与git diff --check通过。新增8项伤害处理器专项覆盖真实导电生产链、元素/目标、同区加法、过期/共享替换、独立BB、预测隔离和缺失输入拒绝。未进行游戏现场验证。
