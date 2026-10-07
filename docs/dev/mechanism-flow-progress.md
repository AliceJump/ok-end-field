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

## 场景节点分类（任务5）

首批八种节点逐项标注场景假设：无敌方攻击，固定实测动作窗口，绝对时间线不随镜头播放变速。镜头透明度检查显式采用选项1，保留布尔取反；距离保存只在没有数值消费者时忽略。管理员连携的距离比较等仍保留未解析，不能将所有距离保存一并删除。选中程序的忽略节点会记录在程序和DEBUG日志中。

新增回归逐项验证忽略前后实际伤害、SP、终结能量一致，另验证参与数值比较的距离保存仍阻塞。全仓1236项通过。重测仍为2/111个完整程序树，未复现任务清单预估的9/111；该数字不代表世界状态和实战计价覆盖。

按影响程序去重的剩余阻塞前十：ComboCacheAction 84、CurveEvaluateFloat 70、主控目标过滤60、子buff/动作绑定寿命59、InterruptAction 44、位置目标过滤43、SaveTargetDistanceAction 39、TemporaryUnlockAction 39、LaunchProjectile 28、CheckDistanceCondition 27。原始审计结果在本地忽略文件tmp/mechanism_coverage_scenario.json。下一步按校验快照核对控制流，不依据类型名放行。

## 控制流核对与配装复查（任务6、7）

六类控制流逐类读取校验快照并各自提交回归，证据和执行缺口见[native-control-flow-audit.md](native-control-flow-audit.md)。均保留未解析：角色调度边界不能替代目标控制、输入缓存、帧跳转、锁定恢复、曲线数值或距离分支。严格模拟拒绝这些动作，原世界snapshot不改变；本阶段没有增加完整程序覆盖。

五个EndSync页面已抓取并与中文来源交叉核对，见[配装复查](small-sample-build-recheck.md)。秋栗新增精确四件定稿，有推荐与独立实战同四件证据；其他四人仍有来源/流派分歧，保留现有配置。重新生成配装、普通基准、固定面板，结构化验证两份32人基准仅秋栗一行改变，其他31人逐项一致。包含六类控制流回归的全仓1242项通过（61.399秒），Ruff I/F与差异检查通过。

## 新干员评估（任务8）

官方目录明河2503、祀2338均为label_type_preview。现有32人角色/成长数据及技能时序角色映射没有两者；按常见名称检索记录也未命中，但不据此断言客户端包内不存在未知内部ID。完整接入清单、脚本可自动生成范围和新版解包闭包要求写在本地tmp/new_operator_onboarding.md（按任务要求不提交新角色代码/数据）。捕获脚本当前无单角色过滤，时序索引重建脚本也不负责解包；缺完整详情、内部ID和校验闭包时不创建可计价占位程序。

本轮任务1～8的执行/分类/调研记录已补齐，任务4完整预算验收仍未完成，模拟覆盖仍待推进。后续优先核对子buff/动作绑定寿命；未知动作继续回退，技力返还时机后置。
