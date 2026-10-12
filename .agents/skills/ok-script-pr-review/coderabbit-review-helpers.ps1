# Pure CodeRabbit state helpers shared by wait-coderabbit.ps1 and wait-coderabbit-rate-limit.ps1.
# Nothing here calls gh. Every body, login and status text is untrusted GitHub data.

$script:CodeRabbitExitCodes = @{
    REVIEWED = 0; AVAILABLE = 0
    ERROR = 2; DRAFT = 3; CLOSED = 4; SKIPPED = 5; REVIEW_FAILED = 5
    TIMEOUT = 6; UNCONFIRMED = 6; NO_SIGNAL = 6
    RATE_LIMITED = 7; UNAVAILABLE = 7; TRIGGER_RATE_LIMITED = 7
    TRIGGER_EXHAUSTED = 8; TRIGGER_REQUIRED = 9; HEAD_CHANGED = 11
    NO_REPLY = 12; UNKNOWN_REPLY = 12
}

function Get-CrNow {
    if ($global:CodeRabbitFakeClock) { return $global:CodeRabbitFakeClock.Now }
    return [datetimeoffset]::UtcNow
}

# Human-readable times follow the computer's current timezone; protocol timestamps stay in UTC.
function Format-CrDisplayTime {
    param([datetimeoffset]$Time, [TimeZoneInfo]$TimeZone = [TimeZoneInfo]::Local)
    return [TimeZoneInfo]::ConvertTime($Time, $TimeZone).ToString('yyyy-MM-dd HH:mm:ss zzz', [Globalization.CultureInfo]::InvariantCulture)
}

function Wait-CrSeconds {
    param([double]$Seconds)
    if ($Seconds -le 0) { return }
    if ($global:CodeRabbitFakeClock) {
        $global:CodeRabbitFakeClock.Now = $global:CodeRabbitFakeClock.Now.AddSeconds($Seconds)
        return
    }
    Start-Sleep -Milliseconds ([int][Math]::Ceiling($Seconds * 1000))
}

function ConvertTo-CrTime {
    param([object]$Value)
    if ($null -eq $Value -or "$Value" -eq '') { return $null }
    if ($Value -is [datetimeoffset]) { return $Value }
    if ($Value -is [datetime]) {
        # PowerShell 7 ConvertFrom-Json already turned the ISO string into a DateTime.
        if ($Value.Kind -eq [DateTimeKind]::Unspecified) { $Value = [datetime]::SpecifyKind($Value, [DateTimeKind]::Utc) }
        return [datetimeoffset]$Value
    }
    return [datetimeoffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture)
}

# REST reports the bot as "coderabbitai[bot]"/"Bot"; GraphQL reports "coderabbitai" with __typename "Bot".
function Test-CodeRabbitAuthor {
    param([string]$Login, [string]$Type)
    return ($Login -eq 'coderabbitai[bot]' -or $Login -eq 'coderabbitai') -and $Type -eq 'Bot'
}

function Test-CodeRabbitReviewCommand {
    param([AllowEmptyString()][string]$Body)
    return ([string]$Body).Trim() -match '^(?i)@coderabbitai\s+(?:full\s+)?review$'
}

function Get-CrPlainText {
    param([AllowEmptyString()][string]$Body)
    return (([string]$Body) -replace '(?s)<!--.*?-->', ' ' -replace '[*_`]', '' -replace '\s+', ' ').Trim()
}

