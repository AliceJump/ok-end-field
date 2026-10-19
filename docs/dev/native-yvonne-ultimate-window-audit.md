# 伊冯冷冻射手：私有暴击层与结束攻击（2026-10-20）

本批核查第二角色的10份原始记录，不启用新的伊冯伤害或默认满层。构建与[莱万汀计时审计](native-laev-paused-clock-audit.md)相同；脚本、逐字解码和摘要只在主仓ignored研究目录。后续从这些真实记录补生产、更新、消费及清理。

## 等级输入与互斥分支

已审核等级12终结参数是 `atk_scale1≈0.2`、`atk_scale2=3`、`atk_scale_extra=6`、`duration=7`。原始SkillData字面却有0.23/1.35/4/12秒，BuffData又有零值或其他备用值；不能用它们替换20%普攻、300%强力攻击、冻结条件600%追加及7秒等级参数。

`chr_0017_yvonne_ult_attack_end`包含多份相似DamageAction，属性处理器和条件分支不同，不能遍历后把全部300%/600%节点相加，也不能把冻结条件追加改成无条件900%。真实目标、条件、继承BB和输出时点继续逐路径核准。

## BB名字不能决定乘区

`buff_chr_0017_yvonne_ultimate_skill_layer` 当前数据：

| 字段 | 已知事实 | 仍不能推定 |
| --- | --- | --- |
| normal_dmg_up=0.03 | 写attributeType9 | 不是按名字当“普攻伤害+3%” |
| crit_rate_up_dynamic=0 | 写attributeType10 | 不是按名字当额外暴击率 |
| crit_rate_up=0.06 | 增强回调在计数至少10时写入上述dynamic键 | 不是第一下普攻就获得最大暴伤 |
| stackingType8、maxStackCnt10 | 增强/刷新类型和上限声明 | 不是10个独立实例，也不是总共只射10次 |
| buffEvent6 | OnBuffEnhanced回调 | 不等于每次终结施放都触发 |
| recycle_time=4 | 原始私有BB输入 | 不是终结姿态时长，传入和刷新继续核准 |

当前metadata明确AttributeType9=`CriticalRate`、10=`CriticalDamageIncrease`，与已审核枚举一致。因此这里的两个动态字段对应暴击率和暴击伤害桶。最终幅度仍依赖真实enhance计数、属性重载和事件次序，不能只读常量就供应当前最终值。

对应当前metadata字段188586/188587，默认数据偏移200765/200766，压缩常量首字节0x12/0x14分别解码9/10；与10份记录的ID及解码摘要、等级参数一起独立重验。核心解码记录摘要：

| 记录 | 解码JSON SHA256 |
| --- | --- |
| chr_0017_yvonne_ultimate_skill | 90d1f3f87b04a4db8bd436176e2e9527bcedebecab448e903c3d4a42b035c2f5 |
| chr_0017_yvonne_ult_attack_end | f5f70a39f50d1578a3f642a051b9f86ea2e9987fe106b7ee4d33698b690d7264 |
| buff_chr_0017_yvonne_ultimate_skill_layer | 6d2ef3f04804faba1805e5d614fda9b9bd11df9d2582949139034a05e6cf4bfd |

这些是当前捕获的解码记录摘要，不能冒充原Unity对象或旧快照原始字节摘要。原始材料仍只留主仓ignored目录，不放入工作树。

## 下一步执行项

1. 终结进入7秒姿态后，核准强化普攻/结束攻击替换和BB传递。
2. 核准实际攻击的层数producer、8号增强/刷新和属性重算，逐次更新报价，不默认满10层。
3. 执行结束攻击的基础、冻结追加及冻结消费，保留各伤害处理器的实际分支。
4. 处理退出/提前结束/技能附着清理，不把7秒内队友技能一起锁住。

科技连击“指定下一重击+50%”仍是另一条机制，不扩大成冷冻射手全部攻击+50%。本批只确认数据和后续路径，完整生产链、射击次数/命中与整段终结报价尚未完成。
