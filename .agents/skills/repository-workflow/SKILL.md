---
name: repository-workflow
description: "Apply ok-end-field repository-wide engineering rules. Use when committing or opening/editing a PR, handling Markdown through PowerShell, checking secrets, choosing commit style, running the standard test suite, reading runtime logs, changing dependency files, or performing broad automated source edits that need structural verification."
---

# Repository Workflow

## Scope

Use this skill for repository-wide rules that do not belong to a narrower domain skill. If a task also matches a specialized skill, load both; the specialized workflow controls its domain details.

## Python environment and generated files

- Dependency sources of truth are `pyproject.toml` and `uv.lock`.
- Use `uv sync --locked` to materialize the repository `.venv`, and `uv run --locked python ...` for reproducible commands.
- `requirements.txt` is generated for publishing workflows; do not edit it directly.
- Run all tests with `scripts/testing/run_tests.ps1`, or a focused test with `uv run --locked python -m unittest <module> -v`.
- Run skill/helper-script regressions with `uv run --locked python -m unittest tests.TestSkillScripts -v`.
- Runtime diagnostics are in `logs/ok-script.log`; rotated files use `logs/ok-script.YYYY-MM-DD.log`.

## Commit and PR checklist

1. Inspect `git status --short --branch`, the relevant working diff, and the staged diff. Never include unrelated user changes.
2. Run focused verification, then the standard suite when the change warrants it.
3. Do not commit tokens, passwords, private keys, PEM files, credential exports, or secret-bearing config. GitHub App IDs may be repository variables; private keys belong only in Secrets.
4. Follow recent repository commit language and use the established conventional prefix where applicable: `fix:`, `feat:`, `docs:`, `refactor:`, or `ci:`.
5. A persisted task config-key rename must also load `ok-config-migration` and follow its strict sequence.

## PowerShell Markdown safety

PowerShell backticks are escapes inside double-quoted strings. Passing Markdown containing code backticks through `"..."` can remove backticks or introduce control characters such as BEL (`^G`).

- Store Markdown bodies in a single-quoted string or a single-quoted here-string (`@' ... '@`).
- Pass the variable to `gh pr create/edit --body $body`; never place Markdown with backticks directly in a double-quoted `--body` value.
- After creating or editing a PR, fetch the remote body and compare it byte-for-byte or text-for-text with the intended local body. A substring search for backticks is insufficient.

Recommended verification:

```powershell
$body = @'
Markdown containing `code`.
'@
gh pr create --base master --head $branch --title $title --body $body
$remoteBase64 = gh pr view $branch --json body --jq '.body | @base64'
$normalizedBody = $body.Replace("`r`n", "`n")
$localBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedBody))
if ($remoteBase64 -cne $localBase64) {
    throw "Remote PR body differs from the intended body"
}
```

## Broad automated edits

Textual batch tools can preserve Python syntax while deleting or duplicating executable statements. For broad docstring, annotation, formatting, or source-rewrite operations:

1. Establish a clean baseline or record pre-existing user changes before running the tool.
2. Parse every changed Python file.
3. Compare each function by qualified name (`Class.method`, not bare method name) against the baseline and flag reductions in non-docstring executable statements.
4. Review every flagged reduction; intentional code removal is allowed only when explained by the requested change.
5. Run focused tests and inspect `git diff --check` plus the full diff before committing.

`ast.parse` may reject a UTF-8 BOM when fed decoded text; read candidate source with `utf-8-sig` for structural comparison.

## Completion report

Report changed areas, verification performed, known pre-existing failures, and whether commit/PR/push operations were performed. Never describe a test or push as successful unless its command completed successfully.