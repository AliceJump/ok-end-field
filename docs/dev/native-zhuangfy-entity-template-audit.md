# 庄方宜造剑与区域模板核查（2026-10-17）

本批核准原生模板和更新后的来源，不新增剑数观测或运行时收益。用户已授权接入电剑预测，导电已确认时可取最低等级；但不能把首次免费战技的白圈 OR 分支直接当导电，也不能把造剑请求立即当成区域已经读到的剑。

## 更新资源与版本边界

安装中的 DLL/metadata 仍分别为 `ab00f1c41e13edf9816f142b166e3c2536d5bc7707992c39e5ed52d1cc888e2c` / `461321d9dca6a832bce3bd2390402efc7c83c145554e97a8058bb68fbec1c8ca`，但 VFS 索引和字符串池已经变化，部分旧 Persistent chunk 已不存在。新资源单独捕获，不覆盖 `data-refresh-v5`，不删除版本守卫。

新捕获 `data-refresh-20261017` 的 8,728 项选择全部提取、缺失零；28 个字符串块严格解码，6,323 份 SkillData/BuffData 经 EOF、ID 和逐字节回编码核验通过。庄方宜 70 份记录的新旧解码内容完全相等；池索引变化后的二进制不能直接与旧记录混读。此比较只覆盖这 70 份语义记录，不声称全游戏配置未变。

三份模板通过 StringPathHash → manifest → bundle 映射、内容 MD5、AnimeStudio JSON/Raw 双输出和对象长度/SHA 核准。读取内容地址相同的 StreamingAssets chunk 时仍验证所选索引的文件 MD5，不以 chunk 所在目录改变记录来源。manifest 的 memory size 不当作原始 Unity 对象长度。

| 模板 | Unity 对象字节数 | 原始对象 SHA256 |
| --- | ---: | --- |
| `abilityentity_chr_0030_zhuangfy_normal_skill_sword` | 3164 | `fc2b50c88c8a7b8cda86008b919abb1fdf2b370bd6eb4d20d79623c9192b1866` |
| `abilityentity_chr_0030_zhuangfy_normal_skill_ult` | 3224 | `3f817aee44423d5bd6c444a8929bdc54d3c061039376b5cfa51f7ca050d9752c` |
| `projectile_chr_0030_zhuangfy_normal_skill_gene_sword` | 4324 | `369d936fadd589fbeea17e2b41a5a2069c97913310ac5ba9e8620896d076c926` |

实际使用的 Template/Root/AbilitySystem/ProjectileComponent 字段均经 serialized TypeTree 精确解码。注册表 fullyDecoded 不等于所有组件都经 TypeTree 解码：空 Controller 和 ProjectileRoot 的辅助布局不用于推断时序。本批没有声称这三份 Unity 对象已经独立回编码；其双输出摘要核验与 6,323 份 MemoryPack 回编码是两项不同验证。

## 可用的生命周期证据

剑 Template 和 RootComponent 都是 Limited，`durationBB` 启用并读取 `EntityBB_swordDuration`。模板字面 `duration=45` 不能覆盖输入；到达子技能显式将所选技能的 `sword_duration` 赋给该 key，目前审核 rank12 为 36 秒。`EntityBB_swordLimit` 同样来自 `remain_sword_limit`，不是当前场上数量。创建动作 `dieOnEnd=false`，不能按根战技动作结束删剑；`dieWhenSourceDie=true` 仍要保留来源失效清理。

齐射区域 RootComponent 的 `durationBB` 未启用动态 key，其直接值为 3 秒；同级旧字面 `duration=1` 不能凭字段外观当作实际寿命。Root 对该输入的具体初始化执行还需核准，因此不据此直接改产品时间保护。

本人范围内剑数仍由独立被动统计，再在区域创建前采样复制。模板寿命补齐不代表到达、来源 children 登记、被动更新、区域快照已经按真实相位运行，详见 [齐射与计数证据](native-zhuangfy-salvo-audit.md)。

## 造剑到达不能固定填 0.2 秒

生成投射物唯一 `Default` 模式的 `moveType=2`；当前真实枚举是 `Bezier2D`，不是 Parabola。模式中存在 `travelDuration=0.2`，但不能因此把它当成实际到达耗时。Bezier2D.OnStart（82733/RVA64c5ad0）在调用基础 OnStart 后，从 MoveModeData+0x40 读取 `speed`，把输入点差交给向量 helper，再以 `DIVSS` 除以该值，写入自身+0x2c。MoveModeData 的 `travelDuration` 实际位于 +0x80，以上已核分支不读它。

基础 OnStart（82715/RVA64c5c40）另读 `useSpeedScaleWithDistance`（+0x58）和对应曲线（+0x60），把曲线结果写入基础管线+0x24。模板 `speed=50`、距离曲线和常量速度曲线均已提取；这些还不能代替后续 Tick、推荐目标骨点、落点与到达事件的核准。旁边的 `parabolaDef=1` 属于未选中的抛物线参数，不能套用来解释 Bezier2D。

首次免费分支的 LaunchProjectile 索引42以 `smart_target` 为发射位置，使用 emitMountPoint52，并向 `swordPos` 发射；不是已经证明从角色脚下直线飞向敌人。不能用角色距离直接代入，也不能忽略骨点高度、导航投点和实际管线更新而宣称三剑一定赶上15帧区域快照。finishDuration 直接值2秒是投射物结束配置，不是保证成功到达的截止时间。

## 接入顺序与验收边界

先把可核准的生成、到达、登记、计数和创建快照连成模型事件，再将其作为预测输入进入真实报价；模型预测不得写成 UI 观测或敌方事实。未知初始剑数保持未知，导电最低等级仅用于确立的导电分支。未核准的部分不以固定0.2秒、上限9剑、首次立即3剑或默认命中补齐。

原始资源、捕获索引、脚本、CLI 清单和机器窗口只在主仓 Git 忽略目录 `tmp/condition-research-20261013`：`zhuang-prediction-20261017`、`zhuang-projectile-motion-20261017` 和 `native-build-ab00f1c4/data-refresh-20261017`。产品 PR489 的面板、15条加成、132报价和姿态未改；研究32面板/128报价、14模型释放/2原生ATK以及完整原生2/111均不提高。
