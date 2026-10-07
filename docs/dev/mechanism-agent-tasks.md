# 机制排轴后续任务清单（可分派给代理）

背景见 [机制排轴模型后续计划](mechanism-model-roadmap.md)。本文把路线拆成小任务，每个任务的“指令”段可以直接复制给代理执行。

难度标记：

- **低**：步骤明确、改动局部，低成本模型可做。
- **中**：需要读懂一两个模块再改，建议中等模型。
- **高**：涉及设计取舍或大范围冲突，建议强模型或人工把关。

以下1–8是历史拆分任务，其完成状态见下表。新的续作顺序以[实际阻塞诊断与计划](mechanism-blocker-plan.md)为准，先诊断依赖、闭合代表队与连续观测，再验证收益/预算并推广全角色；每项共享合同或完整依赖链阶段提交。

## 2026-10-07执行记录

具体证据见[流程进度](mechanism-flow-progress.md)，下文任务指令中的落后提交数、耗时和预期覆盖数为起始估计，不能作为当前结果。

| 任务 | 当前结果 |
| --- | --- |
| 1 | 已rebase到master e7fcb4b2，修复丢失的动作暂存/观测连接，备份后完成强推 |
| 2 | 覆盖审计和离线计时脚本已提交；完整程序树2/111，不代表实战可接管 |
| 3 | 不可变图审查、共享与隔离对拍完成；fork约0.32ms，达到小于1ms目标 |
| 4 | 按需输入与全程序对拍完成；完整144次展开25ms目标尚未验收 |
| 5 | 八类场景节点按前提处理并记录；覆盖仍2/111，未复现9/111估计 |
| 6 | 六类分别核对并提交；均保留未解析，执行缺口见控制流审计 |
| 7 | 五人来源复查完成，只改秋栗并重算两份基准；其余没有一致的新四件证据 |
| 8 | 只读接入报告在本地tmp/new_operator_onboarding.md；两名预览不算已接入 |

任务分类/调研完成不等于机制执行完备。子buff/动作绑定寿命仍有缺口，但先确认它们在真实队伍中的生产者/消费者和最后阻塞，不能仅按上次节点研究断点续作；技力返还时机仍后置。

## 所有指令共用的前置说明

把下面这段放在每条指令前面：

```text
仓库：ok-end-field，分支 codex/effect-semantics-normalization（工作目录为该分支的 worktree）。
开始前先读仓库根目录 AGENTS.md，并按其中的 repository-workflow、use-local-venv 技能执行。
Python 一律用 `uv run --locked python ...`；全量测试用 `scripts/testing/run_tests.ps1`。
不要推送 master，不要改动与任务无关的文件。完成后：运行任务要求的测试，
`uv run --locked ruff check --select I,F <改动的 py 文件>`，`git diff --check`，然后单独提交并推送本分支。
最后用中文简要报告：改了哪些文件、测试结果、是否已提交推送。
```

## 任务 1：同步 master（难度：高）

```text
本分支落后 origin/master 25 个提交。执行 git fetch origin，然后把本分支 rebase 到 origin/master。
遇到冲突时：保留 master 新增的功能，同时保留本分支的语义归一、机制执行和配装数据改动；
assets/data 下的生成数据（fixed_damage_baseline.json、damage_baseline.json、character_builds/*.json）
不要手工合并，冲突解决后按 docs/dev/combat-system/BASELINE_DATA.md 第 2 节的流水线重新生成。
全量测试通过后用 git push --force-with-lease 推送本分支。报告冲突文件和处理方式。
```

## 任务 2：机制覆盖统计脚本（难度：低）

```text
新建 scripts/skill-data/audit_mechanism_coverage.py，只读统计，不改运行时代码。
对 assets/data/fixed_damage_baseline.json 里的每个角色调用
src.data.combat_catalog.build_combat_catalog((角色名,), SkillTimingStore())，
用 src.data.combat_simulation.walk_combat_events 遍历每个程序的事件，收集 event.unresolved。
输出 JSON 到 --out（默认 tmp/mechanism_coverage.json），内容包括：
程序总数、完全无未解析节点的程序数、每角色明细（程序 key、kind、未解析原因列表）、
按“原因去掉角色 ID 后的类型名”统计的影响程序数（降序）。
再统计四人队 ("弭弗","骏卫","余烬","卡缪") 的 world.fork() 平均耗时和每个战技 simulate 的平均耗时，写入同一 JSON。
在 tests/ 下加一个小测试，只验证脚本对单个角色能产出上述字段。
预期当前结果：约 111 个程序里只有 2 个无未解析节点；fork 约 50ms 量级。
```

## 任务 3：分叉性能——共享不可变对象（难度：中）

```text
目标：src/data/combat_simulation.py 中 CombatWorldState.fork() 目前用 copy.deepcopy，
会把 ActionProgram、CombatEvent 等冻结 dataclass 也深拷贝，四人队一次约 50ms。
步骤：
1. 列出 src/data 下所有 @dataclass(frozen=True) 的类，逐个检查字段是否含 list/dict/set 等可变对象。
   含可变字段的，改成 tuple 或 frozenset，并修正构造处；不能改的记下来，不做共享。
2. 对确认不可变的类实现 __deepcopy__(self, memo) 返回 self。
3. 新增测试：fork 后修改新世界的状态（时间推进、SP、buff 实例）不影响原世界；
   同一程序在两个世界里 simulate 的结果与改动前一致（可用固定队伍对拍 damage 和 snapshot）。
4. 用任务 2 的脚本测 fork 耗时，写进提交说明。目标 < 1ms。
全量测试必须通过。
```

