---
name: github-workflows
description: Configure and troubleshoot GitHub Actions workflow files on ok-script app repos: YAML syntax traps (single-line run with :word:, block scalars), diagnosing "Invalid workflow file" / jobs:0 runs that never start, and SonarCloud workflow rules (githubactions:S8544). Use when editing .github/workflows/*.yml, fixing a workflow that fails at parse time, or validating YAML before push. Lessons from ok-end-field PRs #194-#200.
---

# GitHub Workflows — YAML & File Troubleshooting

## Purpose

Pitfalls and verified fixes for `.github/workflows/*.yml` files: YAML traps that break parsing, how to diagnose workflows that never start, and SonarCloud rules that flag workflow files.

## 1. YAML traps

### `--only-binary :all:` in single-line `run:` breaks YAML

```yaml
# BAD — `:all:` parsed as a mapping → "Invalid workflow file ... error in your yaml syntax on line 52", jobs: 0
run: pip install --only-binary :all: -r requirements-docs.txt
# GOOD — quote the whole value
run: 'pip install --only-binary :all: -r requirements-docs.txt'
```

- A single-line `run: <value>` containing `:word:` (colon followed by content) is parsed as a YAML mapping by the GitHub workflow parser → GitHub refuses to start the workflow. This is specific to how the GitHub parser treats the unquoted single-line `run` value; YAML 1.2 plain scalars do allow a colon without a following space in general. Quoting the entire single-line `run` value remains the fix, and text inside a block scalar (`run: |`) is unaffected.
- Inside a **block scalar** (`run: |` multi-line), `:all:` is plain text and is safe (download_stats.yml was unaffected).
- **Always validate YAML before pushing**, in two separate steps:
  1. Generic YAML syntax first:
  ```powershell
  uv run python -c "import yaml; yaml.safe_load(open('<file>', encoding='utf-8')); print('OK')"
  ```
  2. GitHub Actions structure/expressions/contexts (covers undefined `matrix.os`/`matrix.arch` and similar): run `actionlint .github/workflows/*.yml` or an equivalent validator. Generic `yaml.safe_load` alone cannot catch workflow-structure errors.

## 2. Diagnosing "workflow file issue" / jobs: 0

- When `gh run view <id>` shows `This run likely failed because of a workflow file issue` and the run has **0 jobs**, the workflow never started — it's a file-parsing problem, not a step failure.
- Check job count: `gh api repos/<owner>/<repo>/actions/runs/<id>/jobs` → `jobs: []`.
- Locate the offending line from the GitHub UI error (e.g. "error in your yaml syntax on line 52") and fix the YAML, then push to re-trigger.

## 3. SonarCloud rules on workflow files

### S8544 — dependencies not locked

- SonarCloud reports `githubactions:S8544` even when the referenced requirements file pins exact versions (`mkdocs-material==9.7.7`) — it does **not** trace `-r file`.
- Fix: inline the pinned package instead of `-r file`:
  ```yaml
  run: 'pip install --only-binary :all: mkdocs-material==9.7.7'
  ```

### Other workflow rules seen

- `S8264` / `S8233`: move read/write permissions from workflow level to job level.
- `S8541`: add `--only-binary :all:` to pip install.

## 4. Permission tips

- Workflows that create PRs need `pull-requests: write` (plus `contents: write` for branch pushes).
- `persist-credentials: false` on `actions/checkout` stops the persisted `github.token` from overriding later `git push` credentials (see github-rulesets skill for the full App-token push pattern).
- **`gh pr merge --auto` fails with `GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)`** when the repository setting `allow_auto_merge` is false (GitHub default). Enable it once via API:
  ```powershell
  gh api -X PATCH repos/<owner>/<repo> -F allow_auto_merge=true --jq '{allow_auto_merge}'
  ```
  Also verify `allow_squash_merge`/`allow_rebase_merge` match the ruleset's `allowed_merge_methods`. Without this, the PR is created fine but auto-merge enablement fails and the job exits non-zero.
