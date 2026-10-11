---
name: repository-workflow
description: Apply ok-end-field repository rules for tests, commits, PRs, secrets, PowerShell text handling, and broad source edits. Use alongside a narrower skill when its domain applies.
---

# Repository Workflow

Use `$use-local-venv` for Python and the standard/focused test entry points. Skill script regressions run with `uv run --locked python -m unittest tests.TestSkillScripts -v`. Runtime logs are `logs/ok-script.log` and dated rotations.

## Commit and PR

1. Before committing, opening, updating, or restacking a PR, load `$pr-scope-responsibility` and review the proposed diff against the PR title/description. Perform this check before publishing the PR, not only after CodeRabbit comments. If the correct disposition is `Extract a boundary` or `Split prerequisite/follow-up`, restructure the change first. Do not knowingly publish a PR that is expected to fail the repository responsibility/scope review guardrails.
2. Inspect `git status --short --branch`, working and staged diffs. Keep unrelated user changes out of the commit. Run focused verification and the standard suite when the change warrants it. For added, renamed, or deleted `tests/Test*.py` files, also complete the [CI responsibility group checks](../use-local-venv/SKILL.md#ci-responsibility-groups) before committing or pushing.
3. Never commit tokens, passwords, PEM/private keys, credential exports, or secret-bearing config. Use recent commit language and a suitable prefix such as `fix:`, `feat:`, `docs:`, `refactor:`, or `ci:`.
4. Remote `master` is locked. Follow the PR base and commit scope checks below when creating or updating a PR. Land code through a PR; one PR should cover one responsibility. Never push code commits directly to `master`.
5. For a persisted task config key, value format, or owning-task change, load `$ok-config-migration` before changing defaults or running the app.

## PR base and commit scope

Unless the user explicitly requests another base or a stacked PR, create a fresh branch from the fetched remote `origin/master`, using `git switch -c codex/<task-name> origin/master` after replacing the task name. Do not branch from the current worktree HEAD or local `master`: either may contain commits from another PR that are absent from the remote base. For another explicitly requested base, substitute its remote ref in the checks below.

Before pushing or opening a PR, fetch `origin` again and inspect both the commit list and the full proposed diff:

```powershell
git rev-list --left-right --count origin/master...HEAD
git log --oneline origin/master..HEAD
git diff --stat origin/master...HEAD
git diff origin/master...HEAD
git merge-base HEAD origin/master
git rev-parse origin/master
```

The left count is remote base commits missing from HEAD; rebase onto the fetched base when nonzero. The right count is commits contributed by the branch; verify every listed commit belongs to this task, and that every changed file matches the PR scope. The merge base must equal the fetched `origin/master`. Checking only `HEAD..origin/master` for zero detects being behind but does not detect inherited commits from other PRs.

If unrelated commits are already inherited, preserve the original branch state and rebuild from `origin/master` by cherry-picking only this task's commits, or use `rebase --onto` with a verified boundary. A plain `rebase origin/master` does not remove unrelated commits that are already on the branch. Review the resulting commit list and diff, and rerun verification affected by the changed base before publishing.

After publishing, use `gh pr view <number> --json baseRefName,baseRefOid,headRefOid,commits,files` to verify the GitHub PR's actual base, head, commits, and files against the local result. If the remote base advances before publication, synchronize and repeat the checks. For an authorized repair that rewrites a published branch, use `--force-with-lease` with the previously inspected remote head; if the lease fails, fetch and inspect the new commits before retrying.

## Windows text handling

PowerShell double-quoted strings treat Markdown backticks as escapes. Use a single-quoted here-string or body file for PR text, then compare the fetched PR body with the intended text rather than checking only for a substring. See [references/powershell-pr-body.md](references/powershell-pr-body.md).

A non-ASCII `.ps1` script that must run in Windows PowerShell 5.1 needs UTF-8 **with BOM**; console output encoding does not fix source decoding. Keep ASCII-only scripts plain UTF-8 unless another convention applies.

## Broad automated source edits

For batch docstring, annotation, formatting, or source rewrites, record the baseline first. Parse every changed Python file (read BOM-bearing files with `utf-8-sig`), compare executable statements by qualified function name against the baseline, and review any unexpected reduction. Run focused tests, inspect the full diff, and use `git diff --check` before committing.

Report changed areas, verification, known pre-existing failures, and whether commit/PR/push actually completed.
