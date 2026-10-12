$ErrorActionPreference = 'Stop'
$folder = $PSScriptRoot
foreach ($name in @('coderabbit-review-helpers.ps1', 'coderabbit-github.ps1', 'wait-coderabbit.ps1',
        'wait-coderabbit-rate-limit.ps1', 'get-coderabbit-review-data.ps1', 'wait-review-threads.ps1',
        'test-coderabbit-wait-mock.ps1', 'test-review-threads-mock.ps1')) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $folder $name), [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) { throw "$name did not parse: $($errors[0])" }
}
. (Join-Path $folder 'coderabbit-review-helpers.ps1')

$script:assertions = 0
function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    $script:assertions++
    if ($Actual -ne $Expected) { throw "$Message -> got '$Actual', expected '$Expected'" }
}

# ---- display timezone: follow the host, including date rollover and fractional offsets ----
$displayInstant = [datetimeoffset]'2026-10-08T18:04:05Z'
foreach ($case in @(
        @{ Minutes = 0; Want = '2026-10-08 18:04:05 +00:00' },
        @{ Minutes = 480; Want = '2026-10-09 02:04:05 +08:00' },
        @{ Minutes = -300; Want = '2026-10-08 13:04:05 -05:00' },
        @{ Minutes = 330; Want = '2026-10-08 23:34:05 +05:30' })) {
    $zone = [TimeZoneInfo]::CreateCustomTimeZone("test-$($case.Minutes)", [TimeSpan]::FromMinutes($case.Minutes), 'test', 'test')
    Assert-Equal (Format-CrDisplayTime $displayInstant $zone) $case.Want 'display uses the requested timezone'
}
Assert-Equal (Format-CrDisplayTime $displayInstant) ($displayInstant.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss zzz', [Globalization.CultureInfo]::InvariantCulture)) 'default display follows host timezone'

# ---- quota replies: a countdown is unavailable; only an explicit positive reply is available ----
$plan = '<!-- This is an auto-generated reply by CodeRabbit -->' + "`n" +
    'Your [plan](https://docs.coderabbit.ai/management/plans#fair-usage-limits-policy) includes PR reviews subject to [rate limits](https://docs.coderabbit.ai/management/plans#rate-limits).'
$quotaCases = @(
    @{ Body = "$plan More reviews will be available in 1 minute."; Want = 'unavailable'; Retry = 60 },
    @{ Body = "$plan More reviews will be available in 3 minutes."; Want = 'unavailable'; Retry = 180 },
    @{ Body = "$plan Reviews are available now."; Want = 'available'; Retry = $null },
    @{ Body = 'No reviews are available now.'; Want = 'unavailable'; Retry = $null },
    @{ Body = 'Reviews are not available now.'; Want = 'unavailable'; Retry = $null },
    @{ Body = 'You have 2 PR reviews remaining.'; Want = 'available'; Retry = $null },
    @{ Body = 'Remaining PR reviews: 1'; Want = 'available'; Retry = $null },
    @{ Body = '0 reviews remaining. Next included review available in 1.2 minutes.'; Want = 'unavailable'; Retry = 72 },
    @{ Body = 'Please wait **12 minutes and 34 seconds** before requesting another review.'; Want = 'unavailable'; Retry = 754 },
    @{ Body = 'More reviews will be available in 1 hour.'; Want = 'unavailable'; Retry = 3600 },
    @{ Body = 'Review rate limited'; Want = 'unavailable'; Retry = $null },
    @{ Body = $plan; Want = 'unknown'; Retry = $null },
    @{ Body = 'The current review has 2 comments.'; Want = 'unknown'; Retry = $null },
    @{ Body = 'Reviews remaining: maybe later.'; Want = 'unknown'; Retry = $null }
)
foreach ($case in $quotaCases) {
    Assert-Equal (Get-CodeRabbitQuotaState $case.Body) $case.Want "quota state of '$($case.Body)'"
    Assert-Equal (Get-CodeRabbitRetrySeconds $case.Body) $case.Retry "retry seconds of '$($case.Body)'"
}

# ---- review commands must be the whole comment ----
foreach ($pair in @(
        @('@coderabbitai review', $true), @('  @CodeRabbitAI  full review ', $true), @('@coderabbitai review please', $false),
        @('@coderabbitai rate limit', $false), @('please @coderabbitai review', $false), @("@coderabbitai full review`n", $true))) {
    Assert-Equal (Test-CodeRabbitReviewCommand $pair[0]) $pair[1] "review command '$($pair[0])'"
}

# ---- fixtures ----
$head = 'a' * 40
$older = 'b' * 40
$t0 = [datetimeoffset]'2026-01-01T00:00:00Z'
function At([int]$Seconds) { return $t0.AddSeconds($Seconds) }
function Status([long]$Id, [string]$State, [string]$Description, [int]$At, [string]$Login = 'coderabbitai[bot]', [string]$Type = 'Bot') {
    [pscustomobject]@{ id = $Id; context = 'CodeRabbit'; state = $State; description = $Description; login = $Login; type = $Type; created = (At $At) }
}
function Review([long]$Id, [string]$Commit, [string]$Body, [int]$At, [string]$State = 'COMMENTED', [string]$Type = 'Bot') {
    [pscustomobject]@{ id = $Id; login = 'coderabbitai[bot]'; type = $Type; commit_id = $Commit; state = $State; body = $Body; created = (At $At) }
}
function Comment([long]$Id, [string]$Body, [int]$At, [string]$Login = 'alice', [string]$Type = 'User') {
    [pscustomobject]@{ id = $Id; login = $Login; type = $Type; body = $Body; created = (At $At); updated = (At $At) }
}
$marker = '<!-- This is an auto-generated comment by CodeRabbit for review status -->'
$summaryBody = "**Actionable comments posted: 2**`nReviewing files that changed from the base of the PR and between $older and $head.`n$marker"
$outsideOnlyBody = "> Outside diff range comments (1)`nReviewing files that changed from the base of the PR and between $older and $head.`n$marker"
function Snap([object[]]$Statuses = @(), [object[]]$Reviews = @(), [object[]]$Comments = @(), [object]$Ledger = $null,
    [string]$State = 'open', [bool]$Draft = $false, [int]$Arrival = 0) {
    [pscustomobject]@{ head = $head; prState = $State; draft = $Draft; statuses = $Statuses; reviews = $Reviews; comments = $Comments
        headArrivedAt = (At $Arrival); ledger = $Ledger }
}
function Assert-State([object]$Snapshot, [string]$Want, [string]$Message, [string]$Evidence = '') {
    $actual = Get-CodeRabbitHeadState $Snapshot
    Assert-Equal $actual.state $Want $Message
    if ($Evidence) { Assert-Equal $actual.evidence $Evidence "$Message (evidence)" }
}

# ---- coverage proof ----
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head $summaryBody 10) $head) $true 'summary review covers head'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head $outsideOnlyBody 10) $head) $true 'outside-diff-only summary covers head'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head '' 10) $head) $false 'thread-reply review with empty body is not a review of the head'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $older $summaryBody 10) $head) $false 'review of an older commit'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head ($summaryBody -replace "and $head", "and $older") 10) $head) $false 'reviewed range ending elsewhere'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head $summaryBody 10 'PENDING') $head) $false 'pending review'
Assert-Equal (Test-CodeRabbitReviewCoversHead (Review 1 $head $summaryBody 10 'COMMENTED' 'User') $head) $false 'non-bot author with the bot login'

