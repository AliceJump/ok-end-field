# 机制模拟续接检查点（2026-10-05）

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

2026-10-07最新入口：先读mechanism-flow-progress.md和native-buff-lifetime-audit.md的最新续作段。已支持动作窗口清理、Buff父子含Unique首次父根、持久Ability子挂接及显式确认停用后的子孙/旧时间线/订阅清理。槽映射替换/恢复不会直接停用旧Ability；不能把本次cast或handoff当作根结束。完整程序覆盖仍2/111。

最新续作已接入选中被动Skill的Enable期SkillData.buffs独立名单、当前BB重新挂载及_FinishPassiveBuff按UID清理，真实余烬名单诊断在此范围内关闭。全仓1284项通过，完整程序仍2/111。下一批继续定位战斗里的Skill.Disable生产者，补Skill.m_buffsDuringSkill和真实CastEnd原因/目标技能的继承释放链；普通主动Skill的附带buff、完整Enable副作用也仍未覆盖。以下属性/反应/伤害处理器缺口也继续保留；ComboCache、Curve、Interrupt、Jump、目标、投射物/场地实体、搜索完整预算及现场验证均未完成。仅阶段性提交，不删除本会话续作automation-2，不新开PR或推送master。

1. 补属性57/58的实际默认值/生产者来源，不能把未知数据填0。
2. 冰冻的 child buff 寿命、碎冰触发及物理伤害增量；先定位实际子buff，再执行完整归属/销毁链。
3. 腐蚀的 AttributeModifier、RefreshBuffAttrModifierValue、独立 tick 与减抗上限；燃烧攻击快照、子buff和持续触发。
4. OnSpellAbnormalStartFinish、附着增强、免疫及实际不同来源的反应回调仍有诊断，尚不允许完整反应估价。
5. 其他原生攻击方/乘区/复杂条件处理器继续逐项验证；防止与已有 canonical 天赋和装备面板重复叠加。
6. 消除真实队伍的未解析机制后再扩大搜索覆盖。当前多数真实队伍仍回退旧调度，不能报告整套机制已全面投入战斗。

本地外部研究目录保留 DamageScaleProcessorConfig 的解析树、原始/回编码对象、bundle 绑定证据、枚举及方法反汇编；新增研究脚本和日志保持忽略。续接先读本检查点和 native-combat-execution-audit，再检查远程分支与 master 差异。

本阶段全仓1137项测试通过（59.822秒）；修改文件的Python/JSON解析、敏感路径检查、Ruff I/F与git diff --check通过。新增8项伤害处理器专项覆盖真实导电生产链、元素/目标、同区加法、过期/共享替换、独立BB、预测隔离和缺失输入拒绝。未进行游戏现场验证。
