---
name: ok-script-pr-review
description: Handle AI pull-request reviews (CodeRabbit) on ok-script app repos: trigger fresh reviews on the latest branch, reply to and resolve review threads, retry after rate limits, and re-verify stale comments against the current code. Use when a PR has coderabbit comments that need triage, when a force-push has made review comments outdated, when a review needs to be re-run after fixes, or when threads must be marked as resolved via API instead of the GitHub UI.
---

# CodeRabbit PR Review Handling

## Purpose

A focused workflow for triaging and responding to CodeRabbit AI reviews on ok-script app pull requests (the pattern used across PRs #175-#179 of ok-end-field). Covers triggering, replying, resolving, and handling rate limits.

## Workflow

### 1. Survey current review state

For each PR under review, list CodeRabbit review summaries and inline comments. List endpoints must use `--paginate` so no comments are missed, and must output the comment `.id` plus `.in_reply_to_id` (needed to map replies to threads):

```powershell
# review-level state（--paginate 分页拉全）
gh api --paginate repos/<owner>/<repo>/pulls/<n>/reviews --jq '.[] | select(.user.login|contains("coderabbit")) | "\(.user.login) [\(.state)] \(.submitted_at): \(.body // "")"'

# inline review comments（--paginate 分页拉全，带评论 id 与回复关系）
gh api --paginate "repos/<owner>/<repo>/pulls/<n>/comments" --jq '.[] | "\(.id) reply_to=\(.in_reply_to_id // "-") \(.user.login) \(.path):\(.line // .original_line) \(.created_at)\n\(.body)\n---"'
```

### 2. Trigger a fresh review on the latest code

CodeRabbit reviews whatever was pushed when its run started; a later force-push (rewritten branch) makes the old review stale. Re-run it by commenting on the PR:

```powershell
# 普通新提交：默认增量评审
gh pr comment <n> --body "@coderabbitai review"
# force-push 重写分支后：需要完整评审（CLI: coderabbit review --full；
# 触发词见 CodeRabbit 文档），否则重写前的评论会失效/误判
gh pr comment <n> --body "@coderabbitai review --full"
```

### 3. Handle rate limits

CodeRabbit has a rate limit. Signals: a PR was pushed/fixed but no new review appears (`updated_at` moves, review timestamps do not), or the re-trigger comment is ignored. Action: wait, then retry the trigger. Do not spam the trigger; retry after a sensible delay.

### 4. Reply to a review comment

Reply on the thread to record the disposition (accepted / fixed / stale). The endpoint must be the reply sub-resource of the specific comment (`/pulls/<n>/comments/<comment_id>/replies`):

```powershell
gh api repos/<owner>/<repo>/pulls/<n>/comments/<comment_id>/replies -X POST -f body="已采纳：<commit sha 与说明>"
```

回复只记录处置意见，**不自动解析线程**；解析是独立步骤，需先确认修复/失效后执行（见下）。

### 5. Resolve threads via API (Resolve conversation button)

The GitHub "Resolve conversation" button is available as GraphQL. List threads with cursor pagination, returning the thread id plus the path/line/id of its first comment so comments can be matched to threads:

```powershell
gh api graphql -f query='query($cursor:String) { repository(owner:"<o>", name:"<r>") { pullRequest(number: <n>) { reviewThreads(first: 100, after: $cursor) { pageInfo { hasNextPage endCursor } nodes { id isResolved isOutdated comments(first:1) { nodes { id body path line } } } } } } }' -F cursor=null
```

用 `pageInfo.endCursor` 循环翻页直到 `hasNextPage` 为 false（PowerShell 里用单引号包 query，id 拼接用双引号）。

Then resolve a thread (mutation is `resolveReviewThread`, NOT `updatePullRequestReviewThread`):

```powershell
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<threadId>"}) { thread { id isResolved } } }'
```

### 6. Triage rules

- Verify every finding against the CURRENT branch code before acting (the review may predate a force-push).
- `isOutdated=true` 是线程已过时的信号，但**不能单独作为解析依据**：解析前仍需对照当前代码确认问题确已修复或已失效。
- 回复后查询 `isResolved`，仅在值为 `false` 且确认问题已修复/失效时才解析线程；不要把回复与自动解析绑定。
- Accept valid suggestions and push the fix, then reply + resolve.
- Re-run the target test before resolving a correctness (Major) finding using the repository's test command:
  `run_tests.ps1` 或 `uv run python -m unittest discover -s tests`。

## Gotchas

- `gh api graphql` queries on Windows PowerShell: wrap the query in single quotes; use string concatenation for interpolated ids (`'query { ... "' + $id + '" }'`).
- Thread ids look like `PRRT_kwDO...`; they are opaque, fetch them via the query above.
- A review comment id (`pulls/comments/<id>`) is NOT the same as a thread id; use the GraphQL `reviewThreads` listing to map them.
- `--paginate` 只对 REST 列表接口生效；GraphQL 用 `pageInfo.endCursor` 手动翻页。
- `app.quit()` / `os._exit` behaviors seen during crash diagnosis are unrelated to review handling; keep this skill scoped to review triage.