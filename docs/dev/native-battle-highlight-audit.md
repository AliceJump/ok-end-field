# 战技白圈与原生高亮证据（2026-10-09）

白圈对应的技能推荐高亮是多角色共用机制。当前核对了快照中32个角色名、33个原生角色ID（管理员男女各一份）、36份战技入口及已知替换记录，其中13个角色名有非空的 `skillHighlightCondition.actionData`。这不是“仅13位会出现任何白圈”的结论：这里只统计上述战技记录的自定义高亮条件，不推定其他UI提示、未选变体或后续版本。

## 数据与原生调用链

技能记录直接读取本仓库 `assets/data/skill_timings/20261002/records.json.gz`，通过 `load_skill_timings().record(skill_id)` 保留原字段；入口用 `profiles(character_id, "battle")`，加 `battle_phase_profiles(character_id)` 和存在的 `normal_skill_ult` 等已知替换记录。没有按伤害、消耗、角色名猜推荐条件。

检查本地 GameAssembly 与 metadata 完整SHA256与快照 `index.json.native_inputs` 一致后，解析 IL2CPP 方法表、原生字段偏移和指令。确认的主执行块如下；大小及SHA256限定实际检查的连续块，不代表已核准全部跳出块或 IFix 热补丁。

| 方法 | RVA | 检查块字节数 | SHA256 |
| --- | --- | --- | --- |
| Skill._InitHighlightConditionAction | 0x3735fb0 | 167 | 149bdf19b38fed3ca03752e58d432f8f1ca76020caed759c92526bd45078cbde |
| Skill.IsHighlightConditionMet | 0x2ddb380 | 2390 | 19786c62606b89b5244cb1f79627531c53a8723e54a57cd4bf9425c3cc104cc0 |
| SkillButton._UpdateMainUI | 0x2ddc000 | 330 | 889529f9a2c4e48839c8c440f6f13b1c463aa1e0cd3d14429be5e2e752ddb550 |
| SkillButton._CheckNormalSkillHighlight | 0x2ddb210 | 358 | b26151c8a8e4b3390be3db49636d80e9a1374ed42043aabbb604769e8695e61c |
| CheckAllowNormalSkillHighlight._CheckAtbValue | 0x649e80c | 107 | 0e2933064e70b80c9c7a0652358c0e9dd36d942c86aaf47b42371ffe09d12770 |

`_InitHighlightConditionAction` 从 SkillData+0xF0 读取配置，建立 SkillHighlightEnvironment 和 SequenceAction。`IsHighlightConditionMet` 会取得战斗目标、供应 `highlight_smart_target` 相关目标并执行条件序列，而不是只根据“按钮有技能”返回高亮。

`_UpdateMainUI` 先刷新战技CD，再按检查间隔调用 `_CheckNormalSkillHighlight`。后者调用 `IsHighlightConditionMet`，把返回值与 SkillButton+0x2B1 的 `m_normalSkillReady` 一起检查，满足时显示 +0x128 的 `strengLight` 对象，否则走关闭分支。`CheckAllowNormalSkillHighlight` 另检查当前技能中断许可；其 `checkAtbValue=true` 时 `_CheckAtbValue` 最终调用 `Skill.CheckCost`。因此高亮包含推荐条件与当前按钮就绪信息，不能把所有角色的高亮都当成相同的一条buff。

原生证据证明了共用高亮显示条件，也证明条件或就绪状态失效可撤掉高亮；它不证明“消失的唯一原因是释放成功”。本批没有从UI材质/动画资源核准每个像素的白色、脉冲周期或实际视频时延。白色检测沿用仓库现有 `RecommendSkillDetector`，庄方宜按维护者明确指定的“按键后白色消失即确认”运行合同执行。

## 触发条件与反向观察

所有下表条件还要与按钮就绪 `B = m_normalSkillReady` 同时成立。表中 `A` 指记录实际包含的 `CheckAllowNormalSkillHighlight(checkAtbValue=true)`，检查当前技能中断许可和 `Skill.CheckCost`；没有 `A` 的记录不能补一条同样的检查。`T` 指游戏为这个按钮计算的 `highlight_smart_target`，下文称“推荐目标”。它不能直接等同任意敌人或全场敌人；伊冯另有显式主目标相等检查。

表中“亮起可反推”限定有效白圈观测对应上述原生高亮、当前角色及按钮变体。它表示条件通过，不表示技能已经命中。