# ---- per-head state machine ----
$skipped = Status 1 'success' 'Review skipped: manual review required for this OSS repository' 5
$pending = Status 2 'pending' 'Review in progress' 40
$completed = Status 3 'success' 'Review completed' 100
$limited = Status 4 'success' 'Review rate limited' 5
Assert-State (Snap) 'AWAITING_AUTO' 'no status yet'
Assert-State (Snap -Statuses @($skipped)) 'MANUAL_REQUIRED' 'manual review required'
Assert-State (Snap -Statuses @($limited)) 'RATE_LIMITED' 'rate limited'
Assert-State (Snap -Statuses @((Status 1 'success' 'Review skipped: ignored title keyword' 5))) 'SKIPPED' 'skipped for another reason'
Assert-State (Snap -Statuses @((Status 1 'success' 'Review skipped: draft pull request' 5))) 'DRAFT' 'skipped draft'
Assert-State (Snap -Statuses @((Status 1 'error' 'Review failed' 5))) 'REVIEW_FAILED' 'failed status'
Assert-State (Snap -Draft $true -Statuses @($skipped)) 'DRAFT' 'draft PR'
Assert-State (Snap -State 'closed') 'CLOSED' 'closed PR'
Assert-State (Snap -Statuses @($skipped, $pending)) 'IN_PROGRESS' 'pending after skip'
Assert-State (Snap -Statuses @((Status 6 'success' 'Review skipped: manual review required' 60), $pending)) 'MANUAL_REQUIRED' 'status order follows creation time, not list order'
Assert-State (Snap -Statuses @($skipped, $pending, $completed) -Reviews @((Review 9 $head $summaryBody 95))) 'REVIEWED' 'completed with covering review' 'review:9'
Assert-State (Snap -Statuses @($skipped) -Reviews @((Review 9 $head '' 30))) 'MANUAL_REQUIRED' 'thread replies on the head leave it unreviewed'
Assert-State (Snap -Statuses @($completed) -Reviews @((Review 9 $head '' 30))) 'COMPLETED_UNCONFIRMED' 'completed status alone is only a signal'
Assert-State (Snap -Statuses @($completed) -Reviews @((Review 9 $older $summaryBody 30))) 'COMPLETED_UNCONFIRMED' 'older review does not cover head'
$coverage = "<!-- final_review_risk_coverage:{`"sourceCommitId`":`"$head`",`"coveredCommitId`":`"$head`",`"kind`":`"reviewed`"} -->"
$walkthrough = Comment 7 "walkthrough $coverage" 1 'coderabbitai[bot]' 'Bot'
Assert-State (Snap -Statuses @($completed) -Comments @($walkthrough)) 'REVIEWED' 'summary coverage plus completed status' 'summary:7'
Assert-State (Snap -Statuses @($skipped) -Comments @($walkthrough)) 'MANUAL_REQUIRED' 'summary coverage needs a completed status'
Assert-State (Snap -Statuses @($completed) -Comments @((Comment 7 ($walkthrough.body -replace "coveredCommitId`":`"$head", "coveredCommitId`":`"$older") 1 'coderabbitai[bot]' 'Bot'))) 'COMPLETED_UNCONFIRMED' 'summary coverage of another head'
Assert-State (Snap -Statuses @($completed) -Comments @((Comment 7 $walkthrough.body 1 'mallory' 'User'))) 'COMPLETED_UNCONFIRMED' 'summary marker from a human'
Assert-State (Snap -Statuses @((Status 3 'success' 'Review completed' 100), (Status 5 'success' 'Review rate limited' 200)) -Reviews @((Review 9 $head $summaryBody 95))) 'REVIEWED' 'a covered head stays covered after a later limit'
Assert-State (Snap -Statuses @($completed, (Status 5 'pending' 'Review in progress' 200)) -Reviews @((Review 9 $head $summaryBody 95))) 'IN_PROGRESS' 'a new review round is awaited'
Assert-State (Snap -Statuses @((Status 1 'success' 'Review completed' 5 'mallory' 'User'))) 'AWAITING_AUTO' 'status from a non-bot creator is ignored'

$trigger = Comment 20 '@coderabbitai review' 30
Assert-State (Snap -Statuses @($skipped) -Comments @($trigger)) 'TRIGGERED' 'trigger newer than the stale skip status' 'comment:20'
Assert-State (Snap -Statuses @($skipped) -Comments @((Comment 20 '@coderabbitai review' 30 'coderabbitai[bot]' 'Bot'))) 'MANUAL_REQUIRED' 'bot text is not a trigger'
Assert-State (Snap -Statuses @($skipped) -Comments @((Comment 20 '@coderabbitai review' 30)) -Arrival 60) 'MANUAL_REQUIRED' 'a trigger before the head arrived belongs to an older head'
$ack = Comment 21 "<!-- CodeRabbit review command invocation: v2:x -->`n<details><summary>Action performed</summary>Review triggered.</details>" 35 'coderabbitai[bot]' 'Bot'
Assert-State (Snap -Statuses @($skipped) -Comments @($trigger, $ack)) 'TRIGGERED' 'acknowledged trigger'
$finishedAck = Comment 21 ($ack.body -replace 'Review triggered', 'Review finished') 35 'coderabbitai[bot]' 'Bot'
Assert-State (Snap -Statuses @($skipped, $completed) -Comments @($trigger, $finishedAck)) 'COMPLETED_UNCONFIRMED' 'an edited "finished" ack is not a review'
Assert-State (Snap -Statuses @($skipped, $completed) -Comments @($trigger, $finishedAck) -Reviews @((Review 9 $head $summaryBody 95))) 'REVIEWED' 'triggered review landed'
Assert-State (Snap -Statuses @($skipped, (Status 5 'success' 'Review rate limited' 50)) -Comments @($trigger)) 'TRIGGER_RATE_LIMITED' 'trigger answered by a limit'
Assert-State (Snap -Statuses @($skipped) -Comments @($trigger, (Comment 22 'More reviews will be available in 5 minutes.' 40 'coderabbitai[bot]' 'Bot'))) 'TRIGGERED' 'an unrelated reply is not an ack'
$ledger = [pscustomobject]@{ head = $head; recordedAt = (At 30).ToString('o'); path = 'ledger.json' }
Assert-State (Snap -Statuses @($skipped) -Ledger $ledger) 'TRIGGERED' 'local ledger records an earlier trigger' 'ledger:ledger.json'
Assert-State (Snap -Statuses @($skipped) -Ledger ([pscustomobject]@{ head = $older; recordedAt = (At 30).ToString('o'); path = 'x' })) 'MANUAL_REQUIRED' 'another head ledger is ignored'

Assert-Equal (Get-CodeRabbitHeadArrival @((At 50), (At 20), $null) (At 0)) (At 20) 'arrival uses the earliest observed time'
Assert-Equal (Get-CodeRabbitHeadArrival @() (At 3)) (At 3) 'arrival falls back to the commit time'
Assert-Equal ((ConvertTo-CrTime ([datetime]::SpecifyKind([datetime]'2026-01-01T00:00:00', 'Utc'))) -eq $t0) $true 'DateTime from PowerShell 7 JSON'

# ---- review thread outcomes: how the peer answered our reply ----
function TComment([string]$Login, [string]$Type, [string]$Body, [int]$At) {
    [pscustomobject]@{ login = $Login; type = $Type; body = $Body; created = (At $At) }
}
function TOutcome([object[]]$Comments, [bool]$Resolved = $false, [string]$By = '') {
    return (Get-CrThreadOutcome -Comments $Comments -IsResolved $Resolved -ResolvedBy $By -ResolvedByType 'User').outcome
}
$crBot = 'coderabbitai[bot]'
$finding = TComment $crBot 'Bot' '_🎯 Functional Correctness_ | _🟠 Major_ | _⚡ Quick win_' 1
$ourReply = TComment 'AliceJump' 'User' '@coderabbitai 采纳并修复，提交 abc1234。测试：npm test 通过。' 2
function Peer([string]$Body) { return (TComment $crBot 'Bot' $Body 3) }

Assert-Equal (TOutcome @($finding)) 'AWAITING_DETECTION' 'a fix pushed without a reply waits for the peer to look'
Assert-Equal (TOutcome @($finding, $ourReply)) 'AWAITING_PEER_REPLY' 'we replied, the peer has not answered'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，感谢修复。`save()` 现在仅对 EPERM 重试 3 次。测试和 CI 结果以你报告的结果为准。'))) 'ACCEPTED' 'the peer confirmed the fix'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，已核对当前代码。此问题已修复。'))) 'ACCEPTED' 'the peer verified the current code'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，你说得对。我撤回这条意见。'))) 'WITHDRAWN' 'the peer withdrew the finding'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，收到。子仓 PR `#11` 尚未合并，先保持此线程开放。'))) 'KEPT_OPEN' 'the peer keeps it open'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，`stableRect` 修复了原先的问题。但 `publishStatus` 尚未比较实际序列化的矩形。请让状态比较序列化后的矩形。'))) 'FOLLOW_UP' 'the peer accepted part of it and asked for more'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '### Rate Limit Exceeded'))) 'RATE_LIMITED' 'the peer was rate limited'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，我会把这次修改交给编码工作流。'))) 'NEEDS_REVIEW' 'unrecognized wording stays for a human'

