# 伊冯科技连击：冻结来源到下一重击（2026-10-15）

本批进一步核查满级天赋的真实生产/消费链，不启用新的伤害规则。`+50%`只对应一次指定重击；“战技释放后本人持续全伤+50%”没有依据。描述中的成功冻结与实际原生前置通知必须分开，不能把前置事件当作免疫检查后已施加状态。

## 已核链路

1. 战技创建`buff_chr_0017_yvonne_normal_skill_projectile`与`buff_chr_0017_yvonne_normal_skill_listener`。投射物的命中与阻挡都可以转入`chr_0017_yvonne_normal_skill_projhit`；不能把阻挡回调等同成功命中。
2. projhit有四份`ForceSpellStatusAction`路径，读取实际`count`、寒冷/自然消费类型与Frozen类型。它们分别属于目标/附着分支，不能按节点数推定四次冻结。
3. listener订阅事件206 **OnBeforeOutputBuff**，先检查`Skill/Character/Common/SpellStatus/Frozen`标签，再检查`CheckSkillCastId`，随后给事件目标创建私人`buff_chr_0017_yvonne_normal_skill_frozen`标记。标记是1秒、Unlimited，不是可直接借用的全局敌人“已成功冻结”事实；其中追加DamageAction与CameraImpulse都`isEnable=false`，不能把这份闲置伤害算进去。
4. 天赋根`buff_chr_0017_yvonne_talent_1`订阅102 **OnOutputBuff**，检查上述私人标记ID，检查本人定时标记不存在，创建`buff_chr_0017_yvonne_talent_1_valid`，再创建`0.10000000149011612`秒定时标记。前置监听→嵌套创建→通知与原冻结拒绝/免疫的顺序尚未完整核准，不绕过真实cast过滤。
5. valid是15秒定时、Refresh4；其DuringBuffEnable=5配置`ComboCacheAction`：cmd0映射`chr_0017_yvonne_attack5`、cacheTime0.5、overrideCacheTime=true、cacheEndByAction=true。0.5是缓存配置，不能解释成15秒许可的持续时间。
6. valid的30号 **OnBeforeCastSkill**检查精确ID`chr_0017_yvonne_attack5`后，用`CreateBuffAttachingSkill`创建valid_up；它继承`dmg_up`、asChildBuff=true，并继承源castInfo。满级参数0.5，valid_up为lifeType1/Refresh4、Attacker/NormalCalcZone，并非无条件常驻加成。
7. attack5的21～24帧块包含`FinishBuffAction`，finishAll=true清理本人valid。清理起点是第21帧；不同输出buff/挂到Skill/父根关系下valid_up如何存续必须核查，不能随便在按普攻键时消费，也不能保留到所有后续普攻。

## 原始记录摘要

| 记录 | SHA256 |
| --- | --- |
| normal_skill | `fa61bd703dc1825c54cadecce5b702f94fc189925df3d302abb509a0712455c3` |
| normal_skill_projectile | `b3b40e1c984a29229867bdef8d2d8d7be9874e70ee60d70890eb2eb0d8d2e3bc` |
| normal_skill_projhit | `d6f73060567938d426a8f1709c50875b6eb494e4a967df95ff4f9acbd71709a4` |
| normal_skill_listener | `10516db9ef90fe711a51fb118bf4942bc2c586a60ae307110fdebd0ebe7aca81` |
| normal_skill_frozen | `9013d3b874beb17594561641ab982d34c2a6bf8437e74ae0f14c37b25e7bef83` |
| talent_1 SkillData | `c2c78a93e6c55203d99500dbdefc7a826f89759b85df9dc1bedc53128626e451` |
| talent_1 BuffData | `c29199ca8bc3fa8d8fedf7ddad9ca658ddf01508565ea3719b2ecd91ecb06a90` |
| talent_1_valid | `0a609002335950c36bfc651b47962fa281ab25fc85306268f28024cfc14c80e9` |
| talent_1_valid_up | `2d4bd3d7424675b36271043855be4aeafdecadc668cdfa03a01f8f525bc0208c` |
| attack5 | `a0cc8041f2ab8d1f5762f5a5574bd71690eb1060dfa78b3bffc0462c43d8c90d` |

本批复用仓库hash校验的native_record读取，没有拿名字猜倍率。独立逐字记录/查询脚本和日志仍仅在主仓库忽略目录`tmp/condition-research-20261013`。

## 同版方法核准与未知

`ComboCacheAction.ExecuteInternal`57979/RVA2dd6400/528字节窗口SHA`a3b0eb142e391c66ea63f761708e9327c00d7b75ff683edc501bc6fd7615a088`，取得动作owner并调用AbilitySystem.AddMappingModifier，保存返回的modifier handle。OnEnd57980/RVA2dd6330/208字节SHA`7bd6cb52dc45994e3d02d446f179fad97aa143b7045b265162e0ad81414562bb`，逐项RemoveModifier并清名单。命令映射的优先级/选择以及实际输入缓存消费仍未执行；不能把映射声明当作真实attack5已经施放。

`CheckTimedMarkerCondition.ExecuteInternal`91685/RVA3ab2760/208字节SHA`09410c57b19c201d14489a2aa29671a57c0d6e23d33935472af330714f3ab354`，先GetFirstTarget→ResolvePartBuffSource→HasMarker。`returnTrueIfNotExists`在原生返回分支翻转HasMarker结果；本例true表示标记不存在才能通过。它没有读取15秒valid期限，也不借用既有CustomSetCd的timer表。

`CreateTimedMarker`执行58101/RVA44734f0/2224字节SHA`ccda25fe442559dbd950f47f68c0e709ab4daaae1bb23b6fed67ff93a9883b5e`、OnEnd58102/RVA3ab2670/240字节SHA`7efee8d159771f9d7a6456d1f221ab7d862f7727bd4572c5fbc38d1b3a68c024`已保存机器码窗口，完整目标集合、marker刷新/时钟/OnEnd清理还待核。

metadata定位`CreateBuffAttachingSkill.OnBuffCreated`58076；当前使用的模块方法指针表没有其非零地址，不能冒充已取得原生附着实现。它是独立于继承CreateBuff执行的回调，需要继续追generic/override映射或其他本地代码证据。上述窗口范围均为下一已知方法指针边界，含对齐，不宣称严格函数长度或IFix路径闭合。

## 逐技能验收边界

| 单位 | 已知 | 不启用的原因 |
| --- | --- | --- |
| 战技 | 投射物回调、前置Frozen标签+同cast过滤、私人标记、0.1秒去重声明 | 真实投射物/冻结输出次序、免疫/拒绝及事件发布未闭合 |
| 普攻/attack5 | 15秒许可、0.5秒缓存、指定重击+50%、21帧清许可 | ComboController选择、附着Skill/父根存续、实际cast和伤害阶段未闭合 |
| 连携/终结 | 仍按各自既有规则；此天赋只检查attack5字面ID | 不将许可扩大为连携/终结或替换普攻的全伤强化 |

本批只新增可审核证据与后续位置，不改面板、报价、运行规则或识别，也不重复已通过的无关全仓测试。最近运行时批全仓1478通过；完整原生程序仍2/111。
