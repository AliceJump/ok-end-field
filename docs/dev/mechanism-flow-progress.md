# 机制排轴流程推进（2026-10-07）

按 mechanism-agent-tasks.md 的任务顺序推进；技力确认时机继续后置。仅推送机制分支，不新开PR。

## 已完成

1. 同步最新master `e7fcb4b2`，rebase无冲突。发现并修复之前重放丢失的 `_stage_combat_action`，以及视觉SP观测到机制世界的连接。保留master的期望ROI/视觉锚点，预测扣费不作为新观测。本轮全仓1227项通过，额外新增观测连接回归通过。远程旧tip `cf769c81` 有本地与远程备份；已以明确旧tip的force-with-lease完成强推。
2. 新增只读覆盖统计脚本 `audit_mechanism_coverage.py`。每条程序按完整嵌套事件树统计；目录诊断和初始世界诊断另列，不能将“程序树无缺口”误报成实战可接管。基线复现2/111；提供逐程序原因、按影响程序去重的阻塞排行、四人队fork/simulate/search计时。非法替换程序的计时带legal=false，不能算作实际技能执行耗时。

## 不可变图审查与共享

只有经递归检查的不可变值图在deepcopy时返回原引用。创建时缓存判定，若有list、dict、未知类或可变后代则继续完整复制；不以frozen=True单独证明安全。

| 类别 | 处理 |
| --- | --- |
| CombatExpression、MagnitudeTerm、ModifierMagnitude、DamageModifierSpec、DamageHit、DamageField | 标量、枚举及不可变元组；安全实例共享，误传可变字段的实例仍复制 |
| combat_simulation里的21种冻结定义/结果、NativePassiveProgram、NativeDefenderDamageScale | 递归检查；只有全部后代安全的实例共享 |
| CombatEvent.effects/resources | 在事件构建时保存EffectValue/ResourceValue；分段计数表变为元组支持的只读Mapping，与原始可变canonical数据脱离 |
| FixedDamagePanel、DamageResult、ActiveDamageModifier | 含damage_bonus/amplification/buckets/inputs字典，继续复制 |
| NativePassive、CharacterProgression、角色能力/机制配置、SkillTiming、隐藏状态/排轴配置里的冻结但含可变后代的类型 | 未登记共享；保留原复制行为，本轮不改公共schema |
| CharacterCombatState、EnemyCombatState、NativeBuffInstance、世界的SP/时间/队列/BB/计时器 | 可变运行时状态，继续独立复制 |

源码批量修改前后对函数AST做逐项比较；原有函数体未删改，新增基类及事件构建快照是本轮明确的行为边界。对拍以禁用共享的完整deepcopy为参照，覆盖弭弗/骏卫/余烬/卡缪全部程序，比较ActionOutcome、BB、原生buff和诊断；另验证预测实例、面板映射、源技能数据、SP/时间隔离。

基线fork均值31.27ms；快照后5次实测0.35ms（硬件负载会影响结果）。程序树覆盖仍2/111，性能优化没有放宽未解析规则。后续继续事件输入按需构建与场景节点逐项核验，不能将性能改进报告为机制覆盖增加。

## 事件输入按需构建

CombatExpression提供实际输入引用集合，CombatEvent在编译时记录本事件需求。嵌套listener/iteration及buff回调在自己的scope评估，只有buff继承表达式属于调用者输入。资源的层数口径、动态伤害modifier与连携命中条件也加入需求，不仅扫描表达式。未证明不可变的事件继续使用旧的完整输入生成。

只计算被引用的效果层数、原生buff/属性查询与timer；仍保留动作开始时trigger快照，以及消费后的事件更新顺序。全仓1234项通过，对拍比较四人队所有程序的伤害、完整snapshot、BB和unresolved；普通无表达式事件不扫描效果层数。

5次实测fork约0.32ms；四个合法战技模拟分别约2.47/1.59/2.98/1.07ms，仍有两项超过2ms目标。当前真实队伍深度2搜索11.59ms，但由于未解析机制提前拒绝分支，不代表完整144次展开已达25ms。阶段1的完整搜索预算验收尚未完成；实战继续按原超时机制回退。
