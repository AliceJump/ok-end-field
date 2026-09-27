---
name: github-rulesets
description: Diagnose or configure GitHub branch/tag rulesets on ok-script repos. Use for blocked PR merges, bypass actors, protected-branch workflows, or App-token tag creation.
---

# GitHub Rulesets

## Branch merge blockers

An `update` restriction combined with `pull_request` on the default branch blocked squash/rebase merges for non-bypassed users even with green checks and zero required approvals. The verified ok-end-field combination that permits PR merges while blocking direct pushes is `deletion` + `non_fast_forward` + `required_linear_history` + `pull_request`, without `update`. Inspect the current ruleset before changing it:

```powershell
gh api repos/<owner>/<repo>/rulesets
gh api repos/<owner>/<repo>/rulesets/<id>
```

A `mergeStateStatus: BLOCKED` result may also come from a pending commit status such as CodeRabbit; inspect checks and reviews before blaming rulesets.

## Bypass and tag automation

- The built-in `github-actions[bot]` integration cannot be added as this repository's bypass actor. Use a custom GitHub App or authorized PAT user when a workflow must bypass a ruleset. A tag ruleset uses `bypass_mode: always`; `pull_request` bypass mode applies only to branch rulesets.
- For App-based tags, grant repository Contents read/write, install the App, and add its Integration ID to tag-ruleset bypass. Store the public App ID in a repository variable and the **full PEM private key** in a Secret. Never commit the key.
- `GITHUB_TOKEN` pushes do not trigger another workflow's `push` event; an App installation token or PAT can. Remove redundant `repository_dispatch` triggers only after confirming the new token's behavior.
- Set `persist-credentials: false` on `actions/checkout` when later Git pushes must use the App token instead of checkout's stored token.

## Workflows targeting protected branches

Convert direct pushes to a feature branch and PR. A workflow that creates the branch and PR needs `contents: write` and `pull-requests: write`. For a reused fixed branch, guard concurrent runs before force-pushing, or use a unique run-specific branch. Check the resulting PR's CI/approval state; PRs opened by `GITHUB_TOKEN` may require approval before workflows run.

For API edits, inspect `bypass_actors` and existing rules before a `POST`/`PUT`. Keep branch protection and tag creation rules separate.
