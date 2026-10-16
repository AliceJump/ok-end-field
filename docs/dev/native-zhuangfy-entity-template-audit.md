# 庄方宜造剑与区域模板核查（2026-10-17）

本批核准原生模板和更新后的来源，不新增剑数观测或运行时收益。用户已授权接入电剑预测，导电已确认时可取最低等级；但不能把首次免费战技的白圈 OR 分支直接当导电，也不能把造剑请求立即当成区域已经读到的剑。

## 更新资源与版本边界

安装中的 DLL/metadata 仍分别为 `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c` / `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca`，但 VFS 索引和字符串池已经变化，部分旧 Persistent chunk 已不存在。新资源单独捕获，不覆盖 `data-refresh-v5`，不删除版本守卫。

新捕获 `data-refresh-20261017` 的 8,728 项选择全部提取、缺失零；28 个字符串块严格解码，6,323 份 SkillData/BuffData 经 EOF、ID 和逐字节回编码核验通过。庄方宜 70 份记录的新旧解码内容完全相等；池索引变化后的二进制不能直接与旧记录混读。此比较只覆盖这 70 份语义记录，不声称全游戏配置未变。

三份模板通过 StringPathHash → manifest → bundle 映射、内容 MD5、AnimeStudio JSON/Raw 双输出和对象长度/SHA 核准。读取内容地址相同的 StreamingAssets chunk 时仍验证所选索引的文件 MD5，不以 chunk 所在目录改变记录来源。manifest 的 memory size 不当作原始 Unity 对象长度。

| 模板 | Unity 对象字节数 | 原始对象 SHA256 |
| --- | ---: | --- |
| `abilityentity_chr_0030_zhuangfy_normal_skill_sword` | 3164 | `fc2b50c88c8a7b8cda86008b919abb1fdf2b370bd6eb4d20d79623c9192b1866` |
| `abilityentity_chr_0030_zhuangfy_normal_skill_ult` | 3224 | `3f817aee44423d5bd6c444a8929bdc54d3c061039376b5cfa51f7ca050d9752c` |
| `projectile_chr_0030_zhuangfy_normal_skill_gene_sword` | 4324 | `369d936fadd589fbeea17e2b41a5a2069c97913310ac5ba9e8620896d076c926` |

实际使用的 Template/Root/AbilitySystem/ProjectileComponent 字段均经 serialized TypeTree 精确解码。注册表 fullyDecoded 不等于所有组件都经 TypeTree 解码：空 Controller 和 ProjectileRoot 的辅助布局不用于推断时序。本批没有声称这三份 Unity 对象已经独立回编码；其双输出摘要核验与 6,323 份 MemoryPack 回编码是两项不同验证。

## 可用的生命周期证据

剑 Template 和 RootComponent 都是 Limited，`durationBB` 启用并读取 `EntityBB_swordDuration`。模板字面 `duration=45` 不能覆盖输入；到达子技能显式将所选技能的 `sword_duration` 赋给该 key，目前审核 rank12 为 36 秒。`EntityBB_swordLimit` 同样来自 `remain_sword_limit`，不是当前场上数量。创建动作 `dieOnEnd=false`，不能按根战技动作结束删剑；`dieWhenSourceDie=true` 仍要保留来源失效清理。

齐射区域 RootComponent 的 `durationBB` 未启用动态 key，其直接值为 3 秒；同级旧字面 `duration=1` 不能凭字段外观当作实际寿命。后续已从Controller.OnSpawn核准其选择分支，见下节；仍不据此直接修改产品时间保护或声称三个现实秒。

本人范围内剑数仍由独立被动统计，再在区域创建前采样复制。模板寿命补齐不代表到达、来源 children 登记、被动更新、区域快照已经按真实相位运行，详见 [齐射与计数证据](native-zhuangfy-salvo-audit.md)。

## 造剑到达不能固定填 0.2 秒

