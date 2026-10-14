# 攻击方NormalCalcZone消费者（2026-10-15）

本批把已确认原生buff实例上的攻击方普通增伤接入研究世界的真实伤害计算。只补消费者，不自动产生buff、假设技能命中或启用伊冯科技连击；其Refresh/冻结/marker/ComboCache链继续保留缺口。PR #489的释放层与固定报价未改。

## 乘区与持有者

旧快照`DamageScaleProcessorConfig.NormalCalcZone`是加法区，`mergeAttackerAndDefender=false`、`isDamageTypeZone=false`。两组native枚举均为Attacker=0、Defender=1；原生启用侧与processor侧必须匹配，本批拒绝跨侧、未知值及bool冒充枚举。

旧构建已核DamageScaleProcessor.ProcessDamagePackDataInternal60962/RVA40132b0：从buff自身BB GetFloat，转double后ModifyDamageScaleZone；已有证据窗口SHA`ea630f94d460b32e78eb1385b35509f0cb5e60600f5deb93bb1f9131de74d13d`。旧方法60931/RVA3549290的6000字节窗口SHA`1809eea9b7f0988568809a45e1ce9a78b3e7988ceabf6306500b140c7f4a4f87`，Normal区读取两侧数组、相乘后非负截断。证据原已登记于mechanism-continuation-checkpoint.md，不把当前客户端的新地址套入这些旧方法编号。

本批统一成`NativeNormalDamageScale(element, addition, side)`。攻击方条目只读`instance.owner == hit.actor`，受击方只读`instance.owner == hit.enemy`，两侧分别累计：

```text
Normal = max(0,
    (1 + 已有面板/技能增伤 + canonical动态增伤 + native攻击方增伤)
  * (1 + canonical受击增伤 + native受击方增伤))
```

例如面板增伤30%、已确认实例50%、受击侧20%时，此区为`(1+.3+.5)*(1+.2)`，不是`1.3*1.5*1.2`。攻击百分比、增幅、脆弱和暴击仍在各自既有乘区，不把攻击方damage_bonus误加到攻击面板或全队。实例仍可按已核单一元素条件过滤；复杂条件、其他processor/zone、主控/guard限制保持未知。

只读enabled实例的私有BB，并对GetFloat作float32转换；缺值不借原始BB默认值、上一事件或别人的输入。对不匹配元素/持有者的命中，不因无关实例缺输入而阻塞。移除与预测隔离沿用实际UID，未自动延长或消费实例。

## 伊冯回归与生产链边界

回归直接编译旧快照`buff_chr_0017_yvonne_talent_1_valid_up`（325字节，SHA`2d4bd3d7424675b36271043855be4aeafdecadc668cdfa03a01f8f525bc0208c`）的真实modifier；满级参数取`chr_0017_yvonne_talent_1_2.dmg_up`。它为Attacker/NormalCalcZone、无元素/命中次数条件，公式消费者不应另造“仅普攻”过滤；只对一次重击生效须由已核生产、附着及清理链约束。

测试显式提供“已确认实例”场景，保留其真实Refresh4定义，不改成Stack0来伪造创建成功。这样验证的是已有实例的报价，不能当作从战技冻结到重击的整条链完成。真实valid回调仍在CreateBuffAttachingSkill上保留`needs reviewed instance policy`，不产生valid_up，也不向当前队伍/排轴默认供应50%。生产者闭合前，不复制本测试场景到正式初始化。

## 来源版本变化

本轮继续提取Refresh桥接时，原始脚本在读取前发现当前安装DLL/metadata与旧快照不同并拒绝运行：

| 输入 | 旧研究快照SHA256 | 当前安装/独立留档SHA256 |
| --- | --- | --- |
| GameAssembly.dll | `c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89` | `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c` |
| global-metadata.dat | `0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e` | `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca` |

安装目录的DLL.lch.bak也不匹配旧DLL；未用它替代。文件时间不是等价证据。旧已留存机器码仍按旧哈希核查，本批数学消费者只使用仓库校验过的旧canonical/原生数据，不导入新构建的数值。

独立目录`tmp/condition-research-20261013/native-build-ab00f1c4`仅在主仓库Git忽略区，保留两份新输入及provenance；没有修改游戏文件。新metadata有91478类型/574771方法，从头定位code registration并核对全部172个image，未借旧缓存。新AddBuffByAbilityAction索引59317、OnBuffCreated60987等只登记为新版本研究位置，不能冒充旧56496/58076已完成跨版本校准。完整源码/字段/返回链、VFS资源及当前客户端实战适用性仍需继续验证。

## 验证

新增7项，伤害处理器文件15项及47项相关测试通过（相关16.303秒），全仓1496项通过（146.746秒）。AST3、Ruff I/F、diff-check通过；32角色/128技能damage-data-flow与保存JSON完全一致，14模型释放/2原生ATK及完整原生程序2/111不提高。未新增测试文件，既有研究分支CI职责缺口仍保留。无现场验证，任务未完成。
