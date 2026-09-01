---
name: github-workflows
description: Configure and troubleshoot GitHub Actions workflow files on ok-script app repos: YAML syntax traps (single-line run with :word:, block scalars), diagnosing "Invalid workflow file" / jobs:0 runs that never start, and SonarCloud workflow rules (githubactions:S8544). Use when editing .github/workflows/*.yml or *.yaml, fixing a workflow that fails at parse time, or validating YAML before push. Lessons from ok-end-field PRs #194-#200.
---

# GitHub Workflows — YAML & File Troubleshooting

## Purpose

Pitfalls and verified fixes for `.github/workflows/*.yml` and `.github/workflows/*.yaml` files: YAML traps that break parsing, how to diagnose workflows that never start, and SonarCloud rules that flag workflow files.

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
- **Always validate YAML before pushing**, in two separate steps. PowerShell does not reliably expand native-command globs, so enumerate both extensions and pass explicit paths:
  ```powershell
  $workflowFiles = @(
      Get-ChildItem -LiteralPath ".github/workflows" -File |
          Where-Object { $_.Extension -in ".yml", ".yaml" }
  )
  if ($workflowFiles.Count -eq 0) {
      throw "No workflow YAML files found"
  }
  $workflowPaths = @($workflowFiles.FullName)

  # Generic YAML syntax. PyYAML is locked in the repository dev dependencies.
  uv run --locked python -c 'import pathlib, sys, yaml; [yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8-sig")) for path in sys.argv[1:]]; print(f"parsed {len(sys.argv) - 1} workflow YAML file(s)")' @workflowPaths
  if ($LASTEXITCODE -ne 0) {
      throw "Workflow YAML parsing failed"
  }

  # GitHub Actions structure, expressions, and contexts.
  $actionlint = Get-Command actionlint -ErrorAction SilentlyContinue
  if (-not $actionlint) {
      throw "actionlint is required; install it before validating workflows"
  }
  $actionlintPath = $actionlint.Source
  & $actionlintPath @workflowPaths
  if ($LASTEXITCODE -ne 0) {
      throw "actionlint failed"
  }
  ```

  Generic `yaml.safe_load` cannot catch undefined contexts such as `matrix.os` or `matrix.arch`; an unavailable `actionlint` is a reported validation failure, not a skipped check.

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
