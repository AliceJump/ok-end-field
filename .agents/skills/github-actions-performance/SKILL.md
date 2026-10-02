---
name: github-actions-performance
description: Diagnose slow GitHub Actions jobs in ok-script repos, especially checkout history/tags and partial-sync-repo tag pruning. Use when attributing a sync job speed change or reducing repeated tag work.
---

# GitHub Actions Performance

## Verified ok-end-field sync case

| Change | Observed effect |
|---|---|
| `37593fa` added `fetch-depth: 0` and `fetch-tags: true` | ~9–11 min to ~3 min |
| `e1125be` upgraded `actions/checkout@v4` to `@v6` | ~3 min to ~1.4 min |

The old shallow checkout saw only tags reachable from its single commit. `ok-oldking/partial-sync-repo` compared that incomplete source set with target tags and deleted about 304 apparently “missing” tags at roughly 2 seconds each. The tags existed in the source repository; they were not orphans. Full history plus tags stopped that work. The later checkout upgrade sped up the full fetch.

## Diagnose another run

1. Get the job log and measure its first/last timestamps; old jobs API `startedAt` / `completedAt` can be null. Count `Deleting tag` lines to see whether pruning dominates.
2. Tie each compared run's `head_sha` to the exact workflow/action version and tag state. Separate a persistent code/version improvement from a one-time cleanup.
3. Check the action's behavior before changing options: it clones target repositories, syncs listed files, and then synchronizes tags. When no files change, its early `continue` skips tag synchronization for that target. It has no “push new tag but keep all old tags” option; that requires a fork or custom commands.

Pin `partial-sync-repo` to a reviewed full commit SHA rather than `@master` when modifying the workflow. Validate YAML and Actions structure with `$github-workflows`.
