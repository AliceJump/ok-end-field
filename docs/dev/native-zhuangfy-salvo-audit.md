# 庄方宜雷击总量与首次免费三剑（新版独立核对）

2026-10-16。用户实战日志暴露出当前接入层仅按普通45%/姿态81%单雷计价，不能代表整次战技；本批继续核对可获得的原生证据，不把未经确认的场上剑数写入排轴。运行时仍未启用下面的总量投影。

> 2026-10-17续批：造剑进度、到达派发、Controller寿命、原始指令标量回放和旧计数假设撤回见[native-zhuangfy-entity-template-audit.md](native-zhuangfy-entity-template-audit.md)。canonical及机制表不再宣称每次雷击消费全部剑，未知初始计数不初始化为0，条件生成不默认均分，旧纸面首次免费“必定全伤害”估价保持未知。真实到达/登记/计数/区域快照仍未执行闭合，不据此提高2/111。

## 版本和复现边界

使用同会话已解出的新版VFS5/content27010254数据。DLL SHA256为`ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c`，metadata为`461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca`。这不是接入PR锁定的旧cc9b世界；名字一样不代表可以直接替换旧运行合同。

主仓忽略目录中的`inspect_native_skill_effects.py`对普通、姿态两份入口重新解析MemoryPack、核验EOF并与保存的decoded逐项相等；字面递归共11份去重记录。`audit_zhuang_salvo.py`另核原始/decoded摘要、真实原生字段类型、分支索引、BB和等级参数，保存`zhuang-salvo-20261016/salvo-proof.json`。临时脚本、解包对象和输出均不进入本工作树或PR。字面引用无缺失不等于完整动态执行闭包。

新版普通入口SHA256：`7a8f64afcbda88181ed72561e6543e24707cd963b3750b5ec0bcc247ae585512`。姿态与区域记录、实体生成和普通触发buff的各自摘要在上述证据中独立保留，不借旧版摘要。

## 首次免费：请求三个位置不等于即时已有三柄剑

姿态入口第5帧的有目标分支检查`ult_skill_free`（索引34）。P0走P1检查的失败分支，索引41的RandomPointFinder明确`pointNum.useBlackboardKey=false/value=3`；残留`haveSwordNum`字串不使它变成动态读值。索引42发出生成剑的投射物，只在到达时调用`normal_skill_gene_sword_projhit`，不是直接在按钮输入时创建三份剑实体。

projhit按实际实体组、数量限制等条件创建剑；姿态入口随后还需通过战斗/推荐目标检查，在第15帧索引81创建区域实体。这里`assignBlackboard=true`，并用`assignEntityBlackboard/assignPairs`传递`EntityBB_SwordNum`。因此，要将“首次三剑”变成实际总量，仍须闭合：随机位置生成、投射物到达、剑实体创建/计数更新、区域读取这份值的顺序。不能拿免费资格、白圈、请求位置数或剑数上限9替代实际输入。

## 每次伤害和最后一次六倍

姿态入口索引79的SimpleCalc先算：

```text
atk_up_final = conductCnt × atk_up_per_conduct
```

区域实体起始索引0：

```text
atk_scale_final = atk_scale + atk_up_final
```

这是附加伤害倍率，不是再乘一个常规增伤桶。满专精原生参数为`atk_scale=0.8100000023841858`、`atk_up_per_conduct=0.18000000715255737`，没有实际导电消费等级就不能默认追加值。

区域`tick_index`初值为1。12～64帧窗口按0.2秒间隔检查`tick_index < EntityBB_SwordNum`；满足时执行17/19之一的名义伤害并增加tick_index，不满足时Jump至64。69帧的单独序列重新检查`tar`目标组，再以索引25把`atk_scale_final`乘`final_rate`，由索引28执行末击。17与19是互斥分支，不能把两条DamageAction都相加。

字段类型已在本版NativeImage中核准：CompareFloat.Data.compare实际是`Beyond.CompareType`（LT=0、Equals=4），不是另一个`EConditionCompareType`；SimpleCalc.operation实际是ModifyDynamicBlackboard.OperationType（Add=1、Multiply=2）。末击节点的字面fallback为18，但`useBlackboardKey=true`，选中技能12的实际`final_rate=6`；不能拿fallback18计价。

在已提供有效的剑数N≥1、实际消费导电等级C、目标仍在各伤害范围、tick/Jump完整执行等条件下，名义结构应为：

```text
单雷单位 = atk_scale + C × atk_up_per_conduct
总倍率 = 单雷单位 × (N - 1 + final_rate)
```

N=0时区域有结束分支，不能直接套上述公式。窗口长短、delta、Jump和目标变化仍限制实际执行，表中仅用明确假设解释单雷报价少了多少，不写回现场状态：

| 假定N | 假定C | 姿态名义总倍率，float32计算 |
| --- | --- | --- |
| 1 | 0 | 486.000013% |
| 3 | 0 | 648.000002% |
| 9 | 0 | 1134.000015% |

普通态另走`normal_skill_trigger_sword`：先按实际剑实体选择，将`swordIndex`、`EntityBB_SwordNum→swordCnt`、已相加的atk_scale和final_rate传给`buff_chr_0030_zhuangfy_sword_triggerd`；后者根据实际索引/总数判末击，在索引36乘final_rate。它与姿态区域分路执行，不能只把姿态实体程序改个倍率用于普通态。

