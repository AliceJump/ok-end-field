# Skills

本文件只负责技能发现。遇到下列场景时，先读取对应技能文件并按其中流程执行。

| Skill | 适用场景 | 路径 |
|---|---|---|
| `repository-workflow` | 提交/PR、安全检查、PowerShell Markdown 引号、测试与日志入口、批量改码验证 | `.agents/skills/repository-workflow/SKILL.md` |
| `deploy` | 提交完成改动、计算并创建 stable/beta/alpha tag、推送发布远端 | `.agents/skills/deploy/SKILL.md` |
| `use-local-venv` | 运行 Python、测试、lint、依赖检查或安装，统一使用 uv 与仓库 `.venv` | `.agents/skills/use-local-venv/SKILL.md` |
| `ok-script-tasks` | 创建、修改、注册或审阅 `BaseTask` / `TriggerTask` 任务 | `.agents/skills/ok-script-tasks/SKILL.md` |
| `ok-script-codegen` | 根据描述或截图生成任务 `run()` 自动化代码 | `.agents/skills/ok-script-codegen/SKILL.md` |
| `ok-script-i18n` | gettext UI 文案、PO/MO、lang JSON 归档约定、收集池防污染与目录冲突合并 | `.agents/skills/ok-script-i18n/SKILL.md` |
| `ok-script-ocr-lang` | OCR 匹配语言节点、active locale、`ocr_text_fix.json` 与语言引用排错 | `.agents/skills/ok-script-ocr-lang/SKILL.md` |
| `ok-config-migration` | 修改持久化配置键名并安全迁移用户数据 | `.agents/skills/ok-config-migration/SKILL.md` |
| `ok-script-pr-review` | 触发、等待、核验、回复和解析 CodeRabbit PR 审阅 | `.agents/skills/ok-script-pr-review/SKILL.md` |
| `github-workflows` | 编辑或排查 GitHub Actions YAML、权限、actionlint 与 SonarCloud 规则 | `.agents/skills/github-workflows/SKILL.md` |
| `github-rulesets` | 分支/tag ruleset、bypass、GitHub App token 与受保护分支工作流 | `.agents/skills/github-rulesets/SKILL.md` |
| `github-actions-performance` | 分析 Actions 慢任务、checkout 历史/tag 与同步性能 | `.agents/skills/github-actions-performance/SKILL.md` |
| `log-watcher-ops` | 本机私有 log-watcher 邮件、监控、清理、看板部署与 Apache 反代运维 | `.agents/skills/log-watcher-ops/SKILL.md`（本地忽略，不随仓库发布） |