生成投射物唯一 `Default` 模式的 `moveType=2`；当前真实枚举是 `Bezier2D`，不是 Parabola。模式中存在 `travelDuration=0.2`，但不能因此把它当成实际到达耗时。Bezier2D.OnStart（82733/RVA64c5ad0）在调用基础 OnStart 后，从 MoveModeData+0x40 读取 `speed`，把输入点差交给向量 helper，再以 `DIVSS` 除以该值，写入自身+0x2c。MoveModeData 的 `travelDuration` 实际位于 +0x80，以上已核分支不读它。

基础 OnStart（82715/RVA64c5c40）另读 `useSpeedScaleWithDistance`（+0x58）和对应曲线（+0x60），把曲线结果写入基础管线+0x24。模板 `speed=50`、距离曲线和常量速度曲线均已提取；这些还不能代替后续 Tick、推荐目标骨点、落点与到达事件的核准。旁边的 `parabolaDef=1` 属于未选中的抛物线参数，不能套用来解释 Bezier2D。

首次免费分支的 LaunchProjectile 索引42以 `smart_target` 为发射位置，使用 emitMountPoint52，并向 `swordPos` 发射；不是已经证明从角色脚下直线飞向敌人。不能用角色距离直接代入，也不能忽略骨点高度、导航投点和实际管线更新而宣称三剑一定赶上15帧区域快照。finishDuration 直接值2秒是投射物结束配置，不是保证成功到达的截止时间。

## 累积进度、到达派发与寿命执行续批

当前字段注册表核准：BasePipeline 的 `m_elapsedTime=+0x20`、`m_speedScale=+0x24`；Bezier2D 的 `m_scalingElapsedTime=+0x28`、`m_travelDuration=+0x2c`。Move（82735/RVA91c53b8）的默认执行分支按以下顺序逐次计算；`f32`表示每条单精度指令后的舍入，不把整段合成double公式：

```text
sample = f32(f32(elapsed + f32(delta × 0.5)) / finishDuration)
curveResult = speedCurve.Evaluate(sample)
scaledElapsed = f32(scaledElapsed + f32(f32(curveResult × delta) × speedScale))
positionParameter = scaledElapsed / travelDuration
elapsed = f32(elapsed + delta)
```

`finishDuration`来自ProjectileComponentData+0x18，不是前述管线内的travelDuration。当前位置仍通过Bezier插值和FaceAndMoveTo更新，模型不能仅根据elapsed推断当前坐标。已到达且不允许keepMove时还有提前返回分支，以上公式不是每个外部tick无条件执行。

独立get_isForceReached（82732/RVA91c56cc）使用严格比较：`scaledElapsed > f32(travelDuration + float32(1e-5))`，不是大于等于。0.5与epsilon均从实际RIP常量重读；虚槽6/7分别为isForceReached和skipCommonReachCheck。强制到达只是候选条件，其他几何到达检查不因此消失。

MovementSubComponent.OnTick（82673/RVA4feb600）的已核路径先_PreMove、调用当前管线Move，再_PrepareReachOrCompleteCurMoveSegment，之后检查碰撞，最后在尚未结束时_TriggerReach。_TriggerReach设置m_isReached并为到达子技能设置目标后_CastSkill。因而即使进度跨过强制阈值，同轮碰撞/结束也可能阻止到达子技能；不能把标量阈值直接当剑已创建。

### 真正读取寿命的控制器

AbilityEntityController.OnSpawn（70364/RVA628b080）读取RootData.lifeType+0x50。Infinity路径MarkInvalid；Limited路径在durationBB既未启用key且直接值为0时回退duration+0x54，否则经GetFloat读取durationBB+0x58。显式override另有独立门槛。本批生成剑和区域的Spawn均未启用override，因此分别读取传入的36和直接值3；Root.get_autoRelease返回false不代表寿命无限，寿命由Controller自己的计时器处理。

