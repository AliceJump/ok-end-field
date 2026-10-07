# 逐队机制数据与执行缺口台账

## 当前范围

用户最新要求：逐队补齐数据类可获得证据的缺口，实际识别类缺口不补。技能、满级天赋、约定潜能、原生条件/资源/状态/动作规则和模拟执行均在当前范围；画面识别、连携角色/阶段检测、逐击检测、目标身份观测、HUD阈值及在线观测缺口恢复只登记。技力返还时机继续后置。

先完成第一队的数据与执行依赖，再推广下一队。固定属性/装备归面板，六星默认零潜、主角按约定可达进度、其他星级满潜；余烬80级/技能9级/推荐五星武器。缺乏证据的规则继续未知，不能为了让整队通过而删诊断。

## 第一队：弭弗 / 骏卫 / 余烬 / 卡缪

开场基线见[实际阻塞分析](mechanism-blocker-plan.md)：15个程序、7个战技候选，只有4个基础战技可用。初始目录3条诊断，完整树仍2/111；本队静态完整的卡缪追猎开场不可用。队伍尚未完成。

| 项目 | 当前状态 | 后续动作 |
| --- | --- | --- |
| 卡缪消耗/吸收灼热附着的高级事件标签条件 | 已绑定tag查询、明确Buff事件上下文与加载配置的边界；两条条件编译诊断关闭 | 原生OnConsumeBuff/OnAbsorbBuff的实际生产/分派链仍待补；不能将判断规则完成当作连携全链已完成 |
| 余烬受伤连携的DamageDecorateMask条件 | 未绑定，仍为本队唯一启动编译诊断 | 核验掩码比较与伤害事件上下文，补模型内受伤事件合同；无需新增画面检测 |
| 主控与位置目标组、选择器数量 | 已绑定CharacterTeamFinder+单个无参数MainCharacterValidator；位置/数量等仍有缺口 | 主控目标按当时存活成员筛选，不等同施法者；实时位置输入保留，不猜距离/命中 |
| ComboCache、Switch、Jump、Interrupt | 输入窗口/选技/控制流未执行 | 按本队实际路径核验并绑定；不能作为表现节点整体忽略 |
| Curve和距离输出 | Curve字节结构可解码，运行时与消费者未闭环 | 先追本队全部消费者；纯表现可证明排除，影响结果的部分必须执行或保留未知 |
| 弭弗换段/跨技能buff | 部分状态已执行，明确CastEnd API已有，真实原生结束生产者缺失 | 补模型内生产/消费及状态身份；现场结束时点识别另列 |
| 骏卫破防消费、铁誓及士气 | 破防事件和部分SP链已有，逐层独立buff/recipient仍有缺口 | 补本人/队友归属、层与计时及必要天赋，按前后状态回放 |
| 余烬庇护参数/监听 | 基础战技执行还报Invalid native buff parameters及Shelter/事件监听等 | 从原生duration/BB赋值与生命周期核准，随后补规则，不按错误诊断猜常量 |
| 卡缪投射物、追猎、血翼与被动 | 普通战技投射物及生命周期未闭环，追猎程序只有静态事件树干净 | 补发射/命中及实体数据生产链、真实替换条件、天赋/潜能规则 |
| 增幅、脆弱、长期资源/解锁价值 | 已有部分伤害处理器，整队循环尚不可严格计价 | 先闭合必要伤害与状态规则，再与可信长序列比较 |
| 连携角色/阶段、普攻逐击、目标身份、HUD缺口恢复 | 实际识别类，按用户要求后置 | 只登记；不修改识别代码，不以这些缺口阻止可独立推进的数据核验 |

## 卡缪高级Buff事件条件证据（本批）

`data_chr_0033_camille`的两条条件分别监听211=OnAbsorbBuff与208=OnConsumeBuff，启用的是CheckBuffIdInContextAdvanced，checkType=1、blackboardKey为空，queryType=0=HasAny，标签-1558844517对应`Skill/Character/Common/SpellInflict/FireInflict`。同序列CheckTagMatch节点isEnable=0，不能算作必须成立的目标条件。