## 下一步所需输入

### 创建时的剑数快照与计数副作用（续批）

本版原生默认路径进一步核准了赋值的先后。SpawnAbilityEntity（63058/RVA62eca70）先遍历Data+0xa8的assignPairs（+0xa4启用），在62ef2fe调用CreateAssignDataPair；数值输入经ActionBlackboard.TryGetFloat后将当时float32拓宽为double写入返回pair。随后才在62efc82或62f07e3调用ObjectContainer.SpawnAbilityEntity。该列表不是输入BB的动态订阅，缺值分支返回空pair，模型仍不能据此默认剑数0。

ObjectContainer先LoadEntity，再SetSource、RegisterEntity、TryStart。SetSource分别处理sourceBlackboard（+0x2c8）与entityBlackboard（+0x2d0），冷分支760d097把上述pair列表合入后者；创建动作拿到有效实体后才调用AbilityEntityController.OnSpawn。因而区域显式EntityBB_SwordNum传值是创建前的快照，不能用之后角色被动的同名key更新自动覆盖它；尚未排除其他显式写入或IFix，亦不称区域BB绝不会改变。OwnerSpawnedEntityFinder读取来源AbilitySystem的m_children（+0x1a0），与全局RegisterEntity是不同集合，来源关系通过SetSource/_OnSetAsSource登记。实体到达、初始化、被动刷新与区域创建的实际调度相位仍待执行。

SpawnAbilityEntity.OnEnd独立检查pauseEffectOnEnd（+0xe0）与dieOnEnd（+0xe5）；后者为真才走Entity.Release冷分支。生成剑记录dieOnEnd=false不能被简化为动作结束即删除，也不证明永久存续。

另核Conditions.CheckEntityNum（96696/RVA642d5e0）：它先按过滤统计并比较，只在比较成功且storeKey非空时检查/更新BB；单精度差超过epsilon且AssignDynamic成功后创建类型7的server operation。这不是已证明的AbilitySystem监听事件。研究编译器之前忽略containsHittableTarget、excludeDeadEntity和storeKey，现已使这些未绑定行为明确未知，不授权直接序列后续资源或IfElse任一臆测分支；无过滤无输出的纯计数仍执行。没有实现新的计数producer或导入新版运行快照。

原始探针及独立验证只在主仓忽略目录zhuang-entity-flow-20261016：87个选定机器窗口、11组字段与28个指令锚点的同版摘要/逐字节/字段注册表复核通过；窗口按下一方法指针封顶32KB，含数据或相邻块，不冒充严格函数长度或IFix覆盖。新增2项回归、24专项及1735全仓（167.249秒）通过，Ruff I/F与diff-check通过。面板/报价/既有绑定数值不变，不提高完整原生覆盖口径。

### 续查：剑数实际producer已找到

`chr_0030_zhuangfy_check_sword_passive`不是有timeline动作的战技。它通过SkillData顶层`buffs`挂载`buff_chr_0030_zhuangfy_passive_check_sword`，并把`swordRange=50`传进去；只有遍历action节点会漏掉这条依赖。独立解析器已补读顶层字面buff引用，输出明确不因此推断初始化时刻。

该buff是Infinity，配置`triggerInterval=0.029999999329447746`、`waitFirstTriggerInterval=false`；事件1在本版真实枚举为OnBuffTrigger，不称OnBuffEnable或借用AI中另一个同名Event。它按序执行：

1. 查本人OwnerSpawnedEntity（类型512）中满足剑标签的实体，XZ平面距离不大于`swordRange`，保存`swordsInRange`。
2. CheckEntityNum将实际组大小写入`swordsNum`；`containsHittableTarget=false`、`excludeDeadEntity=false`，不能等同“实际可打中目标的有效剑数”。
3. NotifyCharPassiveUIAction把这个数通知被动UI。
4. ModifyDynamicBlackboard把`swordsNum`写入本人`EntityBB_SwordNum`。

因此场上本人的剑因移动超出50米、实体到期或移除都会影响计数；不能按历史累计生成次数保持到上限9。0.03秒是配置触发间隔，不是已实测的固定33Hz，也不证明与区域创建处于同一更新相位。buff初始化、触发/实体到达/区域创建与assignEntityBlackboard读取/复制的确切先后仍待实现核准。

新增两份当前记录同样经过EOF、回编码及raw/decoded摘要核验，合计13份；audit脚本进一步核这条顶层挂载、传参、实际筛选/计数/UI/BB写入顺序及buff事件枚举。没有在运行时创建默认0、3或9，没有新增UI数字识别。

仍缺实际选择剑数、导电消费等级、推荐目标/区域目标持续有效性、生成投射物到达与实体登记/移除顺序、native tick/Jump执行。当前白圈合同只覆盖对应按钮机制可用，不提供这些数量及命中事实。研究得到的是带条件的总量结构，不是运行时完整producer，也不新加识别。

本批11份记录/原始及decoded摘要、2个原生字段类型、运算/比较枚举、所选rank12参数与条件样例核验通过，脚本AST通过。首次把末段目标组写成普通态的`thunderTar`被验证拒绝，按本版区域实际`tar`修正后通过。没有借旧输出掩盖失败。32研究面板/128报价、14模型释放/2原生ATK绑定及完整程序2/111均不变。