有限寿命被夹到非负后设置PeriodicTimer.period并Reset(true)，随后注册DurationTick。_DurationTick（70361/RVA628c940）调用Timer.Update，成功后SetFinishReason(LifeTime=1)、Entity.Die并使计时器失效。PeriodicTimer.Update（6975/RVA61db310）虚槽5按float32执行remaining减delta和passed加delta；remaining<=0时返回true，将remaining置0、passed置period。这里没有复用Bezier到达的1e-5比较。未核准Tick管理器的实际delta来源/相位，所以仍不能把有限寿命改写成绝对double墙钟截止。Die、OnFinish通知和来源children真正移除之间也不直接画等号。

### 原始指令回放验证

独立校验重读158个方法窗口、10组字段、2个常量和48个锚点，验证原生表中的方法身份/地址和虚槽。窗口仍按下一方法指针封顶，可能包含相邻代码；不冒充严格函数长度或IFix实际选路证明。

另用隔离的Unicorn环境回放原始x64标量块，与逐指令float32解释作388次比较，涵盖中点采样、进度累加、严格epsilon边界和PeriodicTimer.Update。输入是明确提供的合成状态，不是游戏实测：36周期分别重复提供float32(1/30)、float32(1/60)，在第1081、第2161次Update才就绪，说明简单36秒deadline并不等价。该结果不能证明游戏固定30/60Hz，更不证明剑已经到达/计入/命中。临时依赖未改项目锁文件或全局环境，完整原生程序覆盖不提高。

## 撤回会污染计数的旧假设

canonical惊霆诀的基础effects原来含`STACK_QINGTING_SWORD/count=-1`，机制表的普通/首次免费分支又写`all_on_attack`。技能描述消耗的是导电；已核普通引雷/剑触发和姿态区域分路没有依据把整组剑统一消费。本批移除这项无条件全量消费声明，保留条件生成及真实寿命/移除链的未确认项；这不声称任何状态下剑都不会移除。

普通分支改为显式`0..3_conditional`生产合同：已有至少3剑而未消费导电时允许零生成，其余生成取决于条件。旧期望层记录该未闭合producer，不按范围均分新增剑；它也不据此得到了一个新的精确总数。青霆剑资源的initial_count明确为None，未提供已知旧剑时不初始化成0；给定已有剑的名义转移不再先清空它们。

旧HiddenStateExpectation还曾把首次免费资格直接定价为完整高端伤害并标为概率1。该纸面包络没有到达、创建时剑数或逐雷输入，本批将这类剑依赖战技的完整包络保留为未定价，传递到本来就不可执行的纸面爆发计划。PR489使用独立的释放层报价，未受此研究层修正影响；没有阻止其中既有免费战技执行，也没有将未确认剑数塞入它的真实调度。

三项新增回归分别覆盖给定已有剑的名义转移不清零、零生成场景不捏造均匀增量、未知初始剑数及开放producer保留；原纸面免费完整伤害断言改为未知，并验证传播到计划层。70相关回归及全仓1738项（162.194秒）通过，164测试文件/16组登记、Ruff I/F、AST和diff-check通过。重生成固定基线后32面板/profile/build和128技能报价逐项完全相同，只有庄方宜canonical来源摘要变化；数据流审计仍32角色/128重放、128项待语义核查，不据此减少全任务缺口。

## 接入顺序与验收边界

先把可核准的生成、到达、登记、计数和创建快照连成模型事件，再将其作为预测输入进入真实报价；模型预测不得写成 UI 观测或敌方事实。未知初始剑数保持未知，导电最低等级仅用于确立的导电分支。未核准的部分不以固定0.2秒、上限9剑、首次立即3剑或默认命中补齐。

原始资源、捕获索引、脚本、CLI 清单和机器窗口只在主仓 Git 忽略目录 `tmp/condition-research-20261013`：`zhuang-prediction-20261017`、`zhuang-projectile-motion-20261017` 和 `native-build-ab00f1c4/data-refresh-20261017`。产品 PR489 的面板、15条加成、132报价和姿态未改；研究32面板/128报价、14模型释放/2原生ATK以及完整原生2/111均不提高。