原生ExecuteInternal取得Buff事件上下文，在偏移72读取事件buff ID。tag分支调用BattleDataLoader.TryGetBuff，再读取BuffData偏移104的applyTags，交给MatchesQuery。它检查事件配置，不依赖该buff在目标身上仍有几层；消耗后的查询不能改查当前附着池。动态ID引用与非空blackboardKey的匹配后字符串写回是另外两条未绑定路径，继续未知。

| 核验窗口 | RVA | 长度 | SHA256 |
| --- | --- | --- | --- |
| CheckBuffIdInContextAdvanced.ExecuteInternal | 0x649eddc | 3000 | 5ad4476163351935706673cc9cc6935c990fb16b7614e8ac2d3624d4ed33e0d6 |

以上是校验读取窗口，不将长度当实际方法长度；GameAssembly和metadata哈希与已发布native_inputs一致。只保存证据摘要，研究脚本和原客户端路径不发布。

编译器使用明确Buff上下文标记，缺上下文返回false；上下文存在但该世界未加载BuffData标签时保留缺输入诊断，避免Except查询误把未知配置当空标签。四种tag模式及空查询沿用已核验GameplayTag合同。CharacterData的显式buff事件分派携带已加载配置；未新增实际识别或伪造消耗/吸收事件。

8项新增专项覆盖真实两条件、消费后池为空、异元素/无标签配置、未知配置、非Buff上下文、全部tag模式/空查询、未实现ID/输出路径及代表队目录边界。39项相关回归通过；全仓1313项通过（81.717秒）。全程序复测仍2/111；本队目录诊断3→1，剩余余烬掩码条件。动作执行、事件生产链和整队计价仍未完成。

## 第一队共享主控目标选择（续作）

四人的基础战技实际使用CharacterTeamFinder与单个MainCharacterValidator，validator配置无参数，没有额外postProcessor。原生FindTargetWithoutCenter从squadMembers中取alive成员；MainCharacterValidator逐项Lock目标，与GameUtil.mainCharacter指针比较并移除非主控目标。这是已有世界输入下的选择规则，未新增主控识别。

编译器绑定这一个确定组合为squad_main，执行时按当时存活队员筛出已有main_control；未绑定/非成员/死亡主控返回空集合，不回退到施法角色。普通CharacterTeamFinder绑定living_squad，只筛存活成员，不改变直接squad资源目标的原有语义。其他validator、postProcessor或未验证参数仍拒绝，空间点选择器仍未知。

| 核验窗口 | RVA | 长度 | SHA256 |
| --- | --- | --- | --- |
| CharacterTeamFinder.Data.FindTargetWithoutCenter | 0x40a1610 | 2000 | 36965f976668a6d07d7668f8b4f420304c86b1f4b4c86d9c595d13a39b613eec |
| MainCharacterValidator.Data.Validate | 0x3bcb3c0 | 2000 | 23904ef17c677a1456e7971c01409cbe1556b824c3a7e61413718f9ca8486b72 |

窗口和native_inputs校验口径同上。7项新专项验证四个真实节点、事件时点主控变更、空/死亡目标、存活队员筛选、未知过滤边界及预测隔离。43项相关回归通过。全程序树仍2/111；本队四个基础战技静态诊断分别从12/5/8/5降到11/4/7/4（弭弗/骏卫/余烬/卡缪），替换动作缺口仍4/7/0。全局mainchar/MainChar过滤类别已从本次审计结果中消失，其他目标过滤、控制流和事件生产链未闭环。

本批全仓1320项通过（78.837秒），Ruff I/F、修改文件解析/敏感路径检查与git diff --check通过。没有改动实际识别，第一队仍未完成。

## 后续队伍

第一队的数据依赖完成后，按已有阵容资料和共享机制选择下一队，优先复用已核准规则，再覆盖异常、场地、召唤、姿态等不同机制；已核对角色只补新队交互。每队分别报告数据缺口、实际执行缺口、明确后置识别项和离线回放结果；全角色台账仍为最终范围。