## 任务 4：事件输入按需构建（难度：中）

```text
目标：combat_simulation.py 的 _execute_event_body 每个事件都会遍历全部 EffectType 生成 count.* 输入、
遍历全部原生 buff 查询，单个程序 simulate 约 8ms（任务 3 之后）。
步骤：
1. 在编译阶段（native_action_program.py 生成 CombatEvent 处）收集每个事件的表达式实际引用的输入键，
   可在 CombatExpression 上增加“返回引用的输入名集合”的方法。
2. _execute_event_body 只计算这些键；没有表达式的事件跳过 count.* 生成。
3. 结果必须与改动前逐项一致：新增对拍测试，对弭弗/骏卫/余烬每个程序比较 damage、snapshot、unresolved。
4. 报告单程序 simulate 耗时变化。目标深度 2、144 次展开能在 25ms 内完成（plan_action_sequence 的实战参数）。
```

## 任务 5：场景无关节点分类（难度：中）

```text
背景：大量程序因为“霸体、时间膨胀、镜头透明度检查”等节点被标为未解析，
这些节点在“单个静止敌人在命中范围内、时序取实测时间线”的场景下不影响伤害和资源。
步骤：
1. 在 src/data/native_action_program.py 新增常量 _SCENARIO_IGNORED（与 _PRESENTATION、_SCENE_GEOMETRY 并列），
   首批只放：SetSuperArmorAction+Data、TimeDilationAction+Data、SetIgnoreGlobalTimeScaleAction+Data、
   CheckComboSkillCameraAlphaSetting+Data、IgnoreModelIntervalCheck+Data、SaveTargetDistanceAction+Data、
   MarkCanInterrupt+Data、UltimateTimeAction+Data。
   每项旁边用一行注释写清场景假设（为什么不影响伤害与资源）。
2. 编译时遇到这些节点直接跳过，不写入 unresolved。
3. 不要加入 InterruptAction、JumpToAction、ComboCacheAction、TemporaryUnlockAction、CurveEvaluateFloat、
   CheckDistanceCondition 以及目标空间过滤——它们可能影响控制流或数值，留给后续任务逐个确认。
4. 测试：对每个新增类型构造或选取一个真实程序，证明加入前后 damage 与资源结果相同，只是 unresolved 减少。
5. 用任务 2 的脚本重测，报告可计价程序数变化和新的阻塞排行前 10。
```

## 任务 6：控制流节点逐个确认（难度：高）

```text
针对任务 5 未处理的 InterruptAction、JumpToAction、ComboCacheAction、TemporaryUnlockAction、
CurveEvaluateFloat、CheckDistanceCondition，逐个判断：
(a) 只影响表现/位移 → 加入 _SCENARIO_IGNORED；
(b) 影响动作时序，但实测时间线（assets/data/skill_timings）已覆盖 → 绑定为时序约束；
(c) 影响伤害/资源/后续动作合法性 → 保持未解析，写明缺口。
判断依据只能来自 assets/data 下已校验的原生快照和现有审计文档，不要按名字猜测。
每个类型单独提交，附判断理由和测试。
```

## 任务 7：样本少干员的配装复查（难度：低）

```text
复查秋栗、管理员、大潘、余烬、黎风的固定配装。
抓取 https://endsync.vercel.app/zh/wiki/<slug>/（slug 依次为 akekuri、endministrator、dapan、ember、lifeng），
再搜索 1～2 篇 2026 年中文攻略，列出各来源推荐的护甲/护手/配件/配件。
规则：4 件=护甲1+护手1+配件2，同名配件可装两件，至少 3 件属于有套装效果的同一套组（纾难、涉渊不算）。
只有当两个以上来源一致且与 scripts/skill-data/generate_character_builds.py 中 CURATED_BUILDS 不同时，才修改该表，
并在 note 写明来源与统计。修改后按 BASELINE_DATA.md 第 2 节重新生成配装和固定面板，跑全量测试。
没有充分依据就不改，只在报告里列出对照结果。
```

## 任务 8：新干员明河、祀的数据接入评估（难度：低，只出报告）

```text
官方目录（tools/wiki_catalog/zh_cn 最新快照的 m1_s1.json）里有明河、祀两名干员，本仓库没有收录。
只做调研，不改代码：列出接入一个新干员需要补哪些文件
（参考任意现有干员在 assets/data/character_skills、character_builds、characters.json、
character_progression、skill_timings 以及 src/data/operator_names.py 中的出现位置），
说明哪些可由现有脚本自动生成、哪些需要解包数据。结果写成 tmp/new_operator_onboarding.md 并在报告里总结。
```

## 后续（任务 1～6 完成后再拆）

- 区间计价：未解析部分给伤害上下界，区间分离时才由机制模型接管（路线图阶段 3，难度高）。
- 在线抽象模型与离线策略表（路线图阶段 4，难度高）。
- 按决策影响补长尾：子 buff 寿命 → 投射物 → 能力实体（路线图阶段 5，逐项拆成中等任务）。
- PR 拆分：语义归一、数据管线、模拟内核、实战接入四个 PR（难度高，需人工确认边界）。
