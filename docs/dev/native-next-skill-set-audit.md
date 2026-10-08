# 碾骨、50式应龙的下一次技能加成（2026-10-08）

本批补齐选中配装对应的原生数据、消费顺序和清理依赖，暂不执行。没有把原生永久实例改成任意秒数，没有用动作结束清掉仍在飞行/存活的技能来源，也没有预填最大层数。证据进入逐角色、逐技能审计；14 条释放绑定、2 条原生实例 ATK、32 面板/128 报价及完整程序 2/111 均不变。

## 真实配装与参数

当前 VFS EquipSuitTable 以 equipCnt=3 绑定套装被动及技能等级 1。额外提取当前 ItemTable 与 I18nTextTable_CN，核准套装与装备名称，不用被动文件名猜套装。两张表分别为 659740/9513700 字节，SHA256 `e2c6aab73a489d2687bfacbf3fb0cbc60de6269db7c21981d90de49e49c247ad` / `fca1a4ea03ccd38dd20c664e5c4efc53178cb4e9df722c1ebba10e37f4426ea1`。提取和保存选中片段前重新用当前 BLC/VFS MD5 验证四张表和九份 SkillData/BuffData；九份均有逐字节回编码证明。仓库仅保存选中名称/表行/树和摘要，研究工具与客户端数据留在本地忽略目录。

| 套装 / 原生被动 | 实际启用的角色 | 当前等级覆盖 BB | 原文件闲置默认 |
| --- | --- | --- | --- |
| 碾骨 suit_attri01 / passive_equipsuit_attrisuit_01 | 艾维文娜，4 件 | atk_up=0.15000000596046448，dmg_up=0.30000001192092896，max_stack=2 | 技能 atk_up=.05、dmg_up=0、max_stack=0 |
| 50式应龙 suit_atk02 / passive_equipsuit_atk_02 | 大潘和萤石，各3件 | atk_up=0.15000000596046448，dmg_up=0.20000000298023224，max_stack=3 | 技能 atk_up=0、dmg_up=.2、max_stack=0；检测 buff 上限还留有5 |

大潘的两件同名雷达占两个装备槽，仍是三件套，不按名字去重。阿黛莉娅、别礼、昼雪只穿一件碾骨散件；审计登记未激活，不能施加三件套。15% 常驻 ATK 已归固定面板，本批不再额外施加。

## 原始流程

碾骨永久 Unique parent `buff_equipsuit_attrisuit_01` 监听 OnBeforeCastSkill=30，以 SkillType ComboSkill=6 创建一层永久 Stack=2 的 `buff_equipsuit_attrisuitup_01`，max_stack 从选中参数读取。pending 的 OnBeforeCastSkill 检查 NormalSkill=2，依次：

1. SaveBuffStackNumAdvanced 将 Owner 的实际 pending 层数存到 stack，未按施放次数或最大值补输入。
2. ModifyDynamicBlackboard 将 dmg_up 乘 stack。
3. CreateBuff 创建 `buff_equipsuit_attrisuitup_02`，传入计算后的 dmg_up，inheritSourceSkillCastInfo=true，asChildBuff=false。
4. FinishBuffAdvanced 对原 pending 执行 finishAll=true。

新伤害 buff 要求 CheckSkillCastId 和 HasAll NormalSkill mask=256，攻击方 NormalCalcZone 读取 dmg_up；不是给此后所有战技加常驻收益，也不是第一次命中才消费一层。

应龙 parent `buff_equipsuit_atk_02` 的 OnEnable=5 启动 AuraAction；给选中的存活角色挂 `buff_equipsuit_atk_02_aruadetect`，继承 dmg_up/max_stack。检测器监听 NormalSkill=2，创建 `buff_equipsuit_atk_02_addcombodamage`；Create 的 targetSource=1（Source）与碾骨的 targetSource=4（Owner）不同，保留原始配置，不能根据中文“该干员”替换对象。

应龙 pending 监听 ComboSkill=6，使用同样的 snapshot → multiply → create → finishAll 顺序；新 `buff_equipsuit_atk_02_addcombodamage_buff` 要求 CheckSkillCastId 与 HasAll ComboSkill mask=8192。Aura 实际成员/Source/Owner、来源叠加和施放前事件顺序仍需执行证明，当前不宣称跨角色受益链闭合。

两个伤害 buff 都是 lifeType=1，通过 OnEnable 执行 SkillAffixAction。它们不使用闲置 duration=0 定时，也不能按下一次普通动作结束或调度 handoff 清理。

## 清理依赖的机器码

重新核准同版 DLL/metadata。SkillAffixAction 的字段为 m_skillCastId/m_refCount/m_hasPendingSkillRequest/m_onResetList/m_buffList。ExecuteInternal（59930/RVA3c45e10，1800 字节窗口 SHA256 `f6a1fdc02449ba2be82bc0dfcbf93cebe4148202ac629a3b302bb79dc835e44a`）在 3c45ec0 保存 cast id、3c45ecf 设置 refCount=1，并订阅后续 cast/skill end/输出 buff 等事件。

_OnAbilityEntitySpawned（59933/RVA2e56810，1800 字节 SHA256 `d13cf0a1fc1d7e07919a018066847571b939f641d88fbeeadda1752a20fcb1f3`）在 2e56868～2e5686e 比较 cast id，2e5691a 增加 refCount，并登记释放回调。_OnProjectileLaunched（59932/RVA347b910，1800 字节 SHA256 `09de81c4fbfa99bd26d80029e50cc9c68471f851c27df399804c404bfc989834`）也先比较 cast id，其匹配分支在另一段机器码，尚需继续核准登记/释放顺序。

_OnSkillEnd（59937/RVA4299b90，1800 字节 SHA256 `d4b677fc212182e6d59a362d427294c552e17cc056a7a85b946ff01f6ef73c39`）只在匹配跟踪 skill 时转到 _DecreaseRefCount。后者（59942/RVA4299d30，1800 字节 SHA256 `8fdedcf65d88e526c7bf672df7442834ce292decfcd84ada0dff7a38afd47042`）在 4299d6d～4299d77 减少计数，仍大于零立即返回；到零后进入 buff 结束路径。长度均为校验窗口，不是完整函数长度。

CheckSkillCastId（91607/RVA44cabf0，1800 字节 SHA256 `2df0e85060aac1870501073b525d1fa5d8d28d5ae964b3381587162a6ef3ace4`）读取实例 cast id 后比较当前上下文的来源 cast。零 ID/不同上下文类型的分支和真实 damage publisher 尚未完整核准，不添加猜测默认 ID。

## 继续执行的边界

数据的名称、等级、实际件数、原始层数/消费步骤/过滤已获得；模型还须补 Aura 归属、真实释放上下文、原生 float32 求值、cast identity 的命中传播及 SkillAffix 引用和结束链。两套都保持 evidence_candidate_only，逐技能明确区分是否为生产/消费类型。完整链未执行，不能把 60%/60% 作为默认收益或关闭未知诊断。
