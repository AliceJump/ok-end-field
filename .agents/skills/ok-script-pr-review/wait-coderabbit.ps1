<#
Drives one PR to a CodeRabbit review that covers its current head.

Every poll re-reads the PR head. A review session belongs to exactly one head SHA: when the head
changes, all waits, quota answers and trigger bookkeeping of the old session are dropped and the new
head is judged from scratch. Only a CodeRabbit review record for the head (or CodeRabbit's summary
coverage marker plus a completed status) ends in REVIEWED; statuses and replies are signals only.

`@coderabbitai review` is posted at most once per head, and only after wait-coderabbit-rate-limit.ps1
returned AVAILABLE and a fresh re-check shows: PR open, head unchanged, no covering review, no review
in progress, and no earlier trigger for this head (remote comment or local ledger).
Last stdout line is a JSON result; see $CodeRabbitExitCodes in coderabbit-review-helpers.ps1.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repo,
    [Parameter(Mandatory = $true)][ValidateRange(1, 2147483647)][int]$PrNumber,
    [ValidatePattern('^([0-9a-fA-F]{40})?$')][string]$ExpectedHead = '',
    [ValidateRange(60, 86400)][int]$TimeoutSeconds = 7200,
    [ValidateRange(0, 3600)][int]$AutoWaitSeconds = 300,
    [ValidateRange(60, 7200)][int]$ReviewWaitSeconds = 1800,
    [ValidateRange(0, 3600)][int]$UnconfirmedGraceSeconds = 180,
    [ValidateRange(5, 300)][int]$PollSeconds = 30,
    [ValidateRange(0, 86400)][int]$MaxQuotaWaitSeconds = 3600,
    [string]$StateDir = '',
    [switch]$NoTrigger,
    [switch]$StopOnHeadChange
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'coderabbit-review-helpers.ps1')
. (Join-Path $PSScriptRoot 'coderabbit-github.ps1')
if (-not $StateDir) {
    $StateDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ok-script-pr-review\coderabbit-triggers'
}

function Complete-Wait {
    param([string]$State, [string]$Head, [object]$Info = $null, [hashtable]$Extra = @{})
    $result = @{ script = 'wait'; repo = $Repo; pr = $PrNumber; head = $Head; state = $State }
    if ($Info) { $result.evidence = $Info.evidence; $result.detail = $Info.detail }
    foreach ($key in $Extra.Keys) { $result[$key] = $Extra[$key] }
    Write-Output (ConvertTo-CrResultJson $result)
    exit $CodeRabbitExitCodes[$State]
}

function Read-Ledger {
    param([string]$Head)
    $path = Get-CrLedgerPath $StateDir $Repo $PrNumber $Head
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $entry = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $entry | Add-Member -NotePropertyName path -NotePropertyValue $path -Force
    return $entry
}

function Write-Ledger {
    param([string]$Head, [hashtable]$Entry)
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $path = Get-CrLedgerPath $StateDir $Repo $PrNumber $Head
    $Entry.head = $Head; $Entry.repo = $Repo; $Entry.pr = $PrNumber
    # CreateNew makes a concurrent second writer fail instead of sending a second trigger.
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($Entry | ConvertTo-Json -Compress))
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}

function Update-Ledger {
    param([string]$Head, [hashtable]$Entry)
    $path = Get-CrLedgerPath $StateDir $Repo $PrNumber $Head
    $Entry.head = $Head; $Entry.repo = $Repo; $Entry.pr = $PrNumber
    [IO.File]::WriteAllText($path, ($Entry | ConvertTo-Json -Compress), [Text.Encoding]::UTF8)
}

function Get-Snapshot {
    param([string]$Head, [object]$Arrival)
    $pr = Get-CrPr $Repo $PrNumber
    if ($pr.head -ne $Head) { return [pscustomobject]@{ headChanged = $true; head = $pr.head; pr = $pr } }
    $statuses = @(Get-CrStatuses $Repo $Head)
    if ($null -eq $Arrival) { $Arrival = Get-CrHeadArrival $Repo $Head $statuses }
    return [pscustomobject]@{
        headChanged = $false; head = $Head; prState = $pr.state; draft = $pr.draft
        statuses = $statuses; reviews = @(Get-CrReviews $Repo $PrNumber); comments = @(Get-CrIssueComments $Repo $PrNumber)
        headArrivedAt = $Arrival; ledger = Read-Ledger $Head
    }
}

