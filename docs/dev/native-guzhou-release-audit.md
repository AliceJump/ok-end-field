# 孤舟终结释放加成的事件证据（2026-10-08）

本批闭合一条证据明确的加成到离线模型成功释放事件。没有接入master编排、游戏识别或完整原生武器程序，也没有把孤舟的法术异常消费分支改成释放即触发。

## 原生来源与时点

当前VFS的WeaponBasicTable确认wpn_funnel_0015绑定sk_wpn_funnel_0015，原生ItemTable/文本条目确认为孤舟，即庄方宜当前配装。三份记录与当前VFS条目MD5匹配，MemoryPack解码后逐字节回编码一致，DLL/metadata与既有索引一致。完整SkillData和两份BuffData、Rank9 SkillPatch条目保存于assets/data/equipment_mechanics/20261008/guzhou.json；索引校验内容摘要。

终结分支监听**OnBeforeCastSkill=30**；CheckSkillType检查Beyond.Gameplay.SkillType.UltimateSkill=7，非current skill/exclusive time条件。紧接CreateBuffAction创建buff_wpn_funnel_0015_ultimate，asChildBuff=true、buffSource=0、targetSource=1（Source）、count=1。显式传pulse_dmg_up3→pulse_dmg_up、duration2→duration。因此canonical“施放终结技后”并不对应cast finish，本版数据明确在before-cast事件创建加成。

Rank9 patch传pulse_dmg_up3=1.1200000047683716、duration2=25；原技能默认0.5、10秒不使用。原生属性buff没有属性修改、buff/ability/时间线/ignite回调或治疗/盾等副作用，lifeType=0、duration读BB；stackingType=4（Refresh），不增加层数。原始max_stack查值尚未作为通用原生Refresh输入绑定；离线规则的不可叠加由该条canonical描述明确核准，不据此填原生缺失BB。

## 受益范围与计算

damageModifier只有一个攻击方NormalCalcZone加法processor，读bb.pulse_dmg_up。两个AND条件分别为HasAll NormalSkill damage mask=256与DamageTypeMask=8；Pulse枚举3，8=1<<3，即战技电磁伤害。模型规则据此限定self、电磁与skill标签，加入damage_bonus桶；不会给普通攻击、连携、终结或队友加112%。

孤舟固定电磁伤害44.8%（cardAttributeModifier的52/BaseAddition）已在固定面板，本批只增加临时112%规则，避免再次加入44.8%。没有改变32份固定面板或128份未触发报价。

src/data/reviewed_weapon_release.py核验索引、构建、等级、原生事件/动作/继承输入、对象、伤害过滤及canonical数值后返回一个规则，damage_release_rules仅对实际选中孤舟的配装登记。离线CombatWorldState.start通过合法性判断后发布已有成功释放事件，该规则立即作用于随后命中。失败/重复/其他技能不创建；再次终结只刷新25秒实例，提前移除与预测分支沿用原状态隔离。

这里证明的是模型释放→规则创建→限定伤害→刷新/移除/到期。原生before-cast事件的完整调用顺序、取消/死亡时武器passive父根何时停用、原生mask真实发布和逐击观测仍未验证。模型start发生在自身合法性及成本扣除之后，不宣称它逐指令复现了原生before-cast帧。完整原生程序树仍2/111，不能把新增一条模型绑定计为完整程序。

## 仍不触发的另一分支

原技能另一事件为OnConsumeBuff=208，检查原始NormalSkill=2、Skill/Character/Common/SpellStatus标签d270dc57，以及sk_wpn_funnel_0015的独立0.1秒marker。它创建buff_wpn_funnel_0015_combo，Rank9给56%/20秒、max_stack=2，stackingType=2。名字combo不代表连携释放；该分支仍待实际消费事件/来源/marker与独立计时生产链，未登记到任何释放规则，不默认命中或满层。

## 来源摘要

| 记录 | SHA256 | 字节数 |
| --- | --- | --- |
| sk_wpn_funnel_0015 | dc713f294607ac1c6f00a741bf032d4cd028ef74620b92003d8348edccc997d4 | 1685 |
| buff_wpn_funnel_0015_ultimate | aa97cebd521f727dd0425527e2a286f6beb708b097a6c871af2c338217bfb742 | 425 |
| buff_wpn_funnel_0015_combo | 40a2e74f4f12b3f12cdd2e03cea340beb4fbec33f847f4e4c6ff29a40017ddc4 | 449 |

WeaponBasicTable/SkillPatchTable的原表摘要沿用同目录负山证据，内容重新与当前VFS核对。研究解密、客户端路径和全量数据继续本地忽略。

4项新增专项覆盖实际释放类型/元素/对象、失败/重复、刷新与早移除/到期/fork、等级覆盖与不触发消费分支、修改原生伤害过滤后的拒绝；53项属性/审计相关通过。全仓1403项通过（92.556秒），原生程序树复测2/111；解析、来源与敏感标记、Ruff I/F及diff检查通过。未做游戏实测。
