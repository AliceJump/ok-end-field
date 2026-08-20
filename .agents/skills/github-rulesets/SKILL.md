---
name: github-rulesets
description: Configure and troubleshoot GitHub repository rulesets (branch/tag protection) on ok-script app repos: rule combinations that block PR merges, bypass actors (users, GitHub Apps), and GitHub App tokens for auto-tagging. Use when editing rulesets, diagnosing "Cannot update this protected ref" / "Repository rule violations found" / merge BLOCKED, setting up a GitHub App to create tags from a workflow, or converting direct-push workflows to PRs. Lessons from ok-end-field PRs #194-#200.
---

# GitHub Rulesets — Branch/Tag Protection

## Purpose

Verified patterns for GitHub repository rulesets on ok-script app repos: which rule combinations break PR merging, how bypass works (and why `github-actions[bot]` can't bypass), and how to let workflows create tags via a custom GitHub App.

## 1. Rule combinations and merge blockers

### `update` (Restrict updates) blocks ALL PR merges for non-bypassed users

- A ruleset with **both `update` + `pull_request`** on the default branch blocks squash/rebase merges with `Cannot update this protected ref` / `Repository rule violations found` — even with `required_approving_review_count: 0` and all status checks green.
- API shows `current_user_can_bypass: never` when the caller isn't in `bypass_actors`.
- **Verified working set** for the default branch (lets PRs merge, still blocks direct pushes):
  `deletion` + `non_fast_forward` + `required_linear_history` + `pull_request` (NO `update` rule).
- With `bypass_actors` empty, even the repo owner must go through a PR — direct push fails with `Changes must be made through a pull request`.

### Inspect rulesets

```powershell
gh api repos/<owner>/<repo>/rulesets --jq '.[] | {name, target, enforcement, rules: [.rules[].type]}'
gh api repos/<owner>/<repo>/rulesets/<id> --jq '{enforcement, rules: [.rules[].type], bypass: [.bypass_actors[] | {type: .actor_type, id: .actor_id}]}'
```

## 2. Bypass actors

### `github-actions[bot]` cannot be a bypass actor (hard limitation)

- `actor_type: Integration, actor_id: 15368` is rejected: `Actor GitHub Actions integration must be part of the ruleset source or owner organization`. Not available in the UI bypass picker either.
- **Consequence**: workflows pushing tags/branches with `GITHUB_TOKEN` get blocked by any ruleset. You cannot whitelist the built-in Actions bot.
- Fix: custom GitHub App in `bypass_actors`, or a PAT user in the list, or restructure the workflow to not need bypass.

### Bypass actor types

- `User` (actor_id = GitHub user numeric id), `Integration` (actor_id = GitHub App id), `Team`, `RepositoryRole` (admin=5 etc.), `DeployKey`, `OrganizationAdmin`/`EnterpriseOwner` (id ignored; not for personal repos).
- `bypass_mode`: `always` (full bypass) or `pull_request` (branch rulesets only; NOT valid on tag rulesets — use `always`).

## 3. GitHub App token for auto-tagging (tag ruleset with `creation` rule)

### Setup

1. Create GitHub App (Repository permissions → Contents: Read and write; no webhook; install on the repo; record **App ID** + download private key `.pem`).
2. Add App to the tag ruleset bypass list:
   ```json
   "bypass_actors": [
     {"actor_id": <user_id>, "actor_type": "User", "bypass_mode": "always"},
     {"actor_id": <app_id>, "actor_type": "Integration", "bypass_mode": "always"}
   ]
   ```
3. Secrets/variables:
   - App ID is **public** (API-discoverable) → plain repository **variable** (`vars.RELEASE_APP_ID`), not a secret.
   - Private key is sensitive → **secret** (`RELEASE_APP_PRIVATE_KEY`) with the **full PEM** (BEGIN/END markers required; trimming them breaks parsing).

### Why a GitHub App (not GITHUB_TOKEN) for tag creation

- `GITHUB_TOKEN` pushes do **not** trigger other workflows' `push` events (official limitation) and cannot bypass rulesets.
- A GitHub App installation token (or PAT) **does** trigger `push` events — so once on an App token you can also drop the old `repository_dispatch` workaround (avoids double builds).

## 4. Direct-push workflows must move to PRs

Workflows that `git push` to a branch protected by the `pull_request` rule fail. Convert to: commit on a fixed branch → `git push -f origin <branch>` → `gh pr create` (or `gh pr edit` if the PR already exists). Add `pull-requests: write` permission.

```yaml
- name: Push branch and open PR
  env:
    GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    set -euo pipefail
    BRANCH="chore/update-data"
    git push -f origin "$BRANCH"
    if gh pr view "$BRANCH" --json number --jq '.number' >/dev/null 2>&1; then
      gh pr edit "$BRANCH" --title "..." --body "..."
    else
      gh pr create --base master --head "$BRANCH" --title "..." --body "..."
    fi
```

- Note: PRs opened by `GITHUB_TOKEN` trigger CI in an **approval-required** state (official behavior).
- When intentionally reusing the same fixed branch + PR across scheduled runs, concurrent runs can overwrite each other — add a workflow-level `concurrency` group to cancel the older run, or use a branch name incorporating `GITHUB_RUN_ID` and drop the `-f` force-push.

## 5. Reference

- Ruleset REST API: `gh api repos/<owner>/<repo>/rulesets` (create/update via `-X POST/PUT --input`).
- A PR can show `mergeStateStatus: BLOCKED` with all checks green if a commit status (e.g. CodeRabbit) is pending; `gh pr merge --squash --admin` bypasses.
