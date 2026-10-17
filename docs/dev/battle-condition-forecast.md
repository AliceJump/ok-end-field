# 白圈场景条件参与后续编排（2026-10-09）

> 2026-10-14续批：增加赛希「栈溢出」的敌方tag条件保留合同。它的全队增幅与条件驱散均指向友方，六份入口/子buff逐项核SHA；不据此保留本人资源、距离、实体或不明条件。实际预测可安排赛希终结→当前白圈强化的弧光战技→已有主控伊冯普攻，分别保留电磁追加与寒冷增幅收益；未新增切主控/命中/目标识别。原13条释放加成、面板、报价和庄方宜姿态不变。本批只扩大已审核的条件传递，不宣称全角色闭环。

用户最新要求：白圈证明当前战斗场景出现了对应条件，可组织后续技能，不能只提升当前按钮的报价。本批把原“只计搜索第一个动作”改为按每个预测分支传递条件证据，真实 `TimedCombatLogic.step` 传入当前队伍观察；下一实际step仍重新观察。

## 实际行为

弧光当前导电白圈亮时，搜索可以选择安塔尔终结技，再安排弧光战技。辅助终结不消耗公共SP；后续战技仍要100SP、按钮就绪和动作时间。弧光两份物理伤害与一次导电条件电磁伤害保持分开，安塔尔22%电磁增幅只作用于电磁部分。狼卫的燃烧OR导电整组条件也可经过这个辅助释放继续参与其后续报价，保留OR歧义，不任选一支。

这改变的是爆发动作顺序与后续伤害收益。白圈不会直接抢放。SP不足不能只凭一个零伤害辅助起手制造收益；是否按终结仍由已有个人能量就绪观察决定。无白圈时普通战技规则保留，庄方宜单独批准的白圈执行合同不变。

## 状态与边界

`BattleConditionEvaluator.forecast()`从本帧原始白圈观察先产出条件判定，再建立不可变`ForecastConditions`；观测层只保存visible与来源，不运行推断或决定释放。状态保存原条件组、按钮/技能变体、来源角色、观察时间以及有证据的tag/buff下限事实。opaque节点和通用就绪节点不当作战场事实；队伍来源模型排除出来的某一OR分支不冒充实际观察事实。二元、三元与嵌套AND/OR均保持原逻辑。批次监测复用现有BattleMixin固定小框，独立执行合同见[white-ring-observation-boundary.md](white-ring-observation-boundary.md)。

每个搜索分支带自己的一份状态，预测动作之后应用审核过的转换。安塔尔终结技是最早核准保留敌方tag条件的动作，随后核准了赛希终结技。未审核的攻击、可能消费/改变条件的动作使对应预测证据退回未知，绝不写成每个分支false。预测没有把消费、命中或返还写入真实战斗观察。实际白圈熄灭/未知、按钮变体或换队会替换旧观察。

最新已审核辅助为安塔尔与赛希终结技；该名单由逐技能合同限定，不按“辅助角色”或“零伤害起手”自动推广。

保留的条件仍属于原按钮的推荐目标范围。没有独立目标身份，不把两个角色的推荐目标当成同一个敌人，也不跨不同按钮合并EnemyState。当前事实接口可供后续技能消费者按已审核的范围使用；本批没有启用未核准倍率、条件消费者或跨目标推断。

未知剩余持续时间、目标存活、位置和晚期检查仍未知。保留是当前条件的名义短序列预测，未宣称状态一定活到后续命中；实际后续释放重新检查当前条件并重新选招。没有新增命中、目标、HP或属性识别，没有更改HUD/技力返还时点。

## 安塔尔原生转换证据

审核来源保持干净研究提交 `cc9b471b50cdd6bc8b3a148b4eca93deef4efa02`。五份记录SHA256：

| 记录 | SHA256 |
| --- | --- |
| chr_0023_antal_ultimate_skill | 95ba6add40352915096f74a8fc04d921a9c6fef28c861db75223ecb75b4f2945 |
| buff_chr_0023_antal_utimate_skill | 9f91af7045dee6bba8bddc018cfc3e0ff69739a16566c7905d2b5b4f7d10a1a3 |
| buff_common_damage_immune_ult_skill | b742dfedad17bd70a8b051ce9ccab45fa231d4ff1e5ed363c58e3bbb38337436 |
| buff_chr_0023_antal_ultimate_icon | be55268fa75109fdf04c8f918781d814361a24936f3b30b6e12eb517193ccc85 |
| buff_chr_0023_antal_ultimate_icon_2 | bdfd590551784fab066e8c7f3fa65c3d764edd1d119d1e4297c6b5f8c4f270e9 |

入口创建本人动作免疫与team增幅buff；后者的OnBuffStart（buffEvent=0）EnhancedAction在友方buff持有者创建灼热/电磁子图标，均无敌方异常创建、消费或清除。另有DuringBuffEnable（buffEvent=5）显示动作；它与OnBuffEnable（3）是不同原生事件。两个子图标只含友方显示效果，共通免疫的霸体设置也只指向本人。入口可能移动本人，不据此保留实体、距离、owner资源或目标身份条件。其他辅助需单独追子技能/场地/周期链，不能仅凭零伤害报价判断。