# Quoted material is not the peer's answer. CodeRabbit wraps the finding, the diff and a
# "Prompt for AI Agents" block in <details>, and quotes code in fences, spans and blockquotes.
# This skill's own scripts contain the classifier phrases verbatim, so a quote must never be
# read as an answer: only the visible prose may be classified.
$qDetails = @'
`@AliceJump`，感谢修复。修复已确认，测试与 CI 结果以你报告为准。

<details><summary>Prompt for AI Agents</summary>

```powershell
if ($text -match '撤回') { return 'WITHDRAWN' }
if ($text -match '保持[^。]{0,10}开放') { return 'KEPT_OPEN' }
```

</details>
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qDetails))) 'ACCEPTED' 'a details block quoting the classifier source is not a withdrawal'

$qDetailsDemand = @'
`@AliceJump`，感谢修复。当前代码已按建议修正。

<details><summary>Prompt for AI Agents</summary>

请在合并前确认 CI 结果。

</details>
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qDetailsDemand))) 'ACCEPTED' 'a demand inside the details block is not a follow-up'

$qFence = @'
`@AliceJump`，已核对当前代码，此问题已修复。

```text
// 上一轮的结论：先保持此线程开放
```
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qFence))) 'ACCEPTED' 'a fenced block quoting keep-open wording is not a kept-open thread'

