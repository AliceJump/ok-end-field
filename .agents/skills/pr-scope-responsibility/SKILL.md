---
name: pr-scope-responsibility
description: Review or plan pull requests for responsibility boundaries, scope consistency, and minimal split points. Use when deciding whether a PR mixes independent reasons to change, whether diagnostics should be separated from core logic, or whether the advertised PR scope matches the actual diff.
---

# PR Responsibility And Scope Review

## Core rule

Judge responsibility boundaries by **independent reasons to change**, not by file length, function count, or diff size alone.

A large module may still have one coherent responsibility. A small module may already mix unrelated responsibilities. Do not recommend splitting unless there are at least two independently changing concerns and a concrete maintenance, testing, runtime, or release risk caused by keeping them coupled.

## What to inspect

1. Identify the PR's stated goal from its title and description.
2. Classify the actual diff into behavior-changing concerns, diagnostics/observability, configuration, persistence/formatting, concurrency/runtime infrastructure, tests, and documentation as applicable.
3. For each concern, state its independent reason to change. Treat concerns as separate responsibilities only when they can reasonably evolve, fail, be reviewed, or be released independently.
4. Check whether the diff changes behavior that the PR description does not disclose. Changes to thresholds, candidate selection, scheduling, state transitions, persistence semantics, or public contracts are behavior changes even when introduced alongside diagnostics.
5. Prefer the smallest useful boundary. Do not demand new classes, interfaces, design patterns, or files merely to satisfy an abstract interpretation of SRP.

## Core logic versus diagnostics

Core business logic may produce structured diagnostic evidence and call a diagnostic boundary, but should not also own independently evolving diagnostic mechanisms such as:

- live overlays;
- image or trace annotation;
- diagnostic file formats;
- file persistence and retention;
- background writers, queues, or worker lifecycle;
- diagnostic-only failure handling.

Ordinary configuration registration and thin orchestration are not, by themselves, responsibility violations.

Diagnostics must not change the core decision result or state progression. Diagnostic failures must fail soft unless the feature explicitly defines otherwise. On hot paths, inspect synchronous work such as copying, annotation, serialization, and queue submission; do not equate asynchronous disk I/O with fully asynchronous diagnostics.

## PR scope consistency

Compare the advertised scope with the actual diff.

- If a PR says it only adds logs, debugging, or visualization but also changes thresholds, filtering, scheduling, state semantics, persistence, or contracts, flag the mismatch.
- If multiple modules change for one clearly stated objective and the description discloses the behavior changes with separate tests, do not require a split merely because several files or modules are involved.
- Prefer splitting when independent concerns have meaningfully different risk, validation, rollout, or review paths. A prerequisite/follow-up stack is often better than a broad refactor.

## Evidence required before claiming mixed responsibilities

When reporting a responsibility-boundary problem, include all of the following:

1. the concrete functions or modules involved;
2. at least two independent reasons to change;
3. the actual maintenance, testing, runtime, or release risk caused by the coupling;
4. the minimum viable split or boundary;
5. whether the issue is newly introduced or materially worsened by the current PR.

Do not require the PR to clean up unrelated historical coupling.

## Recommended dispositions

Use one of these outcomes instead of a generic "violates SRP" label:

- **Keep together** — one objective, coupled validation, no meaningful independent change reason.
- **Clarify scope** — implementation is coherent, but title/description understate behavior changes.
- **Extract a boundary** — one PR can remain, but implementation should move an independently changing concern behind a module/function boundary.
- **Split prerequisite/follow-up** — concerns have distinct behavior/risk and should be independently reviewed or merged.
- **Defer historical cleanup** — coupling predates the PR and is not materially worsened here.

When CodeRabbit is involved, also read `../ok-script-pr-review/SKILL.md` for triggering, waiting, replying, and resolving review threads.
