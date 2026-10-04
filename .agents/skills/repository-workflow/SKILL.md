---
name: repository-workflow
description: Apply ok-end-field repository rules for tests, commits, PRs, secrets, PowerShell text handling, and broad source edits. Use alongside a narrower skill when its domain applies.
---

# Repository Workflow

Use `$use-local-venv` for Python and the standard/focused test entry points. Skill script regressions run with `uv run --locked python -m unittest tests.TestSkillScripts -v`. Runtime logs are `logs/ok-script.log` and dated rotations.

## Commit and PR

1. Before committing, opening, updating, or restacking a PR, load `$pr-scope-responsibility` and review the proposed diff against the PR title/description. Perform this check before publishing the PR, not only after CodeRabbit comments. If the correct disposition is `Extract a boundary` or `Split prerequisite/follow-up`, restructure the change first. Do not knowingly publish a PR that is expected to fail the repository responsibility/scope review guardrails.
2. Inspect `git status --short --branch`, working and staged diffs. Keep unrelated user changes out of the commit. Run focused verification and the standard suite when the change warrants it.
3. Never commit tokens, passwords, PEM/private keys, credential exports, or secret-bearing config. Use recent commit language and a suitable prefix such as `fix:`, `feat:`, `docs:`, `refactor:`, or `ci:`.
4. Remote `master` is locked. Fetch `origin`, branch from `origin/master`, and rebase before pushing if behind. Land code through a PR; one PR should cover one responsibility. Never push code commits directly to `master`.
5. For a persisted task config key, value format, or owning-task change, load `$ok-config-migration` before changing defaults or running the app.

## Windows text handling

PowerShell double-quoted strings treat Markdown backticks as escapes. Use a single-quoted here-string or body file for PR text, then compare the fetched PR body with the intended text rather than checking only for a substring. See [references/powershell-pr-body.md](references/powershell-pr-body.md).

A non-ASCII `.ps1` script that must run in Windows PowerShell 5.1 needs UTF-8 **with BOM**; console output encoding does not fix source decoding. Keep ASCII-only scripts plain UTF-8 unless another convention applies.

## Broad automated source edits

For batch docstring, annotation, formatting, or source rewrites, record the baseline first. Parse every changed Python file (read BOM-bearing files with `utf-8-sig`), compare executable statements by qualified function name against the baseline, and review any unexpected reduction. Run focused tests, inspect the full diff, and use `git diff --check` before committing.

Report changed areas, verification, known pre-existing failures, and whether commit/PR/push actually completed.
