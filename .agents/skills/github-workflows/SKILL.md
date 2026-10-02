---
name: github-workflows
description: Edit or diagnose GitHub Actions YAML in ok-script repos. Use for parse failures, actionlint validation, workflow permissions, and SonarCloud workflow rules.
---

# GitHub Workflows

## Parse and validate

A single-line `run:` value containing `:all:` can make GitHub reject the file with “Invalid workflow file” and **0 jobs**. Quote the full value, for example `run: 'pip install --only-binary :all: ...'`, or use `run: |` for a multiline command. A block scalar treats `:all:` as shell text.

When a run has 0 jobs, inspect its GitHub UI error and `gh api repos/<owner>/<repo>/actions/runs/<id>/jobs`; this is a workflow-file problem rather than a failed step. Before pushing, parse every `.yml` and `.yaml` file and run `actionlint` on explicit paths. PowerShell does not reliably expand native-command globs. Use [references/validate.md](references/validate.md); unavailable `actionlint` is a validation gap, not a passing check. Generic YAML parsing cannot detect invalid Actions contexts.

## SonarCloud and permissions

- `githubactions:S8544` does not trace a pinned `-r requirements-file`; inline the exact pinned package in the workflow command when addressing that finding.
- `S8264` / `S8233`: place read/write permissions at the job level. `S8541`: use `--only-binary :all:` for pip install.
- Creating PRs needs `pull-requests: write` and branch pushes need `contents: write`. Set `persist-credentials: false` on checkout if a later push must use a different token; see `$github-rulesets` for the App-token pattern.
