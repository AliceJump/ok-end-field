# 长息施加事件与队友增伤审计

2026-10-19，数据证据续批。未新增研究执行绑定，也未把这批证据自动导入PR489。完整原生覆盖仍为2/111；32研究固定面板和128报价不变。

## 已核准的数据链

当前ab00/4613客户端捕获的`passive_equipsuit_usp_02`经顶层`SkillData.buffs`挂载`buff_equipsuit_usp_02`，传递`hp_up/dmg_up/duration`。实际EquipSuitTable选择三件、Rank1；SkillPatchTable参数为生命1000、`dmg_up=0.1599999964237213`、持续15秒。固定生命归面板，不把它当动态伤害收益。

父buff监听来源侧事件102（OnOutputBuff），`CheckBuffIdInContext.checkType=1`，`queryType=0`为HasAny，四类标签如下。名字依据固定cc9b标签别名和CRC映射；当前原始查询字节逐项相等，未声称已经重新执行当前客户端整个TagConfig初始化。

| 查询字节 | 标签末级 | 含义 |
| --- | --- | --- |
| 2811f978 | Weak | 虚弱 |
| 3213cd97 | Enhance | 增幅 |
| 5041588b | Shelter | 庇护 |
| 24f87ccb | Vulnerable | 脆弱 |

通过检查后实际执行`targetSource=3`的`CharacterTeamFinder + ExcludeOwnerValidator`，以字面count1创建`buff_equipsuit_usp_02_AddAttack`，`asChildBuff=false`、`autoFinishByAction=false`。因此受益者为其他队友，不能再把持有者也加一份，更不能把未执行的selector字段当真实接收域。

名字中的AddAttack不代表攻击力：子buff没有攻击属性项，只有无额外元素/技能筛选的Attacker侧`DamageScaleProcessor/NormalCalcZone`，读取`dmg_up`。应作为全类型伤害提高16%，与已有同侧增伤相加，不是白攻击+16%或最终独立乘1.16。子buff为Timed/BB duration，Stack2、max1、identifierType0、空stackingKey；两个穿长息的施加者不能未经共享实例身份核准便给同一队友叠32%。

## 梨诺的真实入口与仍缺的一段

普通演唱已接的白攻击提升不属于上述四类标签，不能凭演唱释放自动触发长息。高歌的`buff_chr_0035_liino_spellenhance`自身`applyTags=[]`，必须继续追DuringBuffEnable事件5中的两个EnhancedAction，而不能在无标签承载buff创建时就宣称102门槛满足。

两个动作subType为6/3，分别对应此前版本已核准的电磁/自然增强路径；rate读`spellenhance_rate`，source1、target4。`overrideChildBuffId=false`，所以残留的赛希childBuffId字符串不构成实际模板绑定证据。当前捕获中的通用电磁/自然模板各有Enhance子标签，祖先匹配可满足长息查询；本批没有重新证明当前DLL的完整subType分派。

接入前需要把以下边界明确落实为审核合同或真实执行，不能仅凭16%消费者就宣布producer闭合：

1. 当前构建subType到通用模板的分派及实际来源身份。
2. 模板实际输出通知的前置/刷新/接收顺序；多元素、多队友输出不按事件数叠四份或更多份。
3. 多个长息施加者对同一接收者的max1与刷新归属。
4. 新的15秒独立子buff与高歌提前结束的关系，不能随父姿态误删，也不能无证据延长。

无需新增命中、HP或场内识别；证据充分后可以消费已有明确的模型施加事件。此前三剑、孤舟终结、梨诺高歌已有效的条件报价中，再有长息全程覆盖时约259771.57，属于未启用长息的条件对照，不是目前实际选招值。

## 复现与来源

独立脚本、原始记录与JSON审计均仅在主仓Git忽略目录`tmp/condition-research-20261013`，产品树不保存这些原始材料。`audit_longxi_release_20261019.py`核验实际DLL/metadata完整摘要、8份记录的ID/原始摘要/已回编码结果、等级表、事件与目标、Stack/伤害乘区，以及梨诺两动作和模板标签。审计结果`longxi-release-audit-20261019.json`的SHA256为`b3945f87ceaa288d9b82e2ac282e45bd94985b7bf1e839381d6f364a72fe5ce3`。

| 当前原始记录 | SHA256 |
| --- | --- |
| passive_equipsuit_usp_02 | aec04a7a8d97bf21d7ed1813a397110a6c8df00a371782881df9e85b94695e05 |
| buff_equipsuit_usp_02 | 5389cbabbf11fae53600c01a309f81fa4d5b34c8844e3cda6be6f6fccf2f608d |
| buff_equipsuit_usp_02_AddAttack | 95a60ae96b48c9d7e150ce59cef02fcd7fa155abefaabf99269187e694eb244f |

本批8份来源与断言、独立脚本AST和diff-check通过。仅文档保存，不重复宣称全仓测试或新增运行收益。