$qSpan = @'
`@AliceJump`，感谢修复。`Get-CrThreadPeerOutcome` 里的 `if ($text -match '撤回')` 已按顺序判定。
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qSpan))) 'ACCEPTED' 'inline code quoting the phrase is not a withdrawal'

$qBlock = @'
`@AliceJump`，感谢修复。

> 我撤回这条意见。

修复已确认。
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qBlock))) 'ACCEPTED' 'a blockquote of an earlier comment is not the peer answer'

# The visible prose still decides, so stripping quoted material is not a blanket mute.
$qMixed = @'
`@AliceJump`，此线程暂时保持开放。

> 感谢修复。
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $qMixed))) 'KEPT_OPEN' 'visible prose decides even when a quote says otherwise'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，你说得对。我撤回这条意见。'))) 'WITHDRAWN' 'a withdrawal in visible prose still counts'

# Destructive controls: every rule above must lose to the more specific one.
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，感谢澄清。我撤回该评论。'))) 'WITHDRAWN' 'a withdrawal beats the thank-you in the same body'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，你说得对，这部分已确认。同一条评论中的发布问题仍然存在，因此这条评论暂时保持开放。'))) 'KEPT_OPEN' 'kept open beats a partial confirmation'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，已核对 `1435c58`。原先"图片已删除但框记录仍保留"的问题不再存在。'))) 'ACCEPTED' 'a stray 但/仍 inside prose is not a follow-up request'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding, $ourReply)).peerAnswered $false 'the original finding is not an answer to our reply'
Assert-Equal (TOutcome @($finding, (TComment 'AliceJump' 'User' '@coderabbitai 感谢修复。' 2))) 'AWAITING_PEER_REPLY' 'our own words are never the peer answer'
Assert-Equal (TOutcome @($finding, $ourReply, (TComment 'chatgpt-codex-connector' 'Bot' 'P2 Badge Mark the divider as a native Quit' 3))) 'NEEDS_REVIEW' 'another reviewer bot is not read as CodeRabbit'
Assert-Equal (TOutcome @($finding, $ourReply, (TComment 'mallory' 'User' '@coderabbitai 已核对当前代码，此问题已修复。' 3))) 'AWAITING_PEER_REPLY' 'a human reply is not the peer answer'