| 角色 / 战技记录 | 自定义触发条件（另需 B） | 亮起可反推 | 不能据此反推 |
| --- | --- | --- | --- |
| 诀 / normal_skill | T 的 `combo_skill_seal2` 计数 ≥1，并且 `EntityBB_wisd_greater_will` ≥1 | 推荐目标有 seal2 标记；智识≥意志分支标记成立 | 所有囹圄阶段都等同 seal2、属性具体数值、返技力已发生 |
| 狼卫 / normal_skill | A，并且 T 有燃烧**或**导电 | 至少一种异常存在 | 究竟哪一种、各有几层 |
| 弭弗 / normalskill_3（开天） | A | 当前开天按钮通过中断/费用检查 | 高亮本身没有“破防≥3”条件；不能仅凭白圈反推破防层数。首、次段配置为空 |
| 弧光 / normal_skill | A，并且 T 有导电 | 推荐目标有导电 | 导电等级、全场敌人都导电 |
| 赛希 / normal_skill | A、本人 `talent_1_atb` 计数 ≥1、队伍在战斗中 | 本人天赋入战标记存在，队伍处于战斗 | 满血、晶体存在、治疗次数、技力已返还 |
| 艾维文娜 / normal_skill | 找到自身生成的 Lance 标签实体组，数量 ≥1，Owner 到该组的原生距离检查 ≤50 | 有符合标签的本人雷枪实体，距离检查通过 | 普通/强雷枪各几支、每支都在范围内、雷枪将命中敌人 |
| 伊冯 / normal_skill | A，T 有寒冷附着**或**自然附着，主目标与 T 相等 | 主目标与推荐目标相同，且至少一种附着存在 | 哪一种、附着层数、冻结、暴击层数 |
| 艾尔黛拉 / normal_skill | A，并且 T 有腐蚀 | 推荐目标有腐蚀 | 普通自然附着也一定满足、脆弱已产生 |
| 汤汤 / normal_skill | A，本人 `water` buff计数 ≥2，自身生成的 ComboSkillWater 标签实体组数量 ≥1，Owner 到该组的原生距离检查 ≤50 | 本人 water 计数达到阈值，符合标签的涡流实体存在且距离检查通过 | 总涡流恰好2个、每个都在范围内、所有实体将命中、资源返还已发生 |
| 莱万汀 / normal_skill | A，并且本人 `energy` 计数 ≥4 | 熔火至少4层，强化阈值已满足 | 超过阈值的精确计数、770%追加攻击已执行 |
| 秋栗 / normal_skill | 本人 `karin_ult` 计数 ≥1 | 全力协作终结状态标记存在 | 额外攻击加成已生效、实际返技力时点 |
| 阿列什 / normal_skill | A，并且 T 有寒冷附着 | 推荐目标有寒冷附着 | 冻结、附着层数 |
| 庄方宜 / normal_skill、normal_skill_ult | T 的导电标签buff计数 ≥1，**或**本人 `ult_skill_free` 计数 ≥1 | “推荐目标有导电 / 本人有免费资格”至少一项成立 | 青霆剑存在或数量、是哪一项触发、本人免费额度的精确次数 |

这些是推荐高亮的条件，不能把“没白圈”自动改为全角色不能放战技。现阶段只有庄方宜依维护者明确授权将它作为执行门槛。

### 标签、查询方式和布尔关系的来源

标签名称使用干净研究基准 `cc9b471b50cdd6bc8b3a148b4eca93deef4efa02` 中 `common_mechanics/20261003` 的哈希校验资产和枚举，通过 `src/data/native_tags.py` 将原始4字节小端ID与名称的UTF-8 CRC32对应，不按描述猜标签。GameplayTagConfig 的解包对象SHA256为 `f87d9a4dcd12198eb1ff61805a6ff39cccd6a268f0130e6e72f627b7a03fc3e2`，所在bundle SHA256为 `10554ca18e3892789583e2476bda4d9359cc684f7a8c499b551331b48ff031d3`。本次未将研究运行时或公共资源包导入接入层。

