# 负山动态四维的原生数据来源（2026-10-08）

本批补数据来源，不启用武器触发收益，不接master或识别。上一份已收录原生库只有1738条记录，扫描其中只发现药剂的四维修改，不能据此断言本地没有武器生产者。继续扫描本地SkillData/BuffData，5569条、28513775字节全部解码并逐字节回编码一致，失败0。研究脚本、原始客户端文件和全量未审计导出继续保持本地忽略。

## 核准的武器与等级来源

重新从当前安装的VFS提取EquipSuitTable、EquipTable、WeaponBasicTable、SkillPatchTable、AttributeMetaTable等表，校验解密后内容的原始MD5。表通过其内嵌SparkBuffer类型定义解析；本批不声称这些表做过字节回编码。DLL和metadata摘要再次与现有skill_timings索引一致。

WeaponBasicTable的wpn_lance_0012明确绑定weaponPotentialSkill=sk_wpn_lance_0012；原生ItemTable及文本表确认名称为负山，且是黎风当前固定配装。SkillPatchTable的level=9提供dmg_up=0.5600000023841858、all_attr_up/all_attr_up2=0.2240000069141388、duration=15。技能原文件的0.1/0.04、30秒是旧默认值，不能代替等级表。cd未被该等级patch覆盖，原技能默认为0.1；没有max_stack输入，不填1或无限。

四份完整原生记录及上述表的选中条目保存在assets/data/equipment_mechanics/20261008/burden.json，索引校验payload摘要，明确evidence_only。记录另验证当前VFS条目的MD5及解码后回编码相同；不是从英文名称猜武器绑定。未将其混入runtime原生record库，未改变固定面板、基准报价或producer完成数量。

## 两条独立生产链

sk_wpn_lance_0012监听原生AbilitySystem.Event=206，即**OnBeforeOutputBuff**，不是OnOutputBuff=102。其内部有两个独立action块：

| 原始查询 | 对应TagConfig路径 | 创建属性buff | 冷却marker |
| --- | --- | --- | --- |
| 6674953f | Skill/Character/Common/Affixes/Vulnerable/VulnerablePhysic | buff_wpn_lance_0012_attribute2 | wpn_lance_0012_2 |
| 21281e40 | Skill/Character/Common/NoGuard | buff_wpn_lance_0012_attribute | wpn_lance_0012 |

两块均以CheckBuffIdInContext.checkType=1查询标签，再检查CheckOriginSkillType.skillTypeList=[2]（Beyond.Gameplay.SkillType.NormalSkill，战技；不可误用Beyond.GEnums.SkillType.PassiveSkill=2）。随后检查各自marker，创建buff并设置marker。属性与duration由对应BB显式传入。CreateBuffAction.asChildBuff=true、buffSource=0、受益targetSource=1（Source），并继承来源CastInfo；不能无条件替换成当前主控、命中目标或任意队员。

OnBeforeOutputBuff意味着不能用“已成功施加破防/脆弱”替换实际发布时点；免疫或其他拒绝情况下事件是否已发出仍需追原生发布者。也不能把施放战技、敌人已有状态或累计层数当作这两个事件。

两个属性buff均非转换，分别对39/40/41/42（力量/敏捷/智识/意志）应用formulaItem=6（BaseMultiplier）、modifyAttributeType=0（Specific），读取独立BB。lifeType=0、duration读BB；stackingType=4（Refresh），useMaxStackCntKey=true但对应max_stack来源未找到，isNeedStackEffect=true且列表为空。Enable回调还创建buff_common_vfx_char_atk_up，autoFinishByAction=true。因此不能套用只核准无回调的阿列什Refresh合同，更不能默默丢弃回调链。

## 仍须补齐

- 确认原生BaseMultiplier的组件基数、fixed装备百分比与converted/non-converted运算顺序；不能简单把最终面板乘1.224，两个组也不能先假定乘法相乘。
- 实际OnBeforeOutputBuff的发布条件、来源/原始技能上下文与marker时钟、刷新/清理及父子归属。
- Refresh启用/刷新/通知、max_stack查值及视觉子实例的动作结束清理。接口调用成功不表示这些链已完成。
- 黎风智识/意志变化后的转换攻击刷新，赛希FinalNonConverted智识的实际生产者继续独立待办；已有最终差值接口不代替组件运算。

逐角色逐技能台账给黎风四个技能登记两条候选，保留原始guard/对象/时长/marker；不是判断当前技能名字能直接触发。候选状态始终not_bound，13条释放绑定、2条原生攻击实例绑定和完整程序2/111均不增加。

## 原始来源摘要

| 来源 | SHA256 | 字节数 |
| --- | --- | --- |
| sk_wpn_lance_0012 | 8b09eb93cfaefeef52dcffabd17fb3434c6396a1c5d27a0482480e059a67f766 | 2062 |
| buff_wpn_lance_0012_attribute | da7d1d06977ab0c43b66799b715ac969bb569b83d909c618fae16a54cd961757 | 647 |
| buff_wpn_lance_0012_attribute2 | e9f3690f94b066456cae38678184e9f2a2031beae7cf694c84684a6ac0013ecf | 653 |
| SkillPatchTable | 179d6adb5e836257d0e59b6387dfa1fc2ca24ca8edbda0489cf6238abb9f9fca | 1132328 |
| WeaponBasicTable | 9ea7a1f8b28bb86b59326378694c890bcf065573f35f9724819c72c3f1f9a448 | 17940 |
| AttributeMetaTable | 7a8098ff29e5e5e4c005b7ee4a8cd43be77b98e1bcfaa96cabdafa1c961abcdb | 9920 |
