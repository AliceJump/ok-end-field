# 原生属性默认值、边界与计算组件（2026-10-08）

本批补到 AttributeMetaTable 的原始数据和默认值初始化链。新增表读取器供逐角色审计使用，不向战斗输入填 raw default，不执行负山百分比，也不把赛希的 FinalNonConverted 智识视为已有 producer。32 固定面板和 128 基础报价未改；14 条模型释放、2 条原生实例 ATK 绑定及完整程序 2/111 不增加。

## 已获得的原始表

当前本地 VFS 的 AttributeMetaTable 为 101 行、9920 字节，SHA256 `7a8098ff29e5e5e4c005b7ee4a8cd43be77b98e1bcfaa96cabdafa1c961abcdb`。提取时核准 VFS MD5；仓库保存原始表的 gzip、选中十行和逐字段 offset/format/raw_hex/value，不保存客户端二进制、解密工具或密钥。表来源及同版 DLL/metadata 摘要见 `assets/data/attribute_metadata/20261008/index.json`。

| 属性 | raw default | 生效的下限/上限 | 当前用途与限制 |
| --- | --- | --- | --- |
| 39 Str、40 Agi、41 Wisd、42 Will | 0 | 0 / 100000 | 验证所有固定四维面板在原生范围内；不能用默认值替换角色成长和装备 |
| 57 PulseAbnormalDamageIncrease、58 CrystAbnormalDamageIncrease | 0 | 两个边界开关均 false | 只解决 raw default 来源；没有证明当前 armed/final 或修饰项为零 |
| 76～79 AtkIncreaseFactorFromStr/Agi/Wisd/Will | 0 | 两个边界开关均 false | 主副属性系数还来自 otherBaseAdd；默认零不表示实际转换系数为零 |

每行布局相对 attributeType：ID int32=0、default double=8、hasMax bool=16、hasMin bool=17、max double=24、min double=32。读取器检查原始表/选中数据 SHA、当前构建、属性集合、字段类型和相对 offset；不能把同为零的 min 字段冒充 default，也不能修改 JSON 并重算 manifest 来绕过原始字节。关闭的边界表示为 null，不能使用其闲置 0 值限制属性。

这些数据进入每个角色的 `dynamic_attribute_flow.native_raw_metadata`，四个技能继续共享该角色的属性基数和待确认 producer。审计不修改传入面板、报价或属性值，不因此消除技能语义待办。

## 默认值确实经过代码初始化

重新核准当前 GameAssembly.dll / global-metadata.dat 摘要与 skill_timings 索引一致，以下均为同版机器码。长度是摘要校验窗口，可能包含相邻方法；语义仅来自指明的函数/分支。

| 方法 | RVA / 窗口字节 | SHA256 |
| --- | --- | --- |
| AttributesData.get_fieldMetas（3955） | 3201600 / 4000 | c644b36defd10e80dc3bb52d793feef468a77cc15b40a9b4f51f58636caf29d4 |
| AttributesData.CreateDefault（3965） | 3201200 / 1600 | 69a2f2b7acaf03d9eac595ed9a5ff108324b3550a5ebd15d2fffc0b231a6ac56 |
| AttributesData.CreateFrom（3962） | 36601b0 / 3500 | 0ff8d57d88b704a0c6b189468fd012e68699d57ea86430ca4c55562957ad6e36 |
| Attributes.Reset（60502） | 3544850 / 6500 | 13b2c51cd9b0c13aa0d56c41b4c1713d8ca38d3e7f66663dacf232945d5dbd20 |

get_fieldMetas 建立 101 个 FieldMeta；320175f 读 hasMin 的字节 17，3201797 读 hasMax 的字节 16，32017ca 读 default 的 double 偏移 8，3201848/320186c 在开关启用时分别读 min=32/max=24。存到 FieldMeta 的 default+0x18/min+0x20/max+0x28。关闭开关时的代码常量本批没有作为有效数字发布。

CreateDefault 在 32012c1 读 default、3201325/320132a 读 bounds，320134d 调 clamp，320136b 写入数组。CreateFrom 先在 36601d4 调 CreateDefault，再在 36601fa 调 SetFrom 应用传入数据，因此不能跳过角色条目覆盖。Reset 在 354496c 读 supplied AttributesData、3544972/354499b 读 min/max，再在 35449e7～3544a11 比较截断并写 rawData。后续 modifier/converted 层和实际初始化顺序仍须追踪。

## 百分比为何还不能乘最终面板

同版 _CalculateArmedAttributeValue（60545 / RVA3546200 / 4200字节，SHA256 `cc97d315f1d96512169d1228ea240d73ff153b1b9b8c863e35874bdebde84451`）在 354711b 取得 base，354714c 限制 BaseMultiplier 因子为非负；35471bb/35471c0/35471c5/35471ca 依次乘因子、加 BaseFinalAddition、乘 BaseFinalMultiplier、乘 otherBaseFinalScalar，35471d9 再走 clamp。

_CalculateFinalAttributeValue（60544 / RVA35475e0 / 4200字节，SHA256 `a9d2932d9484ed755f526a22ebbcc982da98084258653475d719888c5d5384d3`）在 354841c/354842d/354843c/354844d 分别汇总 Addition、Multiplier、FinalAddition、FinalMultiplier；35484ed 获取 armed，354851d 限制 Multiplier 因子非负，3548585～3548599 依次加、乘、加、乘，再乘 otherFinalScalar，35485a8 走 clamp。非转换查询的过滤、各组件实际 producer 和缓存失效仍须核准，不据旧研究合同直接推广为可执行通用公式。

负山修改 BaseMultiplier，固定装备/潜能可能还有同层百分比、不同层加法/倍率和转换；已经完成的最终面板是这些操作的结果。直接乘 1.224 会改变后续加法项或与已有百分比相乘，不能代表新增同层 BaseMultiplier。下一步须取得角色和固定配装各组件的实际来源、转换排除及刷新链，然后才能将已确认实例送进正确的组件层。

## 剩余边界

- 负山的原始输出 buff 事件/源战技/两组 marker、Refresh 回调与未知 max_stack 仍未执行，不按施放补触发。
- 黎风智识/意志转换攻击刷新、赛希 FinalNonConverted 智识 producer 仍未知。
- 57/58 已有 raw default 证据，角色覆盖、其他 modifier 和战斗最终值未闭环；现有缺输入诊断保持。
- 陈千语/佩丽卡 EnhanceAndRefresh 计数重建、通知顺序及真实模型事件仍需继续。
- 未改 master、识别端、技力/HUD时机；本批不是完整机制或实战验收。
