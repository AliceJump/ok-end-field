---
name: github-actions-performance
description: Analyze and improve GitHub Actions job performance on ok-script app repos: checkout version differences, shallow-clone side effects, and partial-sync-repo tag pruning cost. Use when a job is slow, when reasoning about sync/partial-sync-repo tag deletion behavior, or when attributing a speed change to a specific commit or action version. Verified on ok-end-field build sync job (9→3→1.4 min across PRs #182, #194).
---

# GitHub Actions Performance — Checkout & Sync

## Purpose

How to reason about GitHub Actions job speedups, with the verified ok-end-field `sync` job case (9 min → 3 min → 1.4 min) as the worked example.

## 1. The worked example: partial-sync-repo sync job

### Timeline and attribution

| Change | Commit | Cause |
|---|---|---|
| 9-11 min → ~3 min | `37593fa` (2026-08-07) | checkout gained `fetch-depth: 0` + `fetch-tags: true` → stopped mis-deleting 304 tags |
| ~3 min → 1.4 min | `e1125be` (#182, 2026-08-19) | `actions/checkout@v4` → `@v6`, faster full-history fetch |

### Root cause of the 9→3 drop (NOT orphan tags)

- Old `sync` job used `actions/checkout@v4` with only `lfs: true` → **shallow clone (single commit)**. Local `git tag` only saw tags reachable from HEAD.
- `ok-oldking/partial-sync-repo@master` compares source vs target tag sets and `git push --delete`s any target tag "missing" in source. With a shallow clone, ~304 existing source tags (e.g. `v0.1.35`) were invisible locally → mis-deleted from the target repo at ~2 s each = ~10 min.
- **They were NOT orphans** — the tags existed in the source repo; the local clone just couldn't see them.
- Fix: give the checkout `fetch-depth: 0` + `fetch-tags: true` so all tags are visible.

### 3→1.4 min

- `actions/checkout@v4` → `@v6` made the full-history + tags fetch faster.

## 2. Debugging / attribution method

- Measure per-job wall time from the run log timestamps (jobs API `startedAt`/`completedAt` is null for old runs — parse log lines instead):
  ```powershell
  $job = gh api repos/<owner>/<repo>/actions/runs/<id>/jobs --jq '.jobs[] | select(.name == "<job>") | .id'
  gh api repos/<owner>/<repo>/actions/jobs/$job/logs   # parse first/last timestamp
  ```
- For partial-sync-repo, count `Deleting tag` lines — that loop is the dominant cost:
  ```powershell
  gh api repos/<owner>/<repo>/actions/jobs/$job/logs | Select-String "Deleting tag"
  ```
- Correlate the speed change with the exact run's `head_sha` → `git log` the touched file to find the responsible commit.

## 3. partial-sync-repo behavior notes

- Inputs: `repos`, `sync_list`, `tag`, optional `gitignore_file`, `show_author`. Pin `ok-oldking/partial-sync-repo` to a full 40-char commit SHA (with a human-readable version in a trailing comment) instead of `@master` — branch refs change as upstream moves, so the workflow could execute unreviewed code. (When updating the pin, verify the new SHA's content before adopting it; do not copy a specific SHA into this doc.)
- It: shallow-clones each target repo fresh (`fse.remove` + `git clone`), syncs listed files, generates a commit message from last-synced tag → current tag, then synchronizes tags (deletes source-missing tags, applies current + special tags like `lts`).
- Commit `2bf054d` "don't sync tags if no changes": when files didn't change, `return` before the tag loop — big time save when there is nothing to commit.
- If you want "don't delete old tags / only push the new tag", the action has no option — you must fork it or replace with custom git commands.

## 4. Checkout version note

- `actions/checkout@v4` vs `@v6` differs meaningfully for full-history (`fetch-depth: 0`) + tags fetch performance. Upgrading is a legitimate, measurable speedup for tag-heavy workflows.
- Always inspect whether a speed change is real code/version optimization vs. a one-time state cleanup (e.g. a single run that deleted 304 accumulated tags) before attributing it.
