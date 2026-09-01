# 官方 WIKI 干员数据抓取与技能分析

## 森空岛官方 WIKI 完整快照

已新增 `scripts/data-capture/capture_skland_operator_details.py`，通过真实浏览器监听官方已签名的 `item/info` 响应，抓取当前 WIKI 中全部干员的完整详情。

以下 2026-08-30 快照和统计仅是历史复现样例，不代表 WIKI 当前条目数或当前分析结果。样例快照为 `tools/wiki_catalog/operator_details/20260830_221449/`（由 gitignore 排除，仅保留在本地）。

- 干员目录：33 项，含噗切娜、提弗洛斯、管理员男女两个词条；
- 完整详情：33/33 成功，原始 JSON 约 13.4 MiB；
- 渲染文本：33 份，共 487,905 字符；
- 每份详情完整保留 `documentMap`、`chapterGroup`、`extraInfo`、`widgetCommonMap`；
- 可利用信息包括角色资料、职业/武器/能力、各等级属性、精英化材料、全部技能描述、WIKI 当前展示的 RANK 1–9 技能倍率与数值、天赋、潜能、能力扩延、推荐武器、档案、语音与资源链接；
- 同时保存 `catalog.json`、`char_pool.json`、`weapon_pool.json`、详情页纯文本，以及每名干员对应的完整 `item/list` 关联物品响应（33/33）；
- `manifest.json` 记录每名干员的 item ID、详情 URL、更新时间、文件路径、数据体积、文档组件数量、文件计数以及完整性状态；`latest.json` 只指向最近一次完整抓取。

完整原始 JSON 是后续自动解析倍率和条件的权威 WIKI 输入；纯文本仅用于人工检索，不应替代结构化原始数据。

## 从干净检出复现

Playwright 属于仓库开发工具依赖，不进入主应用运行依赖。仓库现有依赖分组使用 `dev`，因此抓取工具也安装在该组中：

```powershell
git clone https://github.com/AliceJump/ok-end-field.git
Set-Location ok-end-field
git checkout tools/operator-wiki-analysis
uv sync --locked --group dev
uv run --locked python scripts/data-capture/capture_skland_operator_details.py --help
uv run --locked python scripts/data-capture/capture_skland_operator_details.py
uv run --locked python scripts/skill-data/analyze_operator_skills.py
```

抓取器优先启动系统安装的 Chrome（Playwright `channel="chrome"`）。如果系统 Chrome 不存在或无法启动，会回退到 Playwright bundled Chromium。安装 Python 依赖不会自动下载浏览器二进制：系统 Chrome 可用时无需额外安装；只有需要 bundled Chromium 回退时，才运行：

```powershell
uv run --locked playwright install chromium
```

Linux 环境若同时缺少 Chromium 的系统库，可按 Playwright 要求使用 `uv run --locked playwright install --with-deps chromium`，该命令可能需要系统包安装权限。

可用 `--proxy http://127.0.0.1:10808` 指定代理，用 `--headed` 显示浏览器窗口。抓取目录使用秒级时间戳并原子创建；同秒并发会明确报错，不会复用目录混合数据。

抓取结束后会核对原始 catalog 总数、请求子集、失败项、必需全局文件、manifest 条目和实际文件数。失败或 `--limit` 形成的子集快照仍保留用于诊断，并在 `manifest.json` 中写入 `"complete": false` 和 `incomplete_reasons`，但不会覆盖已有 `latest.json`。

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

默认分析读取 `latest.json`，并拒绝 manifest 未明确标记为完整的快照。若要诊断不完整快照，必须同时显式指定快照和允许 partial：

```powershell
uv run --locked python scripts/skill-data/analyze_operator_skills.py --snapshot 20260830_221449 --allow-partial
```

历史样例分析结果（2026-08-30）：33 名干员、127 个技能、64 个条件、9 个多条件技能、16 个特殊层数技能、21 个待复核技能。输出位于：

- `tools/wiki_catalog/operator_details/20260830_221449/analysis/operator_skill_analysis.json`
- `tools/wiki_catalog/operator_details/20260830_221449/analysis/operator_skill_review.md`
