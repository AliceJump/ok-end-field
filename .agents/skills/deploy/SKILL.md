---
name: deploy
description: Commit validated changes, calculate an annotated stable/beta/alpha version tag, and push the release to the publishing remote. Use when asked to deploy, release, or create/push a version tag.
---

# Deploy

Load `$repository-workflow` and `$use-local-venv`. A local-only request stops after the local commit/tag.

## Branch gate

Remote `master` accepts code only through PRs. On `master`, fetch `origin` and require `HEAD == origin/master` before tagging; do not commit or push branch changes there. If intended code changes remain, land them through a PR first. When no commit is needed, tag the current merged HEAD. On other branches, commit only the validated intended files.

## Version

Use `.agents/skills/deploy/scripts/next_tag.py` with `--remote origin`; stop if remote tag lookup or the locked Python environment fails. `release` increments the latest stable patch. `beta` and `alpha` each continue their own suffix on an unreleased next patch; a stable release closes that prerelease base. Never reuse, move, or delete a tag.

```powershell
uv run --locked python .agents/skills/deploy/scripts/next_tag.py release --remote origin
# substitute beta or alpha for a prerelease
```

## Release steps

1. Inspect `git status --short --branch`, relevant/staged diffs, and the latest non-merge subject (`git log --no-merges -1 --format=%s`). Run focused verification; stop on failure unless the user explicitly accepts it. Use the subject's natural language and a concise repository-style prefix for a new commit.
2. Calculate the tag before committing. Stage only intended files; inspect `git diff --cached --stat` and `git diff --cached`. Create one nonempty commit when needed.
3. Create an annotated tag: `git tag -a "<tag>" -m "<tag>"`. Confirm it points to the intended commit with `git show --no-patch --decorate HEAD`.
4. Push to the publishing remote unless local-only. On `master`, push **only the tag**: `git push origin "<tag>"`. On another branch: `git push origin HEAD "<tag>"`. Report commit subject, tag, remote, and actual push result; a local tag or successful push does not prove CI publishing succeeded.

Do not include unrelated working changes or credentials in the commit. If commit contents are ambiguous, clarify before staging.