| 原始标签字节 | 原生名称（共同前缀 Skill/Character/） | 含义 |
| --- | --- | --- |
| 9648d5bd | Common/SpellStatus/Burning | 燃烧 |
| bf9d6e57 | Common/SpellStatus/Conduct | 导电 |
| 1cdba15d | Common/SpellInflict/CrystInflict | 寒冷附着 |
| a7edd8ab | Common/SpellInflict/NaturalInflict | 自然附着 |
| edaee3e6 | Common/SpellStatus/Corrupt | 腐蚀 |
| 217140df | chr_0012_avywen/Lance | 艾维文娜雷枪实体标签 |
| b470b002 | chr_0027_tangtang/ComboSkillWater | 汤汤涡流实体标签 |

`GameplayTagQuery.QueryType=0` 是 `HasAny`，所以狼卫及伊冯的双标签为“或”。同版原生metadata确认 `BuffFindSettings.CheckType=0` 是 Id、1 是 Tag；`CheckBuffStackNumAdvanced.Data.compareType` 的类型是 `Beyond.CompareType`，3为 GE。此处序列的普通检查均启用、没有反转下一检查节点；`SequenceAction.Execute` 中任一普通检查失败即返回 false，全部通过才返回 true。`OrConditionAction` 逐分支执行，任一 true 即通过。

| 方法 | RVA | 检查块字节数 | SHA256 |
| --- | --- | --- | --- |
| SequenceAction.Execute | 0x30fe070 | 829 | b0633e5588c2708ec25718c851d95a6cf6d4cf9b2d3719b18ec6a1e96cea882c |
| SequenceAction.ExecuteInstant | 0x30fe3c0 | 691 | c363771c39df7675338d091e3200c2981fb29958c4dc4cc3ba01be86a05792f6 |
| OrConditionAction.ExecuteInternal | 0x64aead0 | 181 | 5087f6c117195f6fcbcce8bac0b3ec63a834e9096fe199af04b34441584d262a |
| CheckBuffStackNumAdvanced.ExecuteInternal | 0x30ffa60 | 1419 | cf94129a5d876765529c9f3e18e15933cb28538350faf10566ff4087e5f94fa2 |
| CheckDistanceCondition.ExecuteInternal | 0x42679f0 | 380 | ed90b149378f3e7536fe988ba965b74a8500b8d8129a42725af5aea37179a3a3 |

距离配置为50、`lessThan=true`，原生 `comiss / setae` 路径含等号；没有把原生单位称为米。两份配置的 source 为 Owner，target 分别为 Context/Lances、Context/water，且 `includeTargetRadius=false`、`containsHittableObj=false`。实体组的位置选择仍未闭环，因此只确认距离检查通过，不推广为组内每个实体都在50以内。

诀的 `chr_0032_lizhiyan_passive` 在 `OnCharDeckAttrChanged=155` 比较智识与意志，≥时写标记1、否则写0；`buff_chr_0032_lizhiyan_passive` 的 OnAddedBuff 做同样初始化。原始记录SHA256分别为 `12906c7396d39a3187443dedccf1824cddeeca41cd39fbcadedc1e6a33d0513d`、`eaa67d73fa53004e8d908febd49c157801db1ba59a021e9f95cb38afff9539cb`。不能因为黑板key名含 greater 就误译为严格大于。

赛希 `buff_chr_0011_seraph_talent_1` 在 `OnEnterFight=6` 且参数 `exist>0` 时为本人创建 `talent_1_atb` 标记，原始记录SHA256为 `7d3b1ceb310196747f2cfe2ec78379e5c7a39f744e55e44a74203dc498e38e15`。本批没有核准所选天赋参数如何启用 exist 或完整移除链；因此反推限定标记存在，不补满血/晶体条件，不把未启用的时长字面量当计时。

## 庄方宜两种战技

两份完整原始技能记录的消费字节数等于源字节数，快照解析的高亮配置相等：

| 技能ID | 原始字节数 | 原始SHA256 |
| --- | --- | --- |
| chr_0030_zhuangfy_normal_skill | 23078 | 06a61a8b2630fbe792294c3a0dc0a9a24080d8c8be45cb5895d2f16f0f05a9fd |
| chr_0030_zhuangfy_normal_skill_ult | 23334 | 33483a4c247260eb69cdd90ab80a604c113fb8e57a79817d3a39f54dbf971ccf |

