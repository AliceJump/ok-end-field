# Effect 语义审计与归一口径

> 分支：`codex/effect-semantics-normalization`
>
> 目标：先确认游戏里的实际对象、持有者、作用对象和消费关系，再让技能数据与运行时模型服从这些语义；禁止根据枚举名或中文子串自行脑补机制。

## 1. 语义层级

运行时 Effect 不再统一视为“状态”。`EffectKind` 分为：

| kind | 含义 | 示例 |
| --- | --- | --- |
| `pool` | 有明确整数数量/层数、上限与消费方式的资源 | 法术附着、破防层、连击、熔火、启示 |
| `state` | 持续状态或 modifier | 冻结、燃烧、脆弱、演唱姿态 |
| `entity` | 有身份/寿命/位置的实体或标记 | 自制炸弹、支援晶体、衔火血翼、雷枪 |
| `event` | 瞬时结算，不应驻留在世界状态中 | 法术爆发、猛击、追加攻击 |
| `operation` | 对已有资源/状态/实体执行消费、清除、召回、重复施加 | CLEAR_ATTACH、REMOVE_THUNDER_SPEAR |
| `predicate` | 只用于条件查询的类别/别名 | “存在任意法术附着”“存在破防” |
| `unresolved` | 当前版本没有可信 producer/consumer 的历史占位 | 旧领域、雷达、炮台等 |

`EffectOwner` 只表示状态存放范围，不替代技能的 recipient：

- `ENEMY`：敌人状态/资源；
- `TEAM`：全队共享资源；
- `ACTOR`：任意干员侧效果，具体 self/ally/team 由 `SkillEffect.target` 决定；
- `SELF`：明确属于技能拥有者本人的私有资源/姿态；
- `FIELD`：场上实体；
- `NONE`：unresolved。

## 2. 全部 EffectType 当前口径

### 元素附着与脆弱

| EffectType | kind | owner | canonical 语义 |
| --- | --- | --- | --- |
| ATTACH_COLD | pool | ENEMY | 当前敌人的寒冷附着层，0~4 |
| ATTACH_BURN | pool | ENEMY | 当前敌人的灼热附着层，0~4 |
| ATTACH_ELECTROMAGNETIC | pool | ENEMY | 当前敌人的电磁附着层，0~4 |
| ATTACH_NATURAL | pool | ENEMY | 当前敌人的自然附着层，0~4 |
| VULN_COLD | state | ENEMY | 寒冷脆弱 modifier |
| VULN_BURN | state | ENEMY | 灼热脆弱 modifier |
| VULN_ELECTROMAGNETIC | state | ENEMY | 电磁脆弱 modifier |
| VULN_NATURAL | state | ENEMY | 自然脆弱 modifier |
| VULN_PHYSICAL | state | ENEMY | 物理脆弱 modifier |
| VULN_ALL | state | ENEMY | 历史命名；实际为法术脆弱，不包含物理 |
| VULN_NATURAL_BURST | state | ENEMY | 自然爆发承伤提升 |

### 物理异常、法术异常与通用状态

| EffectType | kind | owner | canonical 语义 |
| --- | --- | --- | --- |
| STATUS_SHRED | predicate | ENEMY | “目标当前有破防层”的查询别名；真实数值只存 STACK_SHRED |
| STACK_SHRED | pool | ENEMY | 唯一破防层真值，0~4 |
| STATUS_HEAVY_HIT | state | ENEMY | 击飞物理异常及其控制结果；已破防时推进破防链 |
| STATUS_KNOCKDOWN | state | ENEMY | 倒地物理异常及其控制结果；已破防时推进破防链 |
| STATUS_SHATTER | state | ENEMY | 碎甲造成的持续物理承伤效果；触发时消费破防 |
| STATUS_HEAVY_STRIKE | event | ENEMY | 猛击结算；消费破防，不是持续状态 |
| STATUS_STAGGER | state | ENEMY | 失衡状态；与“造成 N 点失衡值”不是同一概念 |
| STATUS_CORROSION | state | ENEMY | 腐蚀法术异常 |
| STATUS_FROZEN | state | ENEMY | 冻结法术异常 |
| STATUS_CONDUCTING | state | ENEMY | 导电法术异常 |
| STATUS_BURNING | state | ENEMY | 燃烧法术异常 |
| STATUS_SPELL_INFLICT | predicate | ENEMY | “存在任意法术附着”；不得作为输出写入状态 |
| STATUS_SPELL_BURST | event | ENEMY | 同元素再次附着触发的法术爆发 |
| STATUS_SPELL_ANOMALY | predicate | ENEMY | “存在任意具体法术异常”；不得作为输出 |
| STATUS_SLOW | state | ENEMY | 缓速/减速 canonical 状态 |
| STATUS_BROKEN | event | ENEMY | 碎冰结算；枚举名保留历史兼容 |
| STATUS_FOCUS | state | ENEMY | 安塔尔“聚焦”，同一时间最多一个目标 |
| STATUS_CONFINEMENT | state | ENEMY | 诀“囹圄” |
| STATUS_ORIGINIUM_CRYSTAL | state | ENEMY | 管理员“源石结晶”，可被后续机制消费 |
| STATUS_SINGING | state | SELF | 梨诺演唱姿态 |
| STATUS_HIGH_SINGING | state | SELF | 梨诺高歌姿态 |
| STATUS_HOVERING | state | SELF | 当前只表示提弗洛斯自身浮空姿态；敌人浮空不复用该 ID |
| STATUS_TIANLI_HEZHEN | state | SELF | 庄方宜“天理合真”，持续 25 秒；改写普攻、战技与连携技 |
| STATUS_TIANLI_FIRST_SKILL_READY | state | SELF | 天理合真期间首次惊霆诀的一次性强化标记；首战技后消费 |

