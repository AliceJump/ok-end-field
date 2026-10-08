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

## 有自定义高亮条件的角色

以下只列原生条件及输入，保留未译标签，不把推荐条件当作命中证据。公共条件是 `CheckAllowNormalSkillHighlight`；所有列出的公共条件均设 `checkAtbValue=true`。

| 角色 | 按钮记录/条件要点 |
| --- | --- |
| 诀 | normal_skill：目标组 highlight_smart_target 的 combo_skill_seal2 buff计数条件及浮点比较 |
| 狼卫 | normal_skill：公共条件 + 目标标签9648d5bd / bf9d6e57 |
| 弭弗 | normalskill_3（开天）：公共条件；首、次段配置为空 |
| 弧光 | normal_skill：公共条件 + 目标标签bf9d6e57 |
| 赛希 | normal_skill：公共条件 + talent_1_atb buff计数 + 队伍战斗状态 |
| 艾维文娜 | normal_skill：自身生成实体查找、标签217140df、Lances实体数量及距离条件 |
| 伊冯 | normal_skill：公共条件 + 目标标签1cdba15d / a7edd8ab + 目标相等检查 |
| 艾尔黛拉 | normal_skill：公共条件 + 目标标签edaee3e6 |
| 汤汤 | normal_skill：公共条件 + 本人water计数阈值2 + 自身生成water实体数量/距离条件 |
| 莱万汀 | normal_skill：公共条件 + energy buff计数阈值4 |
| 秋栗 | normal_skill：本人karin_ult buff计数阈值1 |
| 阿列什 | normal_skill：公共条件 + 目标标签1cdba15d |
| 庄方宜 | normal_skill、normal_skill_ult：相同OrConditionAction，目标相关have_sword/标签条件或本人ult_skill_free计数条件 |

## 庄方宜两种战技

两份完整原始技能记录的消费字节数等于源字节数，快照解析的高亮配置相等：

| 技能ID | 原始字节数 | 原始SHA256 |
| --- | --- | --- |
| chr_0030_zhuangfy_normal_skill | 23078 | 06a61a8b2630fbe792294c3a0dc0a9a24080d8c8be45cb5895d2f16f0f05a9fd |
| chr_0030_zhuangfy_normal_skill_ult | 23334 | 33483a4c247260eb69cdd90ab80a604c113fb8e57a79817d3a39f54dbf971ccf |

两分支均用 CheckBuffStackNumAdvanced，阈值为显式1，compareType=3；同版原生枚举 NumberComparer/相关比较枚举将3标为GreaterEqual。第一分支 buffSettings.checkType=1，同时保留 have_sword ID和标签bf9d6e57，checkTarget.targetSource=2、targetGroupKey=highlight_smart_target。这里保留标签查询方式，不擅自解释为“某个敌人一定持有青霆剑”。第二分支checkType=0，ID为 buff_chr_0030_zhuangfy_ult_skill_free，targetSource=1。未按实际伤害次数、SP变化或姿态开始时刻伪造这两条高亮输入。

## 本批执行边界与验证

技能时间排轴中的庄方宜普通、姿态内付费和首次免费战技均在自己的按钮白色时才尝试；不会消费现有通用推荐检测的上升沿状态。按键后进入原pending流程，只有有效画面中本人白色消失才接受释放、写入加成/冷却和消费首次免费机会。失败提示优先；白色未消失或观测未知超过0.8秒则保留免费机会、清理未证实动作并按原0.2秒退避重试。空画面、无效槽位/人数、已有全按钮闪光拒绝条件返回未知，不能当作消失。

预测仍可评价“终结→首次免费战技”带来的收益；执行该未来动作时仍等实际白色。原动作锁、名义姿态起止、公共SP预算保留。其他角色、匿名连携和通用推荐开关的行为保持原合同。没有将这13个角色自动改成“仅白圈释放”，也没有把空高亮配置解释成禁止释放。

本批9项新增专项覆盖自身/队友槽位、短队伍映射、只读检测、付费/免费战技、真实step、失败后重试、白色/未知超时及有效消失提交；相关128项通过，全仓1081项通过（39.442秒）。固定面板、增益快照、报价和导出证据均未改。尚未游戏现场验证。
