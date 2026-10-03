# 05 · BATTLE_STATE — 可程序化的战斗状态定义与事件队列

> 目标：给模拟器一份可直接落地的 State / Event 定义。字段数值来源见各节标注；`?` 表示 `[未确认]`。

## 1. BattleState

```yaml
BattleState:
  time: float                      # 战斗时间（秒），tick 粒度建议 0.05~0.1s [社区参考：Dim-Halo 模拟器用 0.1s]

  team:
    sp: float                      # 全队共享技力，上限300，脱战回/损至200 [已确认]
    sp_regen_rate: 100/12.5        # 战斗中自然恢复 [社区测试]
    link_stacks: int               # 连击层数 0-4 [已确认]
    active_buffs: [BuffRef]        # 队伍级 Buff（长息/拓荒套装等）

  characters:                      # 4 名，index=位置1-4
    - id: str                      # 本地数据 name（中文）
      is_on_field: bool            # 主控与否
      hp: float
      energy: float                # 终结技能量（独立）[已确认]
      energy_max: float            # 本地 rank_stats「所需终结技能量」
      attack: float                # 由公式合成：base_stats + 武器 + 词条 [已确认公式]
      base_stats:                  # 本地 base_stats（1/20/40/60/80/90 级六维）
      crit_rate: 0.05              # 基础5% [已确认]
      crit_dmg: 0.50               # 基础50% [已确认]
      skill_state: str             # 常态 / 强化态（如提弗洛斯变身、噗切娜立盾替换技）
      cooldowns: {skill_id: t}     # 连携技 CD（≤20s，脱战重置）[已确认]
      link_ready: bool             # 连携条件是否满足（5s 窗口）[已确认]
      buffs: [BuffRef]

  enemies:                         # 通常 1 主目标 + 杂兵
    - id: str
      tier: common|advanced|elite|alpha|boss   # 决定终结倍率 1/1.25/1.5/1.75 [已确认]
      hp: float
      def: 100                     # 固定100不随等级 [已确认]
      res: {physical: 0-50, arts: 0-50}        # D=0/C=20/B=50 [已确认]
      stagger: float               # 失衡值当前
      stagger_max: float
      staggered: bool              # 失衡中 → 承伤×1.3，普攻=处决 [已确认]
      finisher_used: bool          # 本次失衡是否已消耗处决 [已确认机制]
      arts_inflictions: {heat: {stacks: 0-4, t_left}, ...}  # 20s 刷新，4层上限 [已确认]
      vulnerable: {stacks: 0-4}    # 物理破防层 [已确认]
      solidified: bool             # 固结（中小型敌人）[已确认]
      electrified: bool
      corrosion: {t_left}
      combustion: {t_left}         # DoT
      buffs: [BuffRef]             # 增幅/脆弱/易伤/弱化/庇护(我方)…

  global:
    events: [Event]                # 事件日志（见 §2）
    damage_modifiers:              # 当前生效的乘区快照（调试用）
```

## 2. Event Queue（时间轴事件）

模拟器**只推进事件**，伤害是事件的产物。事件类型（含本地排轴 token 对应）：

```
CastSkill(char, skill)          # 战技，扣100技力 [已确认]
HitEnemy(char, skill, enemy, t) # 命中：伤害结算+附着+失衡+充能（结算帧 ? 未确认）
GainSP(amount, source)          # 重击/处决/返还/极限闪避
CastLinkCombo(char)             # 连携技（按E，窗口5s）→ 排轴 token "e"
CastUltimate(char)              # 终结技 → 排轴 token "ult_N"
BasicAttack(char, seg)          # 普攻第N段 → 排轴 token "normal_N"
Finisher(char, enemy)           # 处决（失衡首普攻）
StaggerBreak(enemy)             # 失衡开始（承伤1.3生效，处决窗口开启）
ArtsBurst(enemy, element)       # 同元素爆发
ArtsReaction(enemy, type, level)# 反应（燃烧/导电/固结/腐蚀/碎冰），消耗附着层
VulnerableConsumed(enemy, n)    # 猛击/碎甲消耗破防层（触发骏卫类连携）
SwapChar(from, to)              # 切人换位
Interrupt(enemy)                # 打断蓄力（+失衡）
Wait(t)                         # 排轴 token "sleep_N"
```

排轴序列示例（对应 AutoCombatLogic 已有 token 语法）：

```
0.00s normal_1        # 普攻
0.80s CastSkill(1)    # 1号位战技（-100 SP）
0.95s HitEnemy        # 命中：附着+充能+失衡
1.20s e               # 连携技窗口内释放
1.60s ult_2           # 2号位终结技
...
```

## 3. 本地仓库数据 → State 字段映射

| State 字段 | 本地数据源 | 说明 |
|---|---|---|
| characters[].base_stats | `assets/data/character_skills/*.json → base_stats` | 1-90级六维（WIKI属性面板） |
| 技能倍率/失衡值/CD/技力 | 同上 `skills[].rank_stats`（12级全表） | `scripts/skill-data/fill_skill_rank_stats.py` 生成 |
| 技能描述/效果标注 | `skills[].description / effects / enhancements` | effects.py 的效果 ID 体系 |
| 技力消耗/返还/能量 | rank_stats 行「技力消耗」「返还技力」「所需/获得终结技能量」 | 30 技能全覆盖 |
| 处决倍率 | 普攻 rank_stats「处决攻击倍率」 | 32 普攻全覆盖 |
| 敌人数值 | `[缺]` 敌人 HP/DEF/RES/失衡槽/失衡值倍率 未入库 | 需新数据源 |
| 动画时间 | `[缺]` 全部 `[未确认]` | 需逐技能实测/帧分析 |
| 装备/武器 | `[缺]` | 可参考 B站WIKI 装备图鉴批量入库 |

## 4. 模拟器最小可行循环（伪代码）

```python
while not battle_over:
    ev = event_queue.pop()
    apply_state_changes(ev)          # SP/能量/附着/层数/CD
    if ev is HitEnemy:
        dmg = compute_damage(state, ev)   # DAMAGE_FORMULA 全乘区
        enemy.hp -= dmg; enemy.stagger += skill.stagger
        apply_infliction(enemy, skill.element)
        char.energy += skill.energy_gain
        if enemy.stagger >= enemy.stagger_max:
            trigger StaggerBreak
    if ev is BasicAttack and enemy.staggered and not enemy.finisher_used:
        convert to Finisher          # 处决倍率 + 回技力
    check_link_triggers(state)       # 5s 窗口开启
    regen_sp(dt)                     # 100点/12.5s
```