# Resolution is reported next to the flag, never instead of it.
$silent = Get-CrThreadOutcome -Comments @($finding, $ourReply) -IsResolved $true -ResolvedBy $crBot -ResolvedByType 'Bot'
Assert-Equal $silent.outcome 'RESOLVED_SILENT' 'resolved with no reply after ours'
Assert-Equal $silent.resolution 'PEER' 'resolution names the peer'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding, $ourReply) -IsResolved $true -ResolvedBy $crBot -ResolvedByType 'User').outcome 'RESOLVED_SILENT' 'the live resolvedBy shape resolves to the peer'
$byUs = Get-CrThreadOutcome -Comments @($finding, $ourReply) -IsResolved $true -ResolvedBy 'AliceJump' -ResolvedByType 'User'
Assert-Equal $byUs.outcome 'RESOLVED_BY_OTHER' 'resolved by a human'
Assert-Equal $byUs.resolution 'OTHER' 'resolution names us'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding, $ourReply) -IsResolved $true -ResolvedBy 'AliceJump').outcome 'RESOLVED_BY_OTHER' 'a resolver without a type is not assumed to be a bot'
$kept = Get-CrThreadOutcome -Comments @($finding, $ourReply, (Peer '`@AliceJump`，先保持此线程开放。')) -IsResolved $true -ResolvedBy $crBot -ResolvedByType 'Bot'
Assert-Equal $kept.outcome 'KEPT_OPEN' 'the peer wording outranks the resolution state'
Assert-Equal $kept.isResolved $true 'the resolution state is still reported'
$counts = Get-CrThreadOutcome -Comments @($finding, $ourReply, (Peer 'thanks for the fix'))
Assert-Equal $counts.ourComments 1 'one comment from us'
Assert-Equal $counts.peerComments 2 'two comments from bots'
Assert-Equal $counts.totalComments 3 'three comments in total'
Assert-Equal (Test-CrThreadOutcomePending 'AWAITING_PEER_REPLY') $true 'a pending flag is pending'
Assert-Equal (Test-CrThreadOutcomePending 'AWAITING_DETECTION') $true 'waiting for detection is pending'
Assert-Equal (Test-CrThreadOutcomePending 'ACCEPTED') $false 'a terminal flag is not pending'
Assert-Equal (Test-CrThreadOutcomePending 'KEPT_OPEN') $false 'a kept-open thread does not move on its own'