两分支均用 CheckBuffStackNumAdvanced，阈值为显式1，compareType=3（GE）。第一分支 buffSettings.checkType=1，同时保留 have_sword ID和标签bf9d6e57，checkTarget.targetSource=2、targetGroupKey=highlight_smart_target。原生按 checkType 分支：0循环ID并调用 `BuffContainer.GetBuffCountById`，1读取标签查询并调用 `AbilitySystem.GetBuffCountByTag`（0x30ffd34）；所以这里实际检查导电标签，残留 have_sword ID不参与。Advanced检查读取目标视图的首个目标，不将计数求和到全场敌人。第二分支checkType=0，ID为 buff_chr_0030_zhuangfy_ult_skill_free，targetSource=1。

由此，两种战技的白圈都只能直接推出“推荐目标导电计数≥1，或本人免费资格计数≥1”。若另有可靠观察证明免费资格不存在，白圈才可进一步推出推荐目标有导电；若免费资格已存在，亮起不提供导电信息。不能用“姿态中应该免费”或“已经按过一次”自行补出另一分支为 false，再伪造目标状态。

## 如何交给排轴使用

维护者的决策口径是：在采用白圈门槛的机制动作上，亮起才评价对应强化候选的收益，消失就暂时撤下该候选；即使底层buff仍在，当前不能按这个门槛触发，也无需继续追查它是否消失。这里记录的是“当前机制候选不可用”，无需否定或删除其底层状态。观测 unknown 时暂不批准依赖白圈的动作，不产生状态为 false 的事实。本批仍为条件审计，尚未把所有角色改成这种候选策略。

后续反向观察首先记录带角色、按钮变体和观测时间的谓词事实，例如 `熔火计数≥4`、`推荐目标有导电`，而不是写死完整层数或生成一个猜测持续时间的buff。目标条件还需绑定当时推荐目标；当前没有独立的该目标身份观测，就保留“此按钮的推荐目标条件通过”，不能直接写进全局 EnemyState。多分支保存整个“或”条件，只有另一分支可靠为 false 时才缩小结论。

亮起 `B ∧ C` 可以推出 B 与 C；熄灭只能推出 `¬B ∨ ¬C`。CD、费用、中断许可或目标变化都可能撤掉白圈，所以“没有白圈”不表示所有异常/资源都不存在。只有其他门槛已独立确认为 true，才可以据此否定剩下的条件；复合条件取反也只表示至少一项不满足。无有效画面、槽位不明、全屏闪白等返回 unknown，不产生负事实。

这些事实可作为战技候选门槛或强化分支的额外观测依据。例如莱万汀亮起可支持选择“熔火达到强化阈值”的动作报价；弧光亮起可支持导电条件的候选；狼卫仍需保留燃烧/导电分支不确定性。没有证据时不补命中、最大层、持续时间或实际资源返还。白圈按键后消失的释放确认继续只沿用已批准的庄方宜合同，不推广为所有角色的通用成功证据。

## 本批执行边界与验证

技能时间排轴中的庄方宜普通、姿态内付费和首次免费战技均在自己的按钮白色时才尝试；不会消费现有通用推荐检测的上升沿状态。按键后进入原pending流程，只有有效画面中本人白色消失才接受释放、写入加成/冷却和消费首次免费机会。失败提示优先；白色未消失或观测未知超过0.8秒则保留免费机会、清理未证实动作并按原0.2秒退避重试。空画面、无效槽位/人数、已有全按钮闪光拒绝条件返回未知，不能当作消失。

预测仍可评价“终结→首次免费战技”带来的收益；执行该未来动作时仍等实际白色。原动作锁、名义姿态起止、公共SP预算保留。其他角色、匿名连携和通用推荐开关的行为保持原合同。没有将这13个角色自动改成“仅白圈释放”，也没有把空高亮配置解释成禁止释放。

本批9项新增专项覆盖自身/队友槽位、短队伍映射、只读检测、付费/免费战技、真实step、失败后重试、白色/未知超时及有效消失提交；相关128项通过，全仓1081项通过（39.442秒）。固定面板、增益快照、报价和导出证据均未改。尚未游戏现场验证。

触发条件续批仅完善证据文档：重读36份按钮记录、核准13位非空条件及同版标签/枚举/上述原生块，并检查两个庄方宜记录的高亮完全相同；没有新增全角色反向观察运行时，也没有更改已有释放行为。未确认项明确保留为目标身份绑定、实体组位置选择、赛希 exist 启用/移除链、诀 seal2 完整阶段含义及其他未选按钮变体。