### 角色资源、队伍资源、场上资源

| EffectType | kind | owner | canonical 语义 |
| --- | --- | --- | --- |
| STACK_MOLTEN | pool | SELF | 莱万汀熔火，0~4 |
| STACK_IRON_OATH | pool | SELF | 骏卫铁誓，终结技生成 5 点，持续 30 秒并逐点消费 |
| STACK_BLOOD_WING | entity | ENEMY | 历史 STACK 命名；实际是卡缪衔火血翼盘桓目标的可转移实体/标记 |
| STACK_COMBO | pool | TEAM | 队伍共享连击，0~4；下一发战技/终结技消费 |
| STACK_MORALE | pool | ACTOR | 士气激昂，挂在具体干员，最多 3 层、每层独立计时 |
| STACK_WHIRLPOOL | pool | FIELD | 汤汤场上涡流计数投影，最多 2；完整状态还要保留实体寿命/位置 |
| STACK_TRACE | state | ENEMY | 历史 STACK 命名；洛茜爪印斫痕，无法叠加，来自天赋 |
| STACK_QINGTING_SWORD | pool | FIELD | 庄方宜青霆剑场上实体计数，最多 9；一次战技最多生成 3 |
| STACK_SIGN | pool | SELF | 提弗洛斯启示，0~8 |
| STACK_HUNTING_ARROW | pool | SELF | 提弗洛斯猎矢，0~4 |
| MECH_BOMB | entity | ENEMY | 萤石自制炸弹；附着某个敌人，场上同时最多一个 |
| MECH_SUPPORT_CRYSTAL | entity | FIELD | 赛希支援晶体；20 秒、绑定主控、最多 2 次回复 |
| MECH_THUNDER_SPEAR | entity | FIELD | 艾维文娜普通雷枪；连携生成 3 支，默认 30 秒 |
| MECH_STRONG_THUNDER_SPEAR | entity | FIELD | 艾维文娜强雷枪；终结技生成 1 支，默认 30 秒 |
| MECH_GRAVITY | entity | FIELD | 洁尔佩塔持续重力场区域 |
| MECH_VACUUM | event | ENEMY | 瞬时牵引控制 |

### 干员侧 buff / heal

| EffectType | kind | owner | canonical 语义 |
| --- | --- | --- | --- |
| BUFF_ATTACK_UP | state | ACTOR | 攻击力提升；recipient 由 target 决定 |
| BUFF_CRIT_RATE_UP | state | ACTOR | 暴击率提升 |
| BUFF_CRIT_DMG_UP | state | ACTOR | 暴击伤害提升 |
| BUFF_SHIELD | state | ACTOR | 护盾实体的简化入口；完整模型还应携带 shield value/type |
| BUFF_HEAL | event | ACTOR | 治疗结算，不是持续 buff |
| BUFF_SPEED_UP | state | ACTOR | 移速提升 |
| BUFF_DAMAGE_UP | state | ACTOR | 通用伤害增幅 |
| BUFF_COLD_UP | state | ACTOR | 寒冷增幅 |
| BUFF_BURN_UP | state | ACTOR | 灼热增幅 |
| BUFF_ELECTROMAGNETIC_UP | state | ACTOR | 电磁增幅 |
| BUFF_NATURAL_UP | state | ACTOR | 自然增幅 |
| BUFF_SPELL_UP | state | ACTOR | 法术增幅 |
| BUFF_PROTECTION | state | ACTOR | 庇护 |

### 操作与结算事件

