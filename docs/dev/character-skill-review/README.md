# 官方 WIKI 干员数据抓取与技能分析

## 森空岛官方 WIKI 完整快照

已新增 `scripts/data-capture/capture_skland_operator_details.py`，通过真实浏览器监听官方已签名的 `item/info` 响应，抓取当前 WIKI 中全部干员的完整详情。

2026-08-30 最终快照：`tools/wiki_catalog/operator_details/20260830_221449/`（由 gitignore 排除，仅保留在本地）。

- 干员目录：33 项，含噗切娜、提弗洛斯、管理员男女两个词条；
- 完整详情：33/33 成功，原始 JSON 约 13.4 MiB；
- 渲染文本：33 份，共 487,905 字符；
- 每份详情完整保留 `documentMap`、`chapterGroup`、`extraInfo`、`widgetCommonMap`；
- 可利用信息包括角色资料、职业/武器/能力、各等级属性、精英化材料、全部技能描述、WIKI 当前展示的 RANK 1–9 技能倍率与数值、天赋、潜能、能力扩延、推荐武器、档案、语音与资源链接；
- 同时保存 `catalog.json`、`char_pool.json`、`weapon_pool.json`、详情页纯文本，以及每名干员对应的完整 `item/list` 关联物品响应（33/33）；
- `manifest.json` 记录每名干员的 item ID、详情 URL、更新时间、文件路径、数据体积和文档组件数量；`latest.json` 指向最近一次抓取。

完整原始 JSON 是后续自动解析倍率和条件的权威 WIKI 输入；纯文本仅用于人工检索，不应替代结构化原始数据。

## 自动技能分析脚本

`scripts/skill-data/analyze_operator_skills.py` 可直接消费上述官方快照，生成结构化技能审阅数据：

- 提取每名干员的普通攻击、战技、连携技、终结技及完整描述；
- 还原 RANK 1–9、专精 1–3 的倍率、技力、冷却、失衡、持续时间和升级材料二维表；
- 自动识别“当/若/如果”条件，并区分技能发动条件与结算条件；
- 对多个“若……则……”分别建模，不把独立条件错误合并；
- 识别破防、附着、熔火、铁誓、涡流、青霆剑、雷枪等特殊层数/数量及 `>=`、`<=`、精确值；
- 识别“消耗所有”“最后一点”“再次施加该效果”等特殊语义；
- 用 `EFFECT_TERMS` 提出条件和结果的 effect ID 候选；
- 对照本地 `character_skills`，标记描述差异、缺失技能、强化态数量不足和待人工确认项；
- 只生成报告，不自动写回正式角色数据，避免启发式误改。

当前快照分析结果：33 名干员、127 个技能、64 个条件、9 个多条件技能、16 个特殊层数技能、21 个待复核技能。输出位于：

- `tools/wiki_catalog/operator_details/20260830_221449/analysis/operator_skill_analysis.json`
- `tools/wiki_catalog/operator_details/20260830_221449/analysis/operator_skill_review.md`
