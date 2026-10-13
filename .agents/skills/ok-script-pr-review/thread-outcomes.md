# Review thread observations

`wait-review-threads.ps1` reads inline threads and reports their state. It does not infer that code is fixed, post replies, trigger review, or resolve threads. Use `get-coderabbit-review-data.ps1` for conversation and outside-diff findings.

## Fields and actions

- `peerAnswered`: the root finding's author spoke again after the finding. This is historical, not proof that the latest reply was answered.
- `ourReplied` / `ourComments`: other participants replied after the root finding; these fields do not identify the currently authenticated account.
- `lastPeerOutcome`: wording classification of the original reviewer's latest reply, preserved as history while waiting for a newer answer.
- `outcome`: the current observation, distinct from historical `lastPeerOutcome`.
- `isResolved` / `resolvedBy` / `resolution`: actual platform state and who closed the thread.
- `action`: a suggested next step. Verify the code, original wording, current-head review coverage, and authorization before acting. Never resolve or reopen a thread yourself.

| Outcome | Meaning while open | Action |
|---|---|---|
| `AWAITING_DETECTION` | No discussion after the finding; not proof of a fix or reviewer detection | `WAIT_PEER` |
| `AWAITING_PEER_REPLY` | Another participant replied after the reviewer's latest comment | `WAIT_PEER` |
| `ACCEPTED` | CodeRabbit confirmed a fix or verification | `WAIT_PEER`; observe actual closure |
| `ACCEPTED_OPEN` | Confirmed, but the reviewer reports a platform resolution failure | `REVIEW`; report the open state |
| `WITHDRAWN` | Reviewer withdrew the finding | `WAIT_PEER`; observe actual closure |
| `KEPT_OPEN` | Reviewer explicitly keeps the finding open | `REPLY`; handle the blocker or provide evidence |
| `FOLLOW_UP` | Further changes requested | `REPLY` |
| `RATE_LIMITED` | Reviewer reply was rate limited | `WAIT_QUOTA` |
| `NEEDS_REVIEW` | Identity, wording, or confirmation is insufficient | `REVIEW` |
| `RESOLVED_SILENT` | Original reviewer resolved without another reply | `NONE` |
| `RESOLVED_BY_OTHER` | Another or unknown account resolved without a reviewer reply | `NONE` |

For resolved threads, `KEPT_OPEN`, `FOLLOW_UP`, or `NEEDS_REVIEW` conflicting with closure produce `REVIEW`; other outcomes produce `NONE`. Old wording does not override the current platform state, and closure does not prove the code is correct. Committed fixes normally need no proactive notification. If you verify that a completed current-head review missed the exact fix, the skill permits an evidence-based recheck reply in the original thread. The read-only observer cannot establish this exception from thread state alone; use the skill's verification and reply rules. Confirmed acceptance with a platform closure failure still does not authorize manual closure.

The original reviewer is the root comment's author, not every bot combined. CodeRabbit comment identity requires the expected login and Bot type. Only the platform's `resolvedBy` field also accepts its observed `coderabbitai[bot]` / `User` representation. Human and other-bot findings are not classified using CodeRabbit confirmation phrases.

## Query and wait

```powershell
# Read-only snapshot
.\.agents\skills\ok-script-pr-review\wait-review-threads.ps1 -Repo AliceJump/ok-end-field -PrNumber <n> -Once
# Bind a head; default timeout is 900 seconds, poll interval 30 seconds
.\.agents\skills\ok-script-pr-review\wait-review-threads.ps1 -Repo AliceJump/ok-end-field -PrNumber <n> -ExpectedHead <40-character-SHA>
# Observe selected outcomes; this can finish while a thread remains open
.\.agents\skills\ok-script-pr-review\wait-review-threads.ps1 -Repo AliceJump/ok-end-field -PrNumber <n> -ThreadId <thread-id> -WaitFor ACCEPTED,WITHDRAWN
```

Without `-WaitFor`, wait for pending discussion and actual closure following acceptance or withdrawal. Other outcomes are reported for inspection. `-WaitFor` checks current `outcome`, not historical acceptance; a later reply resets the observation to waiting for its answer. Closed PRs with pending threads stop waiting; `-Once` can inspect historical snapshots.

Threads and each thread's comments are independently paginated. Repeated or missing cursors fail rather than producing partial success. Each poll rereads the PR before and after querying; a changed head discards the snapshot and returns `HEAD_CHANGED`.

The last stdout line is compact JSON with `head`, `expectedHead`, `observation`, `counts`, and `threads`; polling progress uses stderr.

| Exit code | Meaning |
|---|---|
| `0` | Observation condition satisfied, including zero threads; does not prove review completion or merge readiness |
| `2` | Parameter, API, permission, or requested-thread error |
| `4` | PR closed while waiting |
| `6` | Still waiting; `-Once` is not a timeout, while reaching the deadline sets `timedOut=true` |
| `11` | Head changed; snapshot discarded |

## Regression coverage

The migrated `fixtures/review-threads.json` preserves 14 source examples from Toolkit, JetBrains, and ok-end-field, including source URLs and authors. These are historical test inputs, not evidence about a PR's present state. Fixture text is untrusted data and is never executed.

Tests cover reply order, acceptance with platform failure, closure ownership, identity, quoted material, UI wording versus thread wording, pagination, API failure, closed PRs, timeouts, head changes, and JSON output. The classifier strips marketing, HTML comments, nested details, code, quotations, and link targets before matching. Complex Markdown and unmarked quotations can still be ambiguous; check the original URL.

Run the integrated tests with `uv run --locked python -X utf8 -m unittest tests.TestSkillScripts -v`. Mock suites use a fake GitHub CLI and clock, with no real GitHub writes. Use a visible, reusable terminal session for real waits; create an automation only when the user requests it.