额外核准 `CheckAllowNormalSkillHighlight`：同版metadata91417 ExecuteInternal在RVA `3e73490` 读取owner当前普通技能及 `Skill.canInterrupt` / `AbilitySystem.CheckCanInterruptCurSkill`；91418 `_CheckAtbValue` 在 `649e80c`，读取checkAtbValue并调用 `Skill.CheckCost`。91417证据窗2704字节SHA256 `1d5915b99413f872c373d616992aee7840b0bf41d3f4ac1d386af4bbbafb733a`，覆盖相邻内联helper而非声称函数长度；91418证据窗108字节SHA256 `7831a25bcfc7bc1275c98eed472c9dc1df18149cb1c56ca829b4172b8363a353`。原生构建SHA仍为 `c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89`。

因此这个通用节点归入availability，并只在AND条件保留判断中与敌方机制事实分开。SP预算/动作锁继续核验；OR中的就绪节点或只有就绪的整组条件不能证明任何敌方机制。其他未知谓词不跳过。完整本地证据在忽略的 `tmp/highlight-availability-native.log` 与 `tmp/antal-condition-children.log`；审核哈希保存在可提交的导出合同。

## 验证

赛希六份原生记录：入口`aeb22f042668624c2de97fab95b2d8900eb6cb4017ef0eee1d0c786886f0c0da`；共通免疫`b742dfedad17bd70a8b051ce9ccab45fa231d4ff1e5ed363c58e3bbb38337436`；增幅父buff`1cc3a54f53c85fee01e7514fd14fc64e00ec9ecd00363b11d5d300f1339ad715`；短显示buff`a83324c48558330c86e8e4f035a8d41001eb98d7e74c7eb5ba21a583fc64e5dc`；寒冷/自然子buff分别`c3450ba84128987ee93d9b7c8b921358fc330e91ca544fdf1870cdbdd1a9a4ee`、`f0f9a2e8c697b238b5418e44c54a5686129e604f99aa55afbf33dd1a8a3633b9`。完整ID与SHA在`reviewed_condition_transitions.py`及导出快照，不依赖外部tmp。

入口第17块用CharacterTeamFinder得到`tar`，CreateBuff与满级天赋条件Dispel都使用该友方集合；自身免疫独立。父buffOnBuffStart（buffEvent=0）的EnhancedAction subtype=5/3，指定两个literal子buff，目标为buff持有者；子buff无敌方tag、属性/伤害修饰或动作回调。父buff另有DuringBuffEnable（5）显示动作，不当作OnBuffEnable（3）。事件名按同版原生枚举核准，不把展示合同当完整原生事件发布。显示与自身移动不证明敌方位置恒定。固定配装智识缩放的已有边界不变，本批没有补FinalNonConverted实际producer。

本批新增4项：敌方条件组/OR保留且本人资源不保留，六依赖各自变更/缺失拒绝（12子案例），有/无该转换的不同起手排序及真实TimedCombatLogic.step。114项专项通过（2.801秒）。原始独立研究脚本、记录和日志统一位于主仓库Git忽略目录`tmp/condition-research-20261013`，不混入本PR；源码与可重放的运行时数据仍按仓库验收。最终全仓结果见本批验收记录。

2026-10-14验收：全仓1352项通过（57.167秒），119份CI职责核验、AST、Ruff I/F、diff-check通过；两份条件快照逐字节重导出一致，产出闭包、倍率绑定与既有释放/辅助/姿态产物不变。PR基线保留后续主控交接和终结负反馈修正，通过合并master补当前差异，不改写已发布历史。尚未游戏现场验证。梨诺周期伤害/姿态退出、秋栗共享天赋副作用、其他强化消费/晚期条件与实际属性producer继续核查，不能因本批完成而称机制全闭环。

2026-10-09审阅修正：运行时逐项检查安塔尔入口和四个子buff的来源SHA，不再仅核入口。五份记录都已在现有timing store中核准，无需引入额外研究运行时。任一依赖缺失或哈希变化均拒绝条件保留；专项分别覆盖五项变更和五项缺失，98项相关及全仓1133项通过（2.366/19.500秒）。没有扩展保留动作或启用新倍率。

新增12项验证实际起手顺序、后续电磁报价、技力不足、未知/消费动作失效、分支隔离、当前false/unknown/换队替换、变体/目标范围、三元OR与就绪节点不当敌方事实。198项相关测试通过（3.400秒），全仓1132项通过（23.517秒）；109份CI职责、AST、Ruff I/F与diff-check通过，两份产物逐字节重导出一致。固定面板、原释放增益和庄方宜姿态产物不变，完整原生执行仍2/111。未游戏现场验证。