| EffectType | kind | owner | canonical 语义 |
| --- | --- | --- | --- |
| PLACE_THUNDER_SPEAR | operation | FIELD | 历史生成动作 ID；新数据应直接生成具体普通/强雷枪实体 |
| REMOVE_THUNDER_SPEAR | operation | FIELD | 召回全部普通/强雷枪 |
| CONSUME_ALL | operation | ENEMY | 消费某个明确资源的全部；调用方必须给出资源对象 |
| CONSUME_STACK | operation | ENEMY | 消费某个明确资源 N 层；调用方必须给出资源对象 |
| CLEAR_ATTACH | operation | ENEMY | 清除当前法术附着 |
| CLEAR_STATUS | operation | ACTOR | 清除干员可被清除的异常；不能理解成删除所有世界状态 |
| CLEAR_COLD | operation | ENEMY | 清除/消费寒冷附着 |
| CLEAR_NATURAL | operation | ENEMY | 清除/消费自然附着 |
| CLEAR_FROZEN | operation | ENEMY | 消费冻结 |
| TRIGGER_LINK | event | TEAM | 连携开放/触发事件；后续模型应携带具体技能 |
| TRIGGER_ADDITIONAL | event | ENEMY | 追加攻击结算；后续模型应携带 attack payload |
| TRIGGER_EXPLOSION | event | ENEMY | 爆炸结算 |
| TRIGGER_HEAL | event | SELF | 历史治疗触发事件；新机制优先直接使用 BUFF_HEAL/HealOutcome |
| TRIGGER_SHIELD | event | SELF | 历史护盾触发事件；新机制优先生成具体 Shield |
| TRIGGER_REPEAT_EFFECT | operation | ENEMY | 再次施加当前已有的具体附着/状态，必须继承原 payload |

### Legacy / unresolved

以下 ID 在当前角色技能/天赋语义中没有可信 producer/consumer，或已经有 canonical 替代。它们不得参与自动文本推断，也不得由当前技能数据产出：

- `STACK_SEED`
- `STACK_CHARGE`
- `DEBUFF_DEF_DOWN`
- `DEBUFF_SPEED_DOWN`（canonical：`STATUS_SLOW`）
- `DEBUFF_HEAL_DOWN`
- `MECH_FREEZE_FIELD`
- `MECH_FIRE_FIELD`
- `MECH_LIGHTNING_FIELD`
- `MECH_NATURE_FIELD`
- `MECH_RADAR`
- `MECH_TURRET`

## 3. 已确认的数据修正

- 黎风连携真实产出 `STACK_COMBO +1`，归属 TEAM。
- 汤汤连携生成场上涡流，不再错误施加寒冷附着。
- 庄方宜青霆剑归属 FIELD，场上上限 9；单次最多生成 3 只是单次生成限制。
- 庄方宜终结技不再错误地在 cast 当下产出电磁附着；改为 25 秒 `STATUS_TIANLI_HEZHEN` + 一次性首战技强化标记。电磁附着归到终结技期间的惊霆诀分支，首个惊霆诀固定生成 3 柄青霆剑；“0 技力/不消耗导电”的覆盖规则留给 action transition 执行。
- 汤汤战技将涡流消费显式写成 `CONSUME_ALL(subject=STACK_WHIRLPOOL)`；多个水龙卷的法术脆弱从基础效果移入“存在涡流”分支，持续 15 秒。每消费 1 处涡流返 20 SP 已保留为明确机制语义，资源结算进入下一层 ActionOutcome。
- 赛希连携技在回复次数耗尽后会实际消费支援晶体实体，再施加寒冷附着。
- 骏卫铁誓归属骏卫自身，终结技生成 5 点、持续 30 秒。
- 卡缪衔火血翼改为 enemy entity，持续 45 秒，不再伪装成 stack。
- 卡契尔“施加 1 层破防”改为 `STACK_SHRED +1`，不再产出 predicate `STATUS_SHRED`。
- 萤石/诀的“再次施加当前法术附着”改为 `TRIGGER_REPEAT_EFFECT`，不再产出 `STATUS_SPELL_INFLICT`。
- 狼卫燃烧/导电分支拆开，消耗具体异常；无异常时才施加灼热附着。
- 洛茜第二段连携的“清空附着 → 基于层数追加伤害 → 击飞 → 15 秒暴击增益”从释放条件中拆为真实 outcome。
- 赛希支援晶体补 20 秒生命周期，全队终结技增幅 target 归一为 team、持续 12 秒。
- 艾维文娜普通雷枪和强雷枪拆为两个 FIELD entity；连携 3 支/30 秒、终结技 1 支强雷枪/30 秒，战技只做召回。
- 洁尔佩塔终结技显式产出 5 秒 `MECH_GRAVITY` 场地实体，不再只有缓速/法术脆弱而缺少区域本体。
- 提弗洛斯缓速统一为 `STATUS_SLOW`。
- 莱万汀 4 熔火条件使用 counted trigger；强制燃烧补 5 秒持续时间。
- 大潘 4 层破防、弭弗 3 层破防、别礼 3 层寒冷附着、萤石 2 层附着、提弗洛斯 8 启示等条件不再压成布尔值，统一使用 `min_count`。

