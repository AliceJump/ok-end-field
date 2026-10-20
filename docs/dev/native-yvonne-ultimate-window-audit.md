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

## 续批：真实层数请求与8号容器执行

本次从当前捕获的7份技能记录找到27处明确的层数请求，均为时间轴直接向本人添加1层，不在投射物命中回调内。它们分布如下；阶段切换仍需经过实际输入/ComboCache/许可，不能全相加成一次终结的保证次数。

| 技能脚本 | 请求所在的原始起始帧 |
| --- | --- |
| ult_attack1_1 | 9、15、21 |
| ult_attack2_1 | 11、14、17 |
| ult_attack2_2 | 1、4、7 |
| ult_attack2_3 | 1 |
| ult_attack3_1 | 13、15、17、19、21、23、25、27 |
| ult_attack3_2 | 1、3、5、7、9、11、13、15 |
| ult_attack_end | 0 |

这说明层数输入可以来自已核准的动作事件，不需要为了这部分新增命中识别。它不证明全部请求都执行、弹丸命中、结束攻击一定造成伤害，或每个阶段可在7秒内无条件完成。

当前StackBuff分派表的8号入口RVA0x589e882：没有旧实例才创建一份；已有实例先执行尝试前回调8，未到max时group计数+1并调用Buff._Enhance，再分配临时参数刷新旧寿命，最后执行尝试后回调7。满层仍刷新，但跳过_Enhance及增强成功回调6，返回空创建handle、out为旧实例，不能把旧对象重挂给后来的Skill/父根。Buff.Reset把m_enhanceCnt初置1，_Enhance每次加1；它与“创建10份独立buff”不同。

| 已校验方法/路径 | RVA | 选定窗口SHA256 |
| --- | --- | --- |
| StackBuff（含8号已有实例分支） | 0x589e3d0 | 2670af1f8c687441f3cc1c649f69a548e1a5dde0d0779d56335844a835fa6e7a |
| Buff._Enhance | 0x5f5ed10 | 5db089660faf559b0e08bc633471f453fc5028b06386f44b1db318e41ec83f60 |
| Buff._OnBeforeTryEnhanced | 0x6e60780 | f2cf807a58c0d186751da6173546644c6f5df023aa07de8fb58f57b2478f928f |
| Buff._OnAfterTryEnhanced | 0x69e0b90 | 71151bb3d3e9604ac917c4497715e5158ce4c777759c5df291c1175046a12563 |

以上为选定机器窗口，不是严格函数长度或IFix实际路径证明。9窗口、9分支锚点及27节点路径/7记录摘要已独立核验。当前与产品旧快照逐字段比较，仅新增iconConfig.showProgressInEnemyHeadBar=false，节点union tag另行区分，未把整个原始序列化称为相等。

研究执行器现在仅为这份已核准的、无周期/共享/优先级、固定max10的容器执行：一份UID保存1～10计数；增强成功后以当前计数运行真实回调6，满10时写入私有crit_rate_up_dynamic；重复申请保留旧source、BB及UID并刷新寿命，到期/整份移除清理，预测隔离。新Skill附着、部分层消费、多次count及其他8号容器继续拒绝，不再退化成普通累计池。Refresh4与8的策略保持分开。

回归已实际编译原生CreateBuff节点并从1执行到2，也验证9层不写dynamic、10层写0.06、之后不重复成功回调。该值是私有参数，不是已经实现的最终60%暴伤：formulaItem5、RefreshBuffAttrModifierValue、属性重载和通知/重入仍有明确诊断，因此尚未增加伊冯伤害报价。recycle_time自有输入为4秒；duration字面备用2不覆盖该键，也不是7秒终结期间。

新增6项测试；105项生命周期/上下文/替换回归及1769全仓（198.486秒）通过，164文件/16测试职责组登记通过。5份修改Python的AST、Ruff I/F、diff-check通过。32面板/128报价的数据流审计与保存JSON逐字节一致，14模型释放/2原生攻击和完整2/111不提高；未改PR489、定时或主仓用户文件，未游戏现场验证。