function Wait-WithinDeadline {
    param([double]$Seconds)
    $remaining = ($globalDeadline - (Get-CrNow)).TotalSeconds
    Wait-CrSeconds ([Math]::Max(0, [Math]::Min($Seconds, $remaining)))
}

# After an uncertain send: look for the trigger comment, re-reading the head before each look.
function Find-SentTrigger {
    param([string]$Head, [object]$Arrival)
    for ($attempt = 0; $attempt -lt 3 -and (Get-CrNow) -lt $globalDeadline; $attempt++) {
        Wait-WithinDeadline $PollSeconds
        $pr = Get-CrPr $Repo $PrNumber
        if ($pr.head -ne $Head) { return [pscustomobject]@{ comment = $null; head = $pr.head } }
        $found = @(Get-CrIssueComments $Repo $PrNumber | Where-Object {
            -not (Test-CodeRabbitAuthor $_.login $_.type) -and (Test-CodeRabbitReviewCommand $_.body) -and $_.created -ge $Arrival
        } | Sort-Object created) | Select-Object -Last 1
        if ($found) { return [pscustomobject]@{ comment = $found; head = $Head } }
    }
    return [pscustomobject]@{ comment = $null; head = $Head }
}

function Write-Transition {
    param([string]$Head, [object]$Info)
    $key = "$Head|$($Info.state)|$($Info.evidence)"
    if ($key -eq $script:LastTransition) { return }
    $script:LastTransition = $key
    Write-Host "[wait] $Repo#$PrNumber head=$($Head.Substring(0, 7)) state=$($Info.state) $($Info.evidence) $($Info.detail)".TrimEnd()
}

