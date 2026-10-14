# 普通buff层数快照接入（2026-10-14）

本批在研究执行器接入`SaveBuffStackNum+Data`，区别于已有Advanced节点。它不是新识别器，也不产生buff；只读取模型当前可确认的状态，把结果传入下一原生公式。未确认的生产者继续保留诊断。

## 原生合同

同版metadata方法59506，`Beyond.Gameplay.Core.SaveBuffStackNum.ExecuteInternal`，RVA`601d0c0`。到下一已知方法指针的564字节窗口SHA256为`81ebbfc8f72af6ed52070d5c287fc38a4da12d47846f2728b37b31c3c73a806d`；边界包含尾部对齐，不当严格函数长度。DLL和metadata仍等于现有快照的`c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89`与`0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e`。

`601d157`调用GetFirstTarget；随后ResolvePartBuffSource，并在有效AbilitySystem上GetBuffCountById。无已选对象时原始count为0。实际有未知对象/部位解析边界时不能用0代替。计数转float32，比较原BB值与实际值的单精度差，仅显著变化才AssignDynamic；该原生通知和server op并未由本批执行。

本研究世界提供明确的角色/单敌人实体和有序目标组，不提供部位别名；按当前查询结果生成BB快照，通知生产保持后续。新增`NativeBuffQuery.first_target_only`只用于普通节点，不改其他查询的既有多对象行为。已绑定空组读0，未绑定组保持缺输入，并清除本次输出的旧/默认BB值；目标组变化或到期后重新读，不缓存旧层数。

## 逐来源核查

当前已核源中有4处启用声明，分布3份记录，不等于3个完整可执行角色：

| 来源 | 输出 | 读取对象/ID | 本批边界 |
| --- | --- | --- | --- |
| 噗切娜normal_skill_counter | is_block、block_time | 持有者block_counter_mark与block_counter | 两个变量均来自真实buff计数；block_time名字不被解释成经过的秒数，完整替换/阻挡阶段仍未知 |
| 莱万汀buff_chr_0016_laevat_energy | count | 持有者自身energy buff | 不默认4层；实际生产/消费和增强刷新仍独立核查 |
| buff_common_affixes_combo_trigger | count→imbue_scale | 持有者自身combo_trigger buff | 按实际计数读取SkillSetting“连击增伤”列，来源角色与持有者分开；全局派发/优先级/实际层数生产仍待完成 |

实际common回调的快照片段已编译并进入`CombatWorldState`。确认某持有者有3层时，下一`ReadSkillSettingData`使用捕获行的第三列；文件“累进值”说明不使该节点自动相加或填最大值。方法59349的实际索引是column-1后读取float；全局多实例/priority组如何形成总收益继续待核，不能用单次读出的列值代表最终全队增幅。`SkillAffixAction`与同cast清理未接通，所以尚未把秋栗新连击增伤加到PR #489。

## 验证与接续

专项覆盖真实common快照片段→参数表、只选有序首对象、旧查询仍合计、空/未知区别与旧值清理、到期重读、预测隔离，以及真实OnBeforeCastSkill callback的持有者/来源分离。验证使用已确认的模型层数输入，不把测试输入当实战producer。固定面板、128基础报价和13条释放规则不改，完整原生程序覆盖仍2/111。

独立原始研究资料按用户约定只保留主仓库忽略目录`tmp/condition-research-20261013`；本文件仅为已执行产品合同及边界。下一批继续全局Stack分派、CreateGlobalBuffAction动作结束、期间计时和SkillAffix释放。普通节点输入可调用与本批测试通过不能认定整个机制闭环。

5项新增、50项相关与全仓1466项测试通过（12.595/133.436秒），最后测试断言/导入整理后13项再次通过。Python AST、Ruff I/F与diff-check通过；完整加成数据流审计与已保存JSON完全一致，128来源/报价可重放。研究分支已有未登记CI职责的历史缺口仍使全量分组校验失败，本批未新增测试文件或改分组；正式全仓测试按run_tests.ps1通过，不把局部执行器测试冒充CI分组通过。