## 4. JSON canonical schema

所有 `character_skills/*.json` 已统一以下规则：

1. `effects` 永远是数组；没有输出时为 `[]`，不再混用 `null`/缺字段。
2. `enhancements` 永远是数组；没有分支时为 `[]`。
3. 每个 effect 显式包含：
   - `value`
   - `duration`
   - `target`
   - `count`
4. 未知/动态值写 `null`，绝不由 loader 自动补成 `0`、`1`、`enemy` 或空字符串。
5. 动态事件/无法用 EffectType 表达的触发条件写 `trigger_condition.effects: null`，不能使用 `{"all":[]}` 表示“永远满足”。
6. 有层数门槛的条件统一使用：
   ```json
   {"effect_id": "STACK_SHRED", "min_count": 3}
   ```
7. predicate 只能出现在 trigger requirement，不能作为技能 output。
8. unresolved effect 不能进入自然语言自动推断，也不能作为当前技能 output。

## 5. 当前数据覆盖与第二阶段边界

2026-10-03 已为 32 个角色接入独立的 CharacterProgression：保留 127 条战斗天赋阶级记录、160 条潜能定义，基准选择 64 个最高阶战斗天赋。原始 modifier、条件与 producer 引用均保留，补充的 26 个 BuffData 已通过逐字节回编码验证。资源审计索引覆盖 325 个原始记录中的 481 处候选动作；这是可追溯的审计覆盖，并不等于 481 条已执行的资源回复。技能、天赋与潜能描述中明确的资源语句优先转成统一规则；最高阶天赋及全部潜能的62条资源相关定义已归类为回复、倍率/消耗修改或监听。原生条件树用于补充描述缺失的次数与顺序，剩余通用参数及状态绑定仍需继续核对。

第一阶段仍需核对原生 modifier 与统一效果的绑定、补足动态公式、通用资源参数及监听规则。天赋数据进入 loader 不代表其所有被动效果已参与排轴结算。

### 当前仍需进入第二阶段模型的内容

本阶段只把**语义和数据真值**校正，并未假装已经实现持续战斗世界状态。下面这些仍需要由下一阶段 `TeamCombatState` / action transition 实现：

- 每个敌人独立的附着、破防、异常、斫痕、血翼、炸弹等状态；
- 干员私有层数、buff 的独立持续时间；
- 涡流、青霆剑、雷枪、晶体等场上实体的寿命/数量/位置；
- 法术附着“重复当前元素”的 payload 传播；
- 互斥 action branch 和动态 formula；
- SP refund / ult energy refund；
- 技能对未来 action 集合的边际价值。

## 6. 本地原始资源与交叉核对来源

优先使用当前安装客户端的 VFS 表、SkillData/BuffData 完整条件树和同版本中文文本；以 GameAssembly/metadata 哈希固定解码布局。Wiki 用于核对名称、展示说明与版本差异，不能覆盖已验证的原始分支条件。旧本地中文表缺少佩尔希等新增角色文本，本次已从当前 VFS 重新解包 I18nTextTable_CN。

天赋与潜能的覆盖、基准口径、补解包流程及剩余工作详见 [本地成长数据审计](native-progression-audit.md)。普通六星 P0，四/五星 P5，战斗天赋取各槽最高阶；管理员使用共享 chr_9000_endmin 的当前定义，暂按有效定义上限 P3 建模，免费材料获取链尚未全部闭合。

Wiki 交叉核对入口：

- 官方战斗教程：https://endfield.games/zh-Hans/tutorials/battle/
- 艾维文娜（GameKee）：https://www.gamekee.com/zmd/691016
- 庄方宜（GameKee）：https://www.gamekee.com/zmd/tj/701601.html
- 洛茜（华法琳 Wiki）：https://warfarin.wiki/cn/operators/rossi
- 提弗洛斯（华法琳 Wiki）：https://warfarin.wiki/cn/operators/typhoeus
- 梨诺（终末地 Wiki）：https://end.canmoe.com/zh-CN/wiki/characters/chr_0035_liino
- 艾尔黛拉（灰机 Wiki）：https://endfield.huijiwiki.com/wiki/%E8%89%BE%E5%B0%94%E9%BB%9B%E6%8B%89

当来源互相冲突时，不把攻略推断写进 canonical effect；保持 `null` 或 `unresolved`，等待官方文本/可重复实测确认。
