---
name: use-local-venv
description: Prefer the repository-local Python virtual environment for coding-agent work. Use when running Python scripts, tests, linters, formatters, package installs, dependency checks, or any Python command in a repo that uses uv and a .venv; keep pyproject.toml plus uv.lock authoritative and avoid global Python drift.
---

# Use Local Venv

## Overview

Use the project `.venv` for Python commands so agent work uses the same dependencies as the repository instead of the global Python installation.

The `.venv` is created and synced by `uv sync` from `pyproject.toml` and `uv.lock`. In this repository, use `uv sync --locked` and `uv run --locked ...` so a verification command cannot silently rewrite the lockfile or resolve different dependency versions.

## Rule

- Run from the repository root unless the script explicitly supports another working directory.
- Prefer `uv run --locked python ...`; it selects the repository `.venv` without activation and verifies the lockfile.
- Use the direct `.venv` interpreter only for bootstrap diagnostics when `uv` is unavailable; it is not a valid formal test or lint entry point.
- Do not fall back to global Python in this repository. If `uv` or `.venv` is missing, run `uv sync --locked` or report the environment problem instead of executing with unknown dependencies.
- `pyproject.toml` and `uv.lock` are dependency sources of truth. `requirements.txt` is a publishing derivative and must not be hand-edited.

## PowerShell

Normal commands:

```powershell
uv sync --locked
uv run --locked python -m unittest tests.TestCheckLang -v
uv run --locked python -m py_compile path/to/script.py
```

Direct-interpreter bootstrap diagnostic after confirming it exists:

```powershell
& ".\.venv\Scripts\python.exe" --version
```

## POSIX Shells

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests
```

## Tests

- Full repository suite: `scripts/testing/run_tests.ps1` (which uses `uv run --locked`).
- Focused unittest: `uv run --locked python -m unittest tests.TestModule -v`.
- Do not use a direct `.venv` interpreter invocation as formal verification; all formal checks use a locked `uv` entry point.
- Preserve and report pre-existing independent failures; do not hide them by switching interpreters or dependencies.
