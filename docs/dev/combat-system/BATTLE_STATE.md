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
      infliction_element: heat|cold|electromagnetic|natural|null  # 任意时刻最多一种法术附着
      infliction_stacks: 0-4       # 同元素叠层；异元素反应消耗全部已有附着
      infliction_time_left: float # 首次/同元素附着刷新为20s；到期清空元素和层数
      vulnerable: {stacks: 0-4}    # 物理破防层 [已确认]
      solidified: bool             # 固结（中小型敌人）[已确认]
      electrified: bool
      corrosion: {t_left}
      combustion: {t_left}         # DoT
      dot_effects: {effect: DoTRef} # 事件编排器保存source/level/generation/expires_at/period；与计时容器分离
      buffs: [BuffRef]             # 增幅/脆弱/易伤/弱化/庇护(我方)…

  global:
    events: [Event]                # 事件日志（见 §2）
    damage_modifiers:              # 当前生效的乘区快照（调试用）
```

## 2. Event Queue（时间轴事件）

模拟器**只推进事件**，伤害是事件的产物。事件类型（含本地排轴 token 对应）：

```
CastSkill(char, skill)          # 按当前技能/阶段的实际技力消耗扣费；常见100，特殊阶段可为0或其他值
HitEnemy(char, skill, enemy, t) # 命中：伤害结算+附着+失衡+充能（结算帧 ? 未确认）
GainSP(amount, source)          # 重击/处决/返还/极限闪避
                               # 未知重击回复量不生成GainSP；定量来源和命中必须确认
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
ApplyDoT(enemy, effect, source, level, duration, period, t) # 由明确的持续伤害效果/反应产生
DoTTick(enemy, effect, source, level, generation, t)       # 定时结算，按当前面板、不暴击
ClearDoT(enemy, effect)          # 净化/移除；使已入队的旧版本tick失效
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

`EnemyCombatState.tick(dt)` 只负责计时和状态过期，不计算伤害或入队。模拟器的事件编排器在施加DoT时保存来源、异常等级、截止时间和版本，并安排首个结算事件；清除或重新施加时旧版本事件失效。燃烧按社区口径每秒一跳、持续10秒，倍率口径仍须按 `ROTATION_REQUIREMENTS C4` 的假设选择。截止时刻已到点的末次tick由DoT记录结算，即使计时容器已移除到期状态；之后删除记录，不再安排下一次。

```python
while not battle_over:
    ev = event_queue.pop()
    dt = ev.time - state.time
    for enemy in state.enemies:
        enemy.tick(dt)              # 命中前推进附着/持续状态计时，避免过期附着触发反应
    regen_sp(dt)                   # 自然恢复约8点/秒
    state.time = ev.time
    apply_state_changes(ev)          # SP/能量/附着/层数/CD
    if ev is ApplyDoT:
        dot = register_dot(enemy, ev)  # 新generation；expires_at=ev.time+duration，保留source/level
        enemy.dot_effects[ev.effect] = dot
        if ev.time + dot.period <= dot.expires_at:
            event_queue.push(DoTTick.from_dot(dot, ev.time + dot.period))
    if ev is ClearDoT:
        enemy.dot_effects.pop(ev.effect, None)
    if ev is DoTTick:
        dot = enemy.dot_effects.get(ev.effect)
        if dot and ev.generation == dot.generation and ev.time <= dot.expires_at:
            enemy.hp -= compute_dot_damage(state, ev)  # 当前source面板，使用记录的异常等级，不暴击
            next_tick = ev.time + dot.period
            if next_tick <= dot.expires_at:
                event_queue.push(DoTTick.from_dot(dot, next_tick))
            else:
                enemy.dot_effects.pop(ev.effect, None) # 末次结算后停止；旧版本/取消的事件不扣血
    if ev is HitEnemy:
        dmg = compute_damage(state, ev)   # DAMAGE_FORMULA 全乘区
        enemy.hp -= dmg; enemy.stagger += skill.stagger
        for effect in skill.effects:
            if effect in ATTACH_ELEMENTS:  # 仅显式 ATTACH_* 效果；伤害元素不等于附着
                enemy.apply_infliction(effect)
        char.energy += skill.energy_gain
        if enemy.stagger >= enemy.stagger_max:
            trigger StaggerBreak
    if ev is BasicAttack and enemy.staggered and not enemy.finisher_used:
        convert to Finisher          # 处决倍率 + 回技力
    check_link_triggers(state)       # 5s 窗口开启
```
