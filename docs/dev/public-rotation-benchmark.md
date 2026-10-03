# Prydwen 排轴对照：莱万汀队

参考 Prydwen 莱万汀攻略的 Single Target Team，队位统一为莱万汀、狼卫、安塔尔、艾尔黛拉。此文档只用于离线对照当前调度模型，不把站点轴或本地模型视为实战 DPS 证明。

## 当前调度语义

时间排轴现在区分三类时间：

- `exclusiveFrame`：硬锁，结束前绝不发送下一动作；
- `AllowNextSkillAction`：候选动作明确匹配时，在硬锁结束后可直接接续；
- `actionable`：没有匹配白名单时的兜底接续点，取 `min(duration, max(0.3, exclusive) + 0.15s)`。

完整 `duration` 仍用于描述作者时间轴，但不再默认作为输入锁。Alt 终结技在时间排轴模式中也不再同步等待头像全未知后重新恢复；旧模式与长按终结技保持原行为。

## 测试范围

`scripts/skill-data/benchmark_public_rotation.py` 会运行同队的本地模型计算，并直接回放 `TimedCombatLogic.step()`。回放只模拟已接受按键、技力消耗和名义自然恢复，没有敌人、真实伤害、普攻命中、额外技力或终结技能量。

输出会同时记录：

- 完整原生 `duration`；
- `exclusive` 硬锁；
- 调度器 `actionable`；
- 首次恢复普通攻击输入时间；
- 战技循环窗口与当前模型的预计伤害。

这些数字用于发现“时间轴保护是否过长”，不能叫实战 DPS。

## 与公开轴的差异

公开轴包含队友附着、重击吸收、连携攒层、支援持续时间和终结技对齐。当前优化器只对可建模的战技有序子集计分，仍有几个明确缺口：

- 熔火、充能等强化前置状态没有完整状态机；
- 支援技能的增伤收益和刷新时点没有完整计价；
- 连携释放者仍无法从现有监测唯一识别；
- `exclusive/actionable` 是数据驱动的调度边界，但仍需要实战验证个别技能是否可在该点安全接续。

因此脚本输出只用于同一模型内部比较，不再把某个固定百分比写成结论。

## 复现

```powershell
uv run --locked python -X utf8 scripts/skill-data/benchmark_public_rotation.py --output tmp/rotation-benchmark/prydwen-laevatain.json
uv run --locked python -X utf8 scripts/skill-data/benchmark_public_rotation.py --regen 6
uv run --locked python -X utf8 scripts/skill-data/benchmark_public_rotation.py --regen 10
```

`--output` 将报告保存到固定的 `tmp/rotation-benchmark/prydwen-laevatain.json`；也可省略参数值。脚本仅比较上述固定队伍，因此不接受其他输出路径。

下一步应直接用游戏内日志比较：战技确认间隔、终结技到下一动作的间隔、实际循环窗口，以及是否出现技能被过早打断。