# Seconds until CodeRabbit says to look again. This is only a recheck time, never proof of quota.
function Get-CodeRabbitRetrySeconds {
    param([AllowEmptyString()][string]$Body)
    $text = Get-CrPlainText $Body
    $unit = '(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)'
    $match = [regex]::Match($text, "(?i)(?:available in|wait|try again in|retry in)\s+((?:\d+(?:\.\d+)?\s*$unit(?:\s*(?:,|and)\s*)?)+)")
    if (-not $match.Success) { return $null }
    $total = 0.0
    foreach ($part in [regex]::Matches($match.Groups[1].Value, "(?i)(\d+(?:\.\d+)?)\s*($unit)")) {
        $value = [double]::Parse($part.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
        switch -Regex ($part.Groups[2].Value) {
            '^(?i)h' { $total += $value * 3600 }
            '^(?i)m' { $total += $value * 60 }
            default { $total += $value }
        }
    }
    return [int][Math]::Ceiling($total)
}

# available | unavailable | unknown. Negative forms are checked before any positive phrase.
function Get-CodeRabbitQuotaState {
    param([AllowEmptyString()][string]$Body)
    $text = Get-CrPlainText $Body
    if ($text -match '(?i)\b(?:no|zero|0)\s+(?:more\s+)?(?:included\s+|pr\s+)?reviews?\s+(?:are\s+|is\s+)?(?:currently\s+)?(?:available|remaining|left)\b' -or
        $text -match '(?i)\breviews?\s+(?:are|is)\s+(?:not|n''t)\s+(?:currently\s+)?available\b' -or
        $text -match '(?i)\breviews?\s+aren''t\s+available\b') {
        return 'unavailable'
    }
    if ($text -match '(?i)\breviews?\s+(?:are|is)\s+available now\b') {
        return 'available'
    }
    if ($null -ne (Get-CodeRabbitRetrySeconds $text)) { return 'unavailable' }
    if ($text -match '(?i)\brate limit(?:ed| exceeded| reached)\b|\breview rate limited\b') { return 'unavailable' }
    $count = [regex]::Match($text, '(?i)\b(\d+)\s+(?:included\s+|pr\s+)?reviews?\s+(?:are\s+)?(?:remaining|left)\b')
    if (-not $count.Success) { $count = [regex]::Match($text, '(?i)\b(?:remaining|available)\s+(?:included\s+|pr\s+)?reviews?\s*[:=]\s*(\d+)\b') }
    if ($count.Success) {
        if ([int]$count.Groups[1].Value -gt 0) { return 'available' }
        return 'unavailable'
    }
    return 'unknown'
}

# A review record proves coverage only when it is CodeRabbit's review summary for exactly this head.
# Thread replies also create review records on the current head, but with an empty body.
function Test-CodeRabbitReviewCoversHead {
    param([object]$Review, [string]$Head)
    if (-not (Test-CodeRabbitAuthor $Review.login $Review.type)) { return $false }
    if ([string]$Review.commit_id -ne $Head) { return $false }
    if ($Review.state -notin @('COMMENTED', 'APPROVED', 'CHANGES_REQUESTED')) { return $false }
    $body = [string]$Review.body
    if ($body -notmatch 'auto-generated comment by CodeRabbit for review status|Actionable comments posted:\s*\d+') { return $false }
    $ranges = [regex]::Matches($body, '(?i)between\s+([0-9a-f]{40})\s+and\s+([0-9a-f]{40})')
    if ($ranges.Count -gt 0 -and $ranges[$ranges.Count - 1].Groups[2].Value -ne $Head) { return $false }
    return $true
}

# CodeRabbit's walkthrough summary records which commit its latest review covered.
function Get-CodeRabbitSummaryCoverage {
    param([object[]]$Comments, [string]$Head)
    foreach ($comment in @($Comments)) {
        if (-not (Test-CodeRabbitAuthor $comment.login $comment.type)) { continue }
        foreach ($match in [regex]::Matches([string]$comment.body, '<!--\s*final_review_risk_coverage:(\{[^>]*?\})\s*-->')) {
            try { $coverage = $match.Groups[1].Value | ConvertFrom-Json } catch { continue }
            if ($coverage.coveredCommitId -eq $Head -and $coverage.kind -eq 'reviewed') { return $comment }
        }
    }
    return $null
}

function Get-CodeRabbitLatestStatus {
    param([object[]]$Statuses)
    $own = @($Statuses | Where-Object { $_.context -eq 'CodeRabbit' -and (Test-CodeRabbitAuthor $_.login $_.type) })
    if ($own.Count -eq 0) { return $null }
    return @($own | Sort-Object @{ Expression = { $_.created } }, @{ Expression = { [long]$_.id } })[-1]
}

# Earliest moment the head is known to exist on GitHub; triggers before it belong to an older head.
function Get-CodeRabbitHeadArrival {
    param([object[]]$ObservedTimes, [object]$CommitTime)
    $times = @($ObservedTimes | Where-Object { $null -ne $_ } | Sort-Object)
    if ($times.Count -gt 0) { return $times[0] }
    return $CommitTime
}

function New-CrState {
    param([string]$State, [string]$Evidence = '', [string]$Detail = '', [object]$Since = $null)
    return [pscustomobject]@{ state = $State; evidence = $Evidence; detail = $Detail; since = $Since }
}

<#
Snapshot fields: head, prState, draft, statuses, reviews, comments, headArrivedAt, ledger.
Comments/reviews/statuses carry login, type, created (datetimeoffset) and body/description.
The result is one state of the per-head machine; callers decide how long each waiting state may last.
#>
function Get-CodeRabbitHeadState {
    param([object]$Snapshot)
    $head = [string]$Snapshot.head
    if ($Snapshot.prState -ne 'open') { return New-CrState 'CLOSED' -Detail "PR state is $($Snapshot.prState)" }
    if ($Snapshot.draft) { return New-CrState 'DRAFT' -Detail 'PR is a draft; do not un-draft it to get a review' }

    $latest = Get-CodeRabbitLatestStatus $Snapshot.statuses
    $description = if ($latest) { [string]$latest.description } else { '' }
    if ($latest -and $latest.state -eq 'pending') {
        return New-CrState 'IN_PROGRESS' -Evidence "status:$($latest.id)" -Detail $description -Since $latest.created
    }

    $covering = @($Snapshot.reviews | Where-Object { Test-CodeRabbitReviewCoversHead $_ $head } | Sort-Object { $_.created })
    if ($covering.Count -gt 0) {
        return New-CrState 'REVIEWED' -Evidence "review:$($covering[-1].id)" -Since $covering[-1].created
    }
    $completed = $latest -and $latest.state -eq 'success' -and $description -match '(?i)review (?:completed|finished)'
    if ($completed) {
        $summary = Get-CodeRabbitSummaryCoverage $Snapshot.comments $head
        if ($summary) { return New-CrState 'REVIEWED' -Evidence "summary:$($summary.id)" -Since $latest.created }
    }

    $arrival = $Snapshot.headArrivedAt
    $triggers = @($Snapshot.comments | Where-Object {
        -not (Test-CodeRabbitAuthor $_.login $_.type) -and (Test-CodeRabbitReviewCommand $_.body) -and
        ($null -eq $arrival -or $_.created -ge $arrival)
    } | Sort-Object { $_.created })
    $triggerTime = $null
    $triggerEvidence = ''
    if ($triggers.Count -gt 0) { $triggerTime = $triggers[0].created; $triggerEvidence = "comment:$($triggers[0].id)" }
    if ($Snapshot.ledger -and $Snapshot.ledger.head -eq $head) {
        $ledgerTime = ConvertTo-CrTime $Snapshot.ledger.recordedAt
        if ($null -eq $triggerTime -or ($ledgerTime -and $ledgerTime -lt $triggerTime)) {
            $triggerTime = $ledgerTime
            $triggerEvidence = "ledger:$($Snapshot.ledger.path)"
        }
    }
    if ($null -ne $triggerTime) {
        # Same-second timestamps are ambiguous; treating them as "limited after the trigger" never re-triggers.
        if ($latest -and $latest.created -ge $triggerTime -and $description -match '(?i)rate limit') {
            return New-CrState 'TRIGGER_RATE_LIMITED' -Evidence "status:$($latest.id)" -Detail $description -Since $triggerTime
        }
        $acks = @($Snapshot.comments | Where-Object {
            (Test-CodeRabbitAuthor $_.login $_.type) -and $_.created -ge $triggerTime -and
            [string]$_.body -match 'CodeRabbit review command invocation|Action performed'
        })
        foreach ($ack in $acks) {
            if ((Get-CodeRabbitQuotaState $ack.body) -eq 'unavailable') {
                return New-CrState 'TRIGGER_RATE_LIMITED' -Evidence "comment:$($ack.id)" -Detail (Get-CrPlainText $ack.body) -Since $triggerTime
            }
        }
        if ($completed -and $latest.created -gt $triggerTime) {
            return New-CrState 'COMPLETED_UNCONFIRMED' -Evidence "status:$($latest.id)" -Detail 'triggered review finished but no review record or summary covers this head' -Since $latest.created
        }
        $detail = if ($acks.Count -gt 0) { Get-CrPlainText $acks[-1].body } else { 'waiting for CodeRabbit to acknowledge' }
        return New-CrState 'TRIGGERED' -Evidence $triggerEvidence -Detail $detail -Since $triggerTime
    }

    if (-not $latest) { return New-CrState 'AWAITING_AUTO' -Detail 'no CodeRabbit status on this head yet' }
    if ($description -match '(?i)rate limit') { return New-CrState 'RATE_LIMITED' -Evidence "status:$($latest.id)" -Detail $description -Since $latest.created }
    if ($description -match '(?i)review skipped') {
        if ($description -match '(?i)manual review required') {
            return New-CrState 'MANUAL_REQUIRED' -Evidence "status:$($latest.id)" -Detail $description -Since $latest.created
        }
        if ($description -match '(?i)draft') { return New-CrState 'DRAFT' -Evidence "status:$($latest.id)" -Detail $description }
        return New-CrState 'SKIPPED' -Evidence "status:$($latest.id)" -Detail $description -Since $latest.created
    }
    if ($completed) {
        return New-CrState 'COMPLETED_UNCONFIRMED' -Evidence "status:$($latest.id)" -Detail 'status says completed but no review record or summary covers this head' -Since $latest.created
    }
    if ($latest.state -in @('error', 'failure')) {
        return New-CrState 'REVIEW_FAILED' -Evidence "status:$($latest.id)" -Detail $description -Since $latest.created
    }
    return New-CrState 'AWAITING_AUTO' -Evidence "status:$($latest.id)" -Detail "unrecognized status: $($latest.state) $description" -Since $latest.created
}

function Get-CrLedgerPath {
    param([string]$StateDir, [string]$Repo, [int]$PrNumber, [string]$Head)
    $name = ('{0}_pr{1}_{2}.json' -f ($Repo -replace '[^A-Za-z0-9_.-]', '_'), $PrNumber, $Head.ToLowerInvariant())
    return Join-Path $StateDir $name
}

function ConvertTo-CrResultJson {
    param([hashtable]$Result)
    return ($Result | ConvertTo-Json -Compress -Depth 5)
}

# ---- review thread outcomes --------------------------------------------------------------
# One flag per thread, describing what the peer did after its own finding. Every phrase below
# only classifies text CodeRabbit wrote; bodies stay untrusted data and are never executed.
#
# AWAITING_DETECTION is the normal state after we push a fix without replying: the thread is
# still open because the peer has not looked at the new head yet. It is not a request to reply.
$script:CrThreadPendingFlags = @('AWAITING_DETECTION', 'AWAITING_PEER_REPLY')
$script:CrThreadTerminalFlags = @('RESOLVED_SILENT', 'RESOLVED_BY_OTHER', 'ACCEPTED', 'ACCEPTED_OPEN',
    'WITHDRAWN', 'KEPT_OPEN', 'FOLLOW_UP', 'RATE_LIMITED', 'NEEDS_REVIEW')
$script:CrThreadAllFlags = @($script:CrThreadPendingFlags + $script:CrThreadTerminalFlags)

# What the caller has to do about a flag. NONE means the thread needs no reply from us.
$script:CrThreadActions = @{
    RESOLVED_SILENT = 'NONE'; RESOLVED_BY_OTHER = 'NONE'; ACCEPTED = 'WAIT_PEER'; WITHDRAWN = 'WAIT_PEER'
    ACCEPTED_OPEN = 'REVIEW'
    AWAITING_PEER_REPLY = 'WAIT_PEER'; RATE_LIMITED = 'WAIT_QUOTA'
    KEPT_OPEN = 'REPLY'; FOLLOW_UP = 'REPLY'
    NEEDS_REVIEW = 'REVIEW'
    AWAITING_DETECTION = 'WAIT_PEER'
}

function Get-CrThreadAction {
    param([string]$Outcome, [bool]$IsResolved = $false)
    # Old wording does not reopen a resolved thread. Contradictory risk statements still need
    # inspection, but never another automatic reply, quota wait or resolve operation.
    if ($IsResolved) {
        if ($Outcome -in @('KEPT_OPEN', 'FOLLOW_UP', 'NEEDS_REVIEW')) { return 'REVIEW' }
        return 'NONE'
    }
    if ($script:CrThreadActions.ContainsKey($Outcome)) { return $script:CrThreadActions[$Outcome] }
    return 'REVIEW'
}

# Match the finding's author, not every bot. Only resolvedBy uses the platform's alternate
# CodeRabbit login/type shape; comment authors still require the exact Bot identity.
function Test-CrThreadPeer {
    param([object]$Comment, [object]$Peer, [switch]$Resolver)
    if (-not $Peer -or -not $Peer.login -or -not $Comment.login) { return $false }
    if (Test-CodeRabbitAuthor ([string]$Peer.login) ([string]$Peer.type)) {
        if ($Resolver) { return $Comment.login -eq 'coderabbitai[bot]' -and $Comment.type -in @('User', 'Bot') }
        return Test-CodeRabbitAuthor ([string]$Comment.login) ([string]$Comment.type)
    }
    return $Comment.login -eq $Peer.login -and $Comment.type -eq $Peer.type
}

# Only the peer's visible prose is its answer. Quoted material must never be read as one:
# CodeRabbit puts the finding, the diff and a "Prompt for AI Agents" block inside <details>,
# and it quotes code in fences, inline spans and blockquotes. Any of those can carry the very
# phrases this classifier looks for - this skill's own scripts contain them verbatim.
function Get-CrThreadAnswerText {
    param([AllowEmptyString()][string]$Body)
    $text = [string]$Body
    $text = $text -replace '(?is)<!-- This is an auto-generated comment: tweet message.*?<!-- end of auto-generated comment: tweet message.*?-->', ' '
    $text = $text -replace '(?s)<!--.*?-->', ' '
    # Nested/attributed details must be removed as a whole, including an unclosed final block.
    $visible = New-Object Text.StringBuilder
    $depth = 0; $cursor = 0
    foreach ($tag in [regex]::Matches($text, '(?is)<(?<close>/)?details\b[^>]*>')) {
        if ($depth -eq 0) { [void]$visible.Append($text.Substring($cursor, $tag.Index - $cursor)) }
        if ($tag.Groups['close'].Success) { $depth = [Math]::Max(0, $depth - 1) } else { $depth++ }
        $cursor = $tag.Index + $tag.Length
    }
    if ($depth -eq 0) { [void]$visible.Append($text.Substring($cursor)) }
    $text = $visible.ToString()
    # Fence length/type must match; a four-backtick quote may contain triple backticks.
    $text = $text -replace '(?ms)^[ \t]{0,3}(?<fence>`{3,}|~{3,})[^\r\n]*\r?\n.*?(?:^[ \t]{0,3}\k<fence>[`~]*[ \t]*$|\z)', ' '
    $text = $text -replace '(?s)(?<!`)(?<ticks>`+)(?!`).*?\k<ticks>(?!`)', ' '
    $text = $text -replace '<[^>]+>', ' '
    $text = $text -replace '(?m)^[ \t]*>.*$', ' '
    # Link targets are metadata, not claims of acceptance (e.g. /verified in a URL).
    $text = $text -replace '\[([^\]]*)\]\([^\s]*(?:\s+"[^"]*")?\)', '$1'
    return Get-CrPlainText $text
}

# How CodeRabbit answered after its own finding. The order matters: a withdrawal usually also
# thanks us, a thread it keeps open may still confirm part of the fix, and a platform failure
# notice reads like "remains open" while actually confirming the fix.
function Get-CrThreadPeerOutcome {
    param([AllowEmptyString()][string]$Body)
    $text = Get-CrThreadAnswerText $Body
    if ($text -match '(?i)rate limit(?:ed| exceeded| reached)') { return 'RATE_LIMITED' }
    $unverified = '(?i)(?:修复|问题|风险)[^。；]{0,16}(?:尚未|仍未|未完成|仍(?:然)?存在)|(?:not|not yet|isn''t|is not) (?:fixed|resolved|verified)|(?:issue|risk) (?:still )?remains|cannot confirm|can''t confirm|尚不能确认|无法确认|没有核对|未验证'
    if ($text -match "(?i)couldn'?t resolve this review thread|can(?:not|'t) resolve this review thread") {
        if ($text -match $unverified -or $text -match '(?i)still (?:needs|requires)|仍需|请让|请更新') { return 'NEEDS_REVIEW' }
        if ($text -match '(?i)thanks for confirming the fix|已确认|感谢修复|问题已修复') { return 'ACCEPTED_OPEN' }
        return 'NEEDS_REVIEW'
    }
    if ($text -match '(?i)撤回|withdraw|(?:评论|意见|建议|问题|这条|该条)[^。]{0,10}不适用') { return 'WITHDRAWN' }
    if ($text -match '(?i)(?:线程|讨论|评论|问题|意见)[^。；，]{0,16}保持[^。；，]{0,8}(?:开放|打开|未解决)|保持[^。；，]{0,8}(?:线程|讨论|评论|问题|意见)[^。；，]{0,8}(?:开放|打开|未解决)|(?:thread|finding|conversation)[^.]{0,24}(?:remain(?:s)?|left|open)|(?:keep|leave)[^.]{0,24}(?:thread|finding|conversation)[^.]{0,12}open') { return 'KEPT_OPEN' }
    if ($text -match $unverified) { return 'NEEDS_REVIEW' }
    if ($text -match '(?i)仍需|请让|请在|请把|请将|请更新|请同时|请改为|应与[^。]{0,12}一起提交|should also') { return 'FOLLOW_UP' }
    if ($text -match '(?i)^[，,。\s]*确认[。，]|fixed in #\d+|感谢修复|感谢确认|已确认|已核对|已核实|核验通过|已核查|已验证|已复核|覆盖了本条|标记为已解决|问题已修复|已在当前代码中核实|确认了你的说法|补充核查完成|这解决了原评论|thanks for (?:fixing|the fix|confirming the fix)|\bI (?:have )?verified\b|(?:^|[,.;\s])verified(?:[,.]| at )') { return 'ACCEPTED' }
    return 'NEEDS_REVIEW'
}

<#
Classifies one review thread from its comments (oldest first) plus the thread's resolution state.
Each comment carries login, type, body and created.

The anchor is the peer's own first comment (its finding), not our reply: under this review flow we
push a fix without replying, so "the peer came back" has to be measured from its finding. Returns
the flag and action set that wait-review-threads.ps1 publishes.
#>
function Get-CrThreadOutcome {
    param([object[]]$Comments, [bool]$IsResolved = $false, [string]$ResolvedBy = '', [string]$ResolvedByType = '')
    $list = @($Comments)
    $peer = if ($list.Count -gt 0) { $list[0] } else { $null }
    $lastResponse = -1
    $lastPeerIndex = -1
    $lastPeer = $null
    for ($i = 1; $i -lt $list.Count; $i++) {
        if (Test-CrThreadPeer $list[$i] $peer) { $lastPeer = $list[$i]; $lastPeerIndex = $i }
        else { $lastResponse = $i }
    }
    $ourReplied = $lastResponse -ge 1
    $peerAnswered = $null -ne $lastPeer
    $resolvedByPeer = Test-CrThreadPeer ([pscustomobject]@{login = $ResolvedBy; type = $ResolvedByType}) $peer -Resolver
    $lastPeerOutcome = if ($lastPeer -and (Test-CodeRabbitAuthor $lastPeer.login $lastPeer.type)) {
        Get-CrThreadPeerOutcome $lastPeer.body
    } else { 'NEEDS_REVIEW' }

    if (-not $IsResolved -and $lastResponse -gt $lastPeerIndex) {
        # An unrelated bot can introduce a separate finding; it is not a response from us.
        $outcome = if ($list[$lastResponse].type -eq 'Bot' -or -not $list[$lastResponse].login) {
            'NEEDS_REVIEW'
        } else { 'AWAITING_PEER_REPLY' }
    } elseif ($peerAnswered) {
        $outcome = $lastPeerOutcome
    } elseif ($IsResolved) {
        $outcome = $(if ($resolvedByPeer) { 'RESOLVED_SILENT' } else { 'RESOLVED_BY_OTHER' })
    } elseif (-not $peer -or -not (Test-CodeRabbitAuthor $peer.login $peer.type)) {
        $outcome = 'NEEDS_REVIEW'
    } else {
        $outcome = 'AWAITING_DETECTION'
    }

    $last = if ($list.Count -gt 0) { $list[$list.Count - 1] } else { $null }
    return [pscustomobject]@{
        outcome = $outcome
        action = Get-CrThreadAction $outcome $IsResolved
        lastPeerOutcome = $lastPeerOutcome
        peerAnswered = $peerAnswered
        ourReplied = $ourReplied
        isResolved = [bool]$IsResolved
        resolvedBy = $ResolvedBy
        resolution = $(if (-not $IsResolved) { 'OPEN' } elseif ($resolvedByPeer) { 'PEER' } elseif ($ResolvedBy) { 'OTHER' } else { 'UNKNOWN' })
        ourComments = @($list | Select-Object -Skip 1 | Where-Object { -not (Test-CrThreadPeer $_ $peer) }).Count
        peerComments = @($list | Where-Object { Test-CrThreadPeer $_ $peer }).Count
        totalComments = $list.Count
        lastAuthor = $(if ($last) { [string]$last.login } else { '' })
        lastReplyAt = $(if ($last) { $last.created } else { $null })
    }
}

# Flags that still change on their own; everything else needs a human before it moves.
function Test-CrThreadOutcomePending {
    param([string]$Outcome)
    return $Outcome -in $script:CrThreadPendingFlags
}