# ---- the review flow: an accepted fix is pushed, not announced ----
# Accepted and fixed means push and stay quiet: the peer finds it on the next round.
Assert-Equal (TOutcome @($finding)) 'AWAITING_DETECTION' 'open with no reply from either side'
Assert-Equal (TOutcome @($finding) -Resolved $true -By $crBot) 'RESOLVED_SILENT' 'the peer resolved it without any reply from us'
Assert-Equal (TOutcome @($finding) -Resolved $true -By 'AliceJump') 'RESOLVED_BY_OTHER' 'a human resolved it without any reply'
Assert-Equal (TOutcome @($finding, $ourReply)) 'AWAITING_PEER_REPLY' 'only an explicit reply starts a wait for the peer'
Assert-Equal (TOutcome @($finding, (Peer '`@AliceJump`，感谢修复。已按建议修正。'))) 'ACCEPTED' 'the peer answered its own finding after a push'

# A resolve failure reads like "remains open" but means the fix was confirmed.
$platformFail = @'
`@AliceJump`，已确认。规则 6 现在仅要求代码提交通过 PR。 🐇 ✅ Thanks for confirming the fix. I couldn't resolve this review thread on the repository platform, so it remains open. Please retry or resolve it manually.
'@
Assert-Equal (TOutcome @($finding, $ourReply, (Peer $platformFail))) 'ACCEPTED_OPEN' 'a resolve failure is an accepted fix, not a kept-open thread'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding, $ourReply, (Peer $platformFail))).action 'REVIEW' 'a platform failure requires inspection, never manual resolution'
foreach ($unverifiedReply in @(
    "已确认需求，但未验证修复。I couldn't resolve this review thread.",
    "已确认需求，但没有核对修复。I couldn't resolve this review thread.",
    "Thanks for confirming the fix, but I cannot confirm it is verified. I couldn't resolve this review thread."
)) {
    Assert-Equal (TOutcome @($finding, $ourReply, (Peer $unverifiedReply))) 'NEEDS_REVIEW' 'unverified fixes are not accepted even when resolution fails'
    Assert-Equal (Get-CrThreadAction (TOutcome @($finding, $ourReply, (Peer $unverifiedReply))) $false) 'REVIEW' 'unverified fixes never suggest manual resolution'
}

# Phrasings seen in the sibling repository.
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，了解。该风险仍存在，因此此线程保持打开。'))) 'KEPT_OPEN' '保持打开 counts as kept open'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，修复尚未提交或验证，此线程保持未解决。'))) 'KEPT_OPEN' '保持未解决 counts as kept open'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，明白。新增条目不需要限制为六种语言节点。此评论不适用。'))) 'WITHDRAWN' '不适用 counts as a withdrawal'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '`@AliceJump`，确认。现在会根据返回值分别记录成功和失败。'))) 'ACCEPTED' 'a bare confirmation is acceptance'
Assert-Equal (TOutcome @($finding, $ourReply, (Peer '✅ Fixed in [#405](https://example.invalid).'))) 'ACCEPTED' 'a fixed-in link is acceptance'

