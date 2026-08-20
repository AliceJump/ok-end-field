# AGENTS.md

项目级强制规则。每次会话开始必须读取并遵守。

## 提交与 PR 强制规则（最高优先级，必须先读）

### PowerShell 双引号字符串中的反引号（必踩坑）

在 PowerShell 双引号字符串里，反引号 `` ` `` 是**转义字符**而非字面量：`` `$HOME `` 保留字面量不展开，而 `$HOME` 会展开变量；合法转义如 `` `a ``（0x07）、`` `r ``（CR）、`` `n ``（LF）会变成控制字符；未知序列如 `` `s `` 会**移除反引号、保留原字符**（`` "Use `src` END" `` → `Use src END`）。给 `gh pr create/edit --body "..."` 等传 Markdown（含代码反引号）时：

- **必须用单引号字符串**（`'...'` 或 `@'...'@` here-string），单引号内反引号是字面量（`` 'Use `src` END' `` → `` Use `src` END ``），`gh pr edit --body $body` 传变量也可；
- **禁止用双引号传含反引号的 body**——反引号会被移除或变控制字符（如 `` `assets `` 里的 `` `a `` 变成 `^G`），造成 PR 描述格式错乱；
- 已踩坑案例：PR #194、PR #203 的 body 中 `` `src/...` `` 全部丢失或变 `^G`，需 `gh pr edit` 重新提交。

**PR 创建后必须自检**——创建时把 body 存到本地变量/文件，创建后用 `gh pr view` 拉取远端 body 与本地**逐字比较**（不要只 `Select-String` 查反引号，无法发现控制字符）：

```powershell
# 创建前：$body 已在变量中
gh pr create --base master --head "$BRANCH" --title "..." --body $body
gh pr view <n> --json body --jq '.body' > remote_body.md
$body > local_body.md
git diff --no-index local_body.md remote_body.md   # 无输出 = 一致
```

若出现 `\xxx\` 或 `^G` 等异常字符，用单引号 here-string 重写 body：

```powershell
$body = @'
...正文（含 `反引号`）...
'@
gh pr edit <n> --body $body
```

### 提交检查清单

提交或创建 PR 前依次确认：

1. PR/commit body 含代码反引号时用单引号 here-string，且创建后自检通过（见上）。
2. 不提交敏感信息（token、密钥、私钥）。`.pem`、`RELEASE_APP_PRIVATE_KEY` 等只进 Secrets 不进仓库。
3. 配置键名修改走 `.agents/skills/ok-config-migration` 严格顺序。
4. 中文 commit message 与仓库风格一致（`fix:`/`feat:`/`docs:`/`refactor:`/`ci:` 前缀）。

## 配置键名修改（重要）

`configs/` 目录下的 JSON 是用户运行数据，修改 `default_config` 中的配置键名时必须遵守严格顺序（迁移表 → 迁移测试 → i18n → 文档 → 恢复），否则会丢失用户配置。**完整流程见 `.agents/skills/ok-config-migration`**。要点：

1. **先加迁移表，再改键名**：同一任务类中先加 `config_key_migrations = {旧键: 新键}`，再改 `default_config` / 键名常量 / 键生成函数，同一提交完成。
2. **迁移表生效前禁止运行程序**：先运行迁移测试（`tests/TestZipLineConfig.py` 等实际迁移测试），测试断言旧键值已迁移到新键（被测函数为 `migrate_config_file_keys(<任务名>, migrations)`，见 `src/tasks/onetime/DeliveryTask.py`）。
3. **同步 i18n**：同步全部 `i18n/*/LC_MESSAGES/ok.po` 的 msgid 并 `task_i18n_helper.py compile`。
4. **同步文档**：搜索 `docs/` 更新旧键名。
5. **配置丢失可恢复**：`logs/ok-script.log` 的 `Config:init self.config = {...}` 保存完整历史配置，可从最后一次含旧键名的记录恢复。

## 运行环境

- Python：依赖通过 [uv](https://docs.astral.sh/uv/) 管理，`uv sync` 创建仓库本地 `.venv`，用 `uv run python ...` 执行（见 `.agents/skills/use-local-venv`）。`pyproject.toml` + `uv.lock` 为唯一来源，`requirements.txt` 为发布流水线的派生产物，勿手改。
- 测试：`run_tests.ps1`（经 `uv run`）或 `uv run python -m unittest discover -s tests`。
- 日志：`logs/ok-script.log`（配置历史、任务执行、OCR 均可在此排查）；历史日志位于同目录的 `ok-script.YYYY-MM-DD.log` 文件中。

## 代码风格

- 任务类基于 ok-script：`BaseTask` / `TriggerTask`，见 `.agents/skills/ok-script-tasks`。
- 任务字符串国际化见 `.agents/skills/ok-script-i18n`（含 `assets/lang/` lang JSON 约定）。
- OCR 语言资源与 OCR 混淆补丁见 `.agents/skills/ok-script-ocr-lang`（`assets/lang/*.json` schema、active locale、`ocr_text_fix.json`）。
- 生成任务代码见 `.agents/skills/ok-script-codegen`（含本项目 OCR/模板参数参考）。
- 配置键名迁移见 `.agents/skills/ok-config-migration`。
- GitHub Actions 工作流/rulesets/性能见 `.agents/skills/github-workflows`、`.agents/skills/github-rulesets`、`.agents/skills/github-actions-performance`。

## lang JSON key 命名约定（重要）

见 `.agents/skills/ok-script-i18n` 的「Lang JSON Convention」节，要点：

- **新增 key 用语义化命名**（如 `inst_title`），不要沿用旧的 `k_<md5前8位>` hash 风格；旧 `k_*` 保持不动，两种风格可共存。
- 每个 key 下为 6 种语言节点（`zh_CN`/`zh_TW`/`en_US`/`ja_JP`/`ko_KR`/`es_ES`），格式 `{"string": "..."}` 或 `{"pattern": "..."}`（详见 skill 的节点类型说明）。
- **lang JSON 只放 OCR 匹配文本**。UI 说明（如 `instructions` 富文本）用 `self.tr("中文msgid")` 走 gettext，msgid 写入 `i18n/*/LC_MESSAGES/ok.po` 后 `task_i18n_helper.py compile`。
- **最小原则**：emoji（`📍` `⚙️` `🖱️` 等）、`└─`/`├─`、HTML 标签/颜色等无需翻译的内容留在代码里拼，只把需翻译的纯文本放进 i18n 数据。
- **动态键名翻译**：`instructions` 里动态读取的配置键名显示时经 `self.tr(键名)` 翻译；查配置值用原始键名，显示用翻译后的键名（见 `src/tasks/mixin/zip_line_mixin.py`）。
