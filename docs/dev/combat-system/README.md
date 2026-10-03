# 战斗系统调查（Combat System Specification）

> 目标：为 Battle Simulator / Damage Calculator / Rotation Optimizer 提供可程序化实现的规则资料。
> 调查日期 2026-09-24；机制均标注可信度（已确认/社区测试/单源/未确认/存在冲突），不编造数据。

| 文档 | 内容 |
|---|---|
| [COMBAT_SYSTEM.md](COMBAT_SYSTEM.md) | 战斗循环、普攻/重击/战技/连携/终结技、技力与能量系统、切人与后台 |
| [DAMAGE_FORMULA.md](DAMAGE_FORMULA.md) | 完整伤害公式、15 个乘区定义与叠加方式、防御/抗性/暴击数值 |
| [EFFECT_SYSTEM.md](EFFECT_SYSTEM.md) | 五元素、附着/爆发/反应组合表、物理异常链、DoT、Buff 分类、触发器模型 |
| [EQUIPMENT_SYSTEM.md](EQUIPMENT_SYSTEM.md) | 装备部位/主词条实测表/套装效果表、乘区归属、Build 对比 |
| [BATTLE_STATE.md](BATTLE_STATE.md) | 可程序化 BattleState 定义、Event Queue、本地数据字段映射 |
| [ROTATION_REQUIREMENTS.md](ROTATION_REQUIREMENTS.md) | 已确认机制清单、本地数据资产、数据缺口 P0-P3、编排器设计约束 |

## 主要来源

- 灰机wiki《伤害》《分类:战斗》与 endfield.wiki.gg《Damage》（互证）
- NGA《终末地数值学导论》《基础伤害乘区》（实测）
- GameWith / Mobalytics / game8（元素反应矩阵，四源一致）
- B站WIKI 装备图鉴（套装/词条精确数值）
- 开源实现：Dim-Halo/Endfield_Simulation、wxhwwla/calc-framework、Taksumii/Arknights-Endfield-DPS-Calc
- 本地：`tools/wiki_catalog` 官方快照、`assets/data/character_skills`（rank_stats/base_stats）