# ---- action: what each flag asks of us ----
foreach ($pair in @(
        @('RESOLVED_SILENT', 'NONE'), @('ACCEPTED', 'WAIT_PEER'), @('WITHDRAWN', 'WAIT_PEER'), @('RESOLVED_BY_OTHER', 'NONE'),
        @('ACCEPTED_OPEN', 'REVIEW'),
        @('AWAITING_PEER_REPLY', 'WAIT_PEER'), @('RATE_LIMITED', 'WAIT_QUOTA'),
        @('KEPT_OPEN', 'REPLY'), @('FOLLOW_UP', 'REPLY'),
        @('NEEDS_REVIEW', 'REVIEW'),
        @('AWAITING_DETECTION', 'WAIT_PEER'))) {
    Assert-Equal (Get-CrThreadAction $pair[0]) $pair[1] "action of $($pair[0])"
}
Assert-Equal (Get-CrThreadAction 'SOMETHING_NEW') 'REVIEW' 'an unknown flag is never treated as done'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding)).action 'WAIT_PEER' 'no peer update waits for detection without suggesting a fix notification'
Assert-Equal (Get-CrThreadOutcome -Comments @($finding, $ourReply) -IsResolved $true -ResolvedBy $crBot).action 'NONE' 'a resolved thread asks for nothing'

# The finding author is the peer, not a collective of all bots or all human reviewers.
Assert-Equal (Test-CrThreadPeer (TComment 'coderabbitai' 'Bot' '' 2) $finding) $true 'GraphQL and REST CodeRabbit authors match'
Assert-Equal (Test-CrThreadPeer (TComment 'coderabbitai' 'User' '' 2) $finding) $false 'a human account cannot impersonate CodeRabbit'
Assert-Equal (Test-CrThreadPeer (TComment 'coderabbitai[bot]' 'User' '' 2) $finding) $false 'comment authors still require Bot'
Assert-Equal (Test-CrThreadPeer (TComment 'coderabbitai[bot]' 'User' '' 2) $finding -Resolver) $true 'resolvedBy uses the alternate platform shape'
Assert-Equal (Test-CrThreadPeer (TComment 'mallory[bot]' 'User' '' 2) $finding -Resolver) $false 'another suffix is not the reviewer'
$humanFinding = TComment 'reviewer' 'User' 'Please inspect this race.' 1
Assert-Equal (TOutcome @($humanFinding)) 'NEEDS_REVIEW' 'human findings require reading, not presumed fixed'
Assert-Equal (TOutcome @($humanFinding, $ourReply)) 'AWAITING_PEER_REPLY' 'the initial human finding is not our reply'
Assert-Equal (TOutcome @($humanFinding, $ourReply, (TComment 'reviewer' 'User' 'Thanks, fixed.' 3))) 'NEEDS_REVIEW' 'human answers are not parsed as bot confirmation'
Assert-Equal (Get-CrThreadOutcome @($humanFinding) $true 'reviewer' 'User').resolution 'PEER' 'a human reviewer can resolve their own finding'
Assert-Equal (Get-CrThreadOutcome @($finding) $true '' '').resolution 'UNKNOWN' 'missing resolver is not called us'

# Historical peer speech cannot stand in for an answer to a more recent response.
$again = TComment 'AliceJump' 'User' 'Here is a new commit. Please check again.' 4
$newRound = Get-CrThreadOutcome @($finding, $ourReply, (Peer '感谢修复。'), $again)
Assert-Equal $newRound.outcome 'AWAITING_PEER_REPLY' 'a new response waits for a new peer answer'
Assert-Equal $newRound.peerAnswered $true 'historical speech remains visible separately'
Assert-Equal $newRound.lastPeerOutcome 'ACCEPTED' 'the previous acknowledgement is retained as history'