try {
    $globalDeadline = (Get-CrNow).AddSeconds($TimeoutSeconds)
    $initial = Get-CrPr $Repo $PrNumber
    $head = $initial.head
    if ($ExpectedHead -and $head -ne $ExpectedHead.ToLowerInvariant()) {
        Complete-Wait 'HEAD_CHANGED' $head -Extra @{ expectedHead = $ExpectedHead.ToLowerInvariant() }
    }

    while ($true) {
        # New session: nothing from an earlier head survives this point.
        $sessionStart = Get-CrNow
        $arrival = $null
        $unconfirmedSince = $null
        $restart = $false
        Write-Host "[wait] session for $Repo#$PrNumber head=$head; waiting deadline: $(Format-CrDisplayTime $globalDeadline) (local time; maximum wait $TimeoutSeconds seconds; not a review completion estimate)"

        while (-not $restart) {
            if ((Get-CrNow) -ge $globalDeadline) { Complete-Wait 'TIMEOUT' $head -Extra @{ detail = 'overall timeout' } }
            $snapshot = Get-Snapshot $head $arrival
            if ($snapshot.headChanged) {
                Write-Host "[wait] HEAD_CHANGED $($head.Substring(0, 7)) -> $($snapshot.head.Substring(0, 7)); old session discarded"
                if ($StopOnHeadChange) { Complete-Wait 'HEAD_CHANGED' $snapshot.head -Extra @{ previousHead = $head } }
                $head = $snapshot.head
                $restart = $true
                continue
            }
            $arrival = $snapshot.headArrivedAt
            $info = Get-CodeRabbitHeadState $snapshot
            Write-Transition $head $info
            if ($info.state -ne 'COMPLETED_UNCONFIRMED') { $unconfirmedSince = $null }

            $state = $info.state
            if ($state -in @('REVIEWED', 'CLOSED', 'DRAFT', 'SKIPPED', 'REVIEW_FAILED', 'TRIGGER_RATE_LIMITED')) {
                Complete-Wait $state $head $info
            } elseif ($state -eq 'COMPLETED_UNCONFIRMED') {
                if ($null -eq $unconfirmedSince) { $unconfirmedSince = Get-CrNow }
                if (((Get-CrNow) - $unconfirmedSince).TotalSeconds -ge $UnconfirmedGraceSeconds) { Complete-Wait 'UNCONFIRMED' $head $info }
            } elseif ($state -eq 'AWAITING_AUTO') {
                if (((Get-CrNow) - $sessionStart).TotalSeconds -ge $AutoWaitSeconds) { Complete-Wait 'NO_SIGNAL' $head $info }
            } elseif ($state -eq 'TRIGGERED') {
                $since = if ($info.since) { $info.since } else { $sessionStart }
                if (((Get-CrNow) - $since).TotalSeconds -ge $ReviewWaitSeconds) { Complete-Wait 'TRIGGER_EXHAUSTED' $head $info }
            } elseif ($state -in @('MANUAL_REQUIRED', 'RATE_LIMITED')) {
                if ($NoTrigger) { Complete-Wait 'TRIGGER_REQUIRED' $head $info -Extra @{ reason = $state } }
                $quotaArgs = @{
                    Repo = $Repo; PrNumber = $PrNumber; ExpectedHead = $head
                    MaxWaitSeconds = [int][Math]::Max(0, [Math]::Min($MaxQuotaWaitSeconds, ($globalDeadline - (Get-CrNow)).TotalSeconds))
                }
                $quotaLines = @(& (Join-Path $PSScriptRoot 'wait-coderabbit-rate-limit.ps1') @quotaArgs)
                $quotaExit = $LASTEXITCODE
                if ($quotaLines.Count -eq 0) { throw 'wait-coderabbit-rate-limit.ps1 produced no result' }
                $quota = [string]$quotaLines[-1] | ConvertFrom-Json
                if ($quota.state -eq 'HEAD_CHANGED') {
                    if ($StopOnHeadChange) { Complete-Wait 'HEAD_CHANGED' $quota.actualHead -Extra @{ previousHead = $head } }
                    Write-Host '[wait] HEAD_CHANGED during quota wait; old session discarded'
                    $head = $quota.actualHead
                    $restart = $true
                    continue
                }
                if ($quota.state -in @('CLOSED', 'DRAFT')) { Complete-Wait $quota.state $head -Extra @{ quota = $quota } }
                if ($quota.state -eq 'ERROR') { Complete-Wait 'ERROR' $head $info -Extra @{ error = $quota.error; quota = $quota } }
                if ($quota.state -ne 'AVAILABLE' -or $quotaExit -ne 0) { Complete-Wait 'RATE_LIMITED' $head $info -Extra @{ quota = $quota } }

                # Re-check everything the quota wait may have outdated before the single trigger.
                $fresh = Get-Snapshot $head $arrival
                if ($fresh.headChanged) {
                    if ($StopOnHeadChange) { Complete-Wait 'HEAD_CHANGED' $fresh.head -Extra @{ previousHead = $head } }
                    Write-Host '[wait] HEAD_CHANGED before trigger; old session discarded'
                    $head = $fresh.head
                    $restart = $true
                    continue
                }
                $freshInfo = Get-CodeRabbitHeadState $fresh
                if ($freshInfo.state -notin @('MANUAL_REQUIRED', 'RATE_LIMITED') -or $fresh.ledger) {
                    Write-Host "[wait] state became $($freshInfo.state) during quota wait; no trigger sent"
                    continue
                }
                $entry = @{ recordedAt = (Get-CrNow).ToString('o'); reason = $freshInfo.state; quotaReplyId = $quota.replyId }
                Write-Ledger $head $entry
                try {
                    $posted = New-CrIssueComment $Repo $PrNumber '@coderabbitai review'
                } catch {
                    $message = $_.Exception.Message
                    $ledgerPath = Get-CrLedgerPath $StateDir $Repo $PrNumber $head
                    # Only an explicit 4xx rejection proves nothing was created; then this head may retry later.
                    if ($message -match 'HTTP 4\d\d') {
                        Remove-Item -LiteralPath $ledgerPath -Force -ErrorAction SilentlyContinue
                        throw
                    }
                    # 5xx, timeouts and network errors may still have created the comment: keep the ledger.
                    $entry.status = 'uncertain'; $entry.error = $message
                    Update-Ledger $head $entry
                    $check = Find-SentTrigger $head $arrival
                    if ($check.head -ne $head) {
                        if ($StopOnHeadChange) { Complete-Wait 'HEAD_CHANGED' $check.head -Extra @{ previousHead = $head } }
                        Write-Host '[wait] HEAD_CHANGED while confirming an uncertain trigger; old session discarded'
                        $head = $check.head
                        $restart = $true
                        continue
                    }
                    if (-not $check.comment) {
                        throw "Trigger send failed with an uncertain result ($message). The ledger $ledgerPath still blocks this head; check the PR for the comment before removing it."
                    }
                    $posted = $check.comment
                    $entry.status = 'confirmed-after-error'
                }
                $entry.commentId = $posted.id; $entry.url = $posted.url; $entry.postedAt = (Get-CrNow).ToString('o')
                Update-Ledger $head $entry
                Write-Host "[wait] sent the single review trigger for head $($head.Substring(0, 7)): $($posted.url)"
                continue
            }
            Wait-WithinDeadline $PollSeconds
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    Complete-Wait 'ERROR' $(if ($head) { $head } else { '' }) -Extra @{ error = $_.Exception.Message }
}
