---
name: ok-script-pr-review
description: Handle AI pull-request reviews (CodeRabbit) on ok-script app repos: trigger fresh reviews on the latest branch, reply to and resolve review threads, retry after rate limits, and re-verify stale comments against the current code. Use when a PR has coderabbit comments that need triage, when a force-push has made review comments outdated, when a review needs to be re-run after fixes, or when threads must be marked as resolved via API instead of the GitHub UI.
---

# CodeRabbit PR Review Handling

## Purpose

A focused workflow for triaging and responding to CodeRabbit AI reviews on ok-script app pull requests (the pattern used across PRs #175-#179 of ok-end-field). Covers triggering, replying, resolving, and handling rate limits.

## Workflow

### 1. Survey current review state

For each PR under review, list CodeRabbit review summaries and inline comments:

```powershell
# review-level state
gh api repos/<owner>/<repo>/pulls/<n>/reviews --jq '.[] | select(.user.login|contains("coderabbit")) | "\(.user.login) [\(.state)] \(.submitted_at): \(.body // "")"'

# inline review comments
gh api "repos/<owner>/<repo>/pulls/<n>/comments" --jq '.[] | "\(.user.login) \(.path):\(.line // .original_line) \(.created_at)\n\(.body)\n---"'
```

### 2. Trigger a fresh review on the latest code

CodeRabbit reviews whatever was pushed when its run started; a later force-push (rewritten branch) makes the old review stale. Re-run it by commenting on the PR:

```powershell
gh pr comment <n> --body "@coderabbitai review"
```

### 3. Handle rate limits

CodeRabbit has a rate limit. Signals: a PR was pushed/fixed but no new review appears (`updated_at` moves, review timestamps do not), or the re-trigger comment is ignored. Action: wait, then retry the trigger. Do not spam the trigger; retry after a sensible delay.

### 4. Reply to a review comment

Reply on the thread to record the disposition (accepted / fixed / stale). Replying often auto-resolves the thread:

```powershell
gh api repos/<owner>/<repo>/pulls/comments/<comment_id>/replies -X POST -f body="已采纳：<commit sha 与说明>"
```

### 5. Resolve threads via API (Resolve conversation button)

The GitHub "Resolve conversation" button is available as GraphQL. First list threads and match by comment body / path / line:

```powershell
gh api graphql -f query='query { repository(owner:"<o>", name:"<r>") { pullRequest(number: <n>) { reviewThreads(first: 20) { nodes { id isResolved isOutdated comments(first:1) { nodes { id body } } } } } } }'
```

Then resolve a thread (mutation is `resolveReviewThread`, NOT `updatePullRequestReviewThread`):

```powershell
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<threadId>"}) { thread { id isResolved } } }'
```

### 6. Triage rules

- Verify every finding against the CURRENT branch code before acting (the review may predate a force-push).
- `isOutdated=true` threads are stale (file moved/deleted by a later rewrite) -> safe to resolve after a one-line explanation.
- Already-fixed threads -> resolve after confirming the fix commit exists and tests pass.
- Accept valid suggestions and push the fix, then reply + resolve.
- Re-run the target test before resolving a correctness (Major) finding:
  `uv run python -m unittest <test_path>` (or the project's test command).

## Gotchas

- `gh api graphql` queries on Windows PowerShell: wrap the query in single quotes; use string concatenation for interpolated ids (`'query { ... "' + $id + '" }'`).
- Thread ids look like `PRRT_kwDO...`; they are opaque, fetch them via the query above.
- A review comment id (`pulls/comments/<id>`) is NOT the same as a thread id; use the GraphQL `reviewThreads` listing to map them.
- `app.quit()` / `os._exit` behaviors seen during crash diagnosis are unrelated to review handling; keep this skill scoped to review triage.