# Current resolution controls whether a write/wait action is still meaningful.
Assert-Equal (Get-CrThreadOutcome @($finding, $ourReply, (Peer $platformFail)) $true $crBot 'User').action 'NONE' 'already resolved platform failures never ask for manual resolution'
Assert-Equal (Get-CrThreadOutcome @($finding, $ourReply, (Peer '此线程保持开放。')) $true $crBot 'User').action 'REVIEW' 'old keep-open wording requires checking, not another reply'
Assert-Equal (Get-CrThreadOutcome @($finding, $ourReply, (Peer 'Rate Limit Exceeded')) $true 'AliceJump' 'User').action 'NONE' 'resolved rate limits do not wait for quota'
Assert-Equal (Get-CrThreadOutcome @($finding, $ourReply, (Peer '感谢修复。'))).action 'WAIT_PEER' 'an acknowledgement does not resolve an open thread'
Assert-Equal (Get-CrThreadOutcome @($finding, $ourReply, (Peer '我撤回这条意见。'))).action 'WAIT_PEER' 'withdrawal still awaits the platform state'
Assert-Equal (Get-CrThreadPeerOutcome "I couldn't resolve this review thread.") 'NEEDS_REVIEW' 'resolve failure alone is not confirmation'
Assert-Equal (Get-CrThreadPeerOutcome "感谢修复，但问题仍然存在。I couldn't resolve this review thread.") 'NEEDS_REVIEW' 'platform failure never authorizes resolving a remaining risk'

# Real false positives: a dialog is not a review thread; a future check is not verification.
Assert-Equal (Get-CrThreadPeerOutcome '已核对当前代码。保存失败时保持对话框打开。感谢修复。') 'ACCEPTED' 'JetBrains #13 describes dialog behavior'
Assert-Equal (Get-CrThreadPeerOutcome "I’ll leave this thread open until the fix is verified.") 'KEPT_OPEN' 'ok-end-field #405 still waits for a fix'
Assert-Equal (Get-CrThreadPeerOutcome "I’ll keep this finding open for that case.") 'KEPT_OPEN' 'toolkit #18 leaves the finding open'
Assert-Equal (Get-CrThreadPeerOutcome '已核对当前代码。问题尚未修复。') 'NEEDS_REVIEW' 'inspection alone is not acceptance'
Assert-Equal (Get-CrThreadPeerOutcome 'Thanks for the update. This is not fixed.') 'NEEDS_REVIEW' 'a thank-you does not overrule an explicit negative'
Assert-Equal (Get-CrThreadPeerOutcome 'Thanks for clarifying.') 'NEEDS_REVIEW' 'politeness alone is not acceptance'
Assert-Equal (Get-CrThreadPeerOutcome 'Check [evidence](https://example.invalid/verified).') 'NEEDS_REVIEW' 'URL text is not a claim'

$nestedQuote = @'
感谢修复。
<details open><summary>引用</summary><details><summary>内层</summary>撤回</details>此线程保持开放。</details>
'@
Assert-Equal (Get-CrThreadPeerOutcome $nestedQuote) 'ACCEPTED' 'nested and attributed details are fully excluded'
$longFence = @'
感谢修复。
````markdown
```text
撤回
```
此线程保持开放。
````
'@
Assert-Equal (Get-CrThreadPeerOutcome $longFence) 'ACCEPTED' 'long fences containing shorter fences are quotes'
Assert-Equal (Get-CrThreadPeerOutcome '感谢修复。 ``撤回 `old` 此线程保持开放``') 'ACCEPTED' 'multi-backtick inline code is a quote'
Assert-Equal (Get-CrThreadPeerOutcome "感谢修复。`n<details>此线程保持开放。") 'ACCEPTED' 'unclosed details cannot leak classifier phrases'

# Regression fixtures are captured raw replies, with source URLs and manually assessed outcomes.
$fixtures = Get-Content (Join-Path $folder 'fixtures/review-threads.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($case in $fixtures.cases) {
    $state = Get-CrThreadOutcome $case.comments ([bool]$case.isResolved) $case.resolvedBy.login $case.resolvedBy.__typename
    Assert-Equal $state.outcome $case.expectedOutcome "$($case.repo)#$($case.pr) $($case.threadId) outcome"
    Assert-Equal $state.action $case.expectedAction "$($case.repo)#$($case.pr) $($case.threadId) action"
}
Write-Output "PowerShell parser and $script:assertions helper assertions passed."
