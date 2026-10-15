<#
Answers one question: is a CodeRabbit review available for this PR right now?
Posts `@coderabbitai rate limit` (a public comment that does not consume review quota), reads only
CodeRabbit replies newer than that query, and re-queries after the suggested delay. A countdown is only
the next check time; AVAILABLE is returned solely on an explicit positive reply. Never requests a review.
Last stdout line is a JSON result; exit code: 0 AVAILABLE, 7 UNAVAILABLE, 11 HEAD_CHANGED, 3 DRAFT,
4 CLOSED, 12 NO_REPLY/UNKNOWN_REPLY, 2 ERROR.
#>
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')][string]$Repo,
    [Parameter(Mandatory = $true)][ValidateRange(1, 2147483647)][int]$PrNumber,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$ExpectedHead,
    [ValidateRange(0, 86400)][int]$MaxWaitSeconds = 3600,
    [ValidateRange(10, 1800)][int]$ReplyTimeoutSeconds = 180,
    [ValidateRange(1, 300)][int]$PollSeconds = 15,
    [ValidateRange(1, 50)][int]$MaxProbes = 6,
    [ValidateRange(0, 900)][int]$RecheckBufferSeconds = 30,
    [ValidateRange(30, 3600)][int]$DefaultRecheckSeconds = 300
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot 'coderabbit-review-helpers.ps1')
. (Join-Path $PSScriptRoot 'coderabbit-github.ps1')
$ExpectedHead = $ExpectedHead.ToLowerInvariant()

function Complete-RateLimit {
    param([string]$State, [hashtable]$Extra = @{})
    $result = @{ script = 'rate-limit'; repo = $Repo; pr = $PrNumber; head = $ExpectedHead; state = $State }
    foreach ($key in $Extra.Keys) { $result[$key] = $Extra[$key] }
    Write-Output (ConvertTo-CrResultJson $result)
    exit $CodeRabbitExitCodes[$State]
}

# Returns a terminal state name when the PR no longer matches the session, otherwise $null.
function Test-Session {
    $pr = Get-CrPr $Repo $PrNumber
    if ($pr.state -ne 'open') { return 'CLOSED' }
    if ($pr.draft) { return 'DRAFT' }
    if ($pr.head -ne $ExpectedHead) { $script:ActualHead = $pr.head; return 'HEAD_CHANGED' }
    return $null
}

function Wait-WithSessionCheck {
    param([datetimeoffset]$Until)
    while ((Get-CrNow) -lt $Until) {
        Wait-CrSeconds ([Math]::Min($PollSeconds, ($Until - (Get-CrNow)).TotalSeconds))
        $stop = Test-Session
        if ($stop) { return $stop }
    }
    return $null
}

try {
    $deadline = (Get-CrNow).AddSeconds($MaxWaitSeconds)
    Write-Host "[rate-limit] waiting deadline: $(Format-CrDisplayTime $deadline) (local time; maximum wait $MaxWaitSeconds seconds; not a review completion estimate)"
    for ($probeCount = 1; $probeCount -le $MaxProbes; $probeCount++) {
        $stop = Test-Session
        if ($stop) { Complete-RateLimit $stop @{ actualHead = $script:ActualHead } }

        Write-Host "[rate-limit] query $probeCount/$MaxProbes for $Repo#$PrNumber at $($ExpectedHead.Substring(0, 7))"
        $probe = New-CrIssueComment $Repo $PrNumber '@coderabbitai rate limit'
        $replyDeadline = (Get-CrNow).AddSeconds($ReplyTimeoutSeconds)
        $floor = (Get-CrNow).AddSeconds($PollSeconds)
        $cap = if ($deadline -gt $floor) { $deadline } else { $floor }
        if ($replyDeadline -gt $cap) { $replyDeadline = $cap }
        $reply = $null
        $sawUnknown = $null
        while ($true) {
            $replies = @(Get-CrIssueComments $Repo $PrNumber | Where-Object {
                $_.id -gt $probe.id -and $_.created -ge $probe.created -and (Test-CodeRabbitAuthor $_.login $_.type)
            } | Sort-Object id)
            foreach ($candidate in $replies) {
                $quota = Get-CodeRabbitQuotaState $candidate.body
                if ($quota -ne 'unknown') { $reply = [pscustomobject]@{ quota = $quota; comment = $candidate }; break }
                $sawUnknown = $candidate
            }
            if ($reply -or (Get-CrNow) -ge $replyDeadline) { break }
            $stop = Wait-WithSessionCheck ((Get-CrNow).AddSeconds($PollSeconds))
            if ($stop) { Complete-RateLimit $stop @{ actualHead = $script:ActualHead; probeId = $probe.id } }
        }
        if (-not $reply) {
            if ($sawUnknown) { Complete-RateLimit 'UNKNOWN_REPLY' @{ probeId = $probe.id; replyId = $sawUnknown.id; reply = Get-CrPlainText $sawUnknown.body } }
            Complete-RateLimit 'NO_REPLY' @{ probeId = $probe.id }
        }
        if ($reply.quota -eq 'available') {
            Complete-RateLimit 'AVAILABLE' @{ probeId = $probe.id; replyId = $reply.comment.id; reply = Get-CrPlainText $reply.comment.body }
        }

        $suggested = Get-CodeRabbitRetrySeconds $reply.comment.body
        $delay = if ($null -ne $suggested) { $suggested + $RecheckBufferSeconds } else { $DefaultRecheckSeconds }
        $nextCheck = (Get-CrNow).AddSeconds([Math]::Max(1, $delay))
        $detail = @{ probeId = $probe.id; replyId = $reply.comment.id; reply = Get-CrPlainText $reply.comment.body; nextCheckAt = $nextCheck.ToString('o') }
        $remaining = [TimeSpan]::FromSeconds([Math]::Max(0, [Math]::Ceiling(($nextCheck - (Get-CrNow)).TotalSeconds)))
        $nextQuery = "$(Format-CrDisplayTime $nextCheck) (local time; in $($remaining.ToString('c')))"
        if ($nextCheck -gt $deadline -or $probeCount -eq $MaxProbes) {
            Write-Host "[rate-limit] unavailable; suggested next query at $nextQuery; this wait has stopped (review completion time is unknown)"
            Complete-RateLimit 'UNAVAILABLE' $detail
        }
        Write-Host "[rate-limit] unavailable; next query at $nextQuery (a countdown is not proof of quota; review completion time is unknown)"
        $stop = Wait-WithSessionCheck $nextCheck
        if ($stop) { Complete-RateLimit $stop @{ actualHead = $script:ActualHead } }
    }
    Complete-RateLimit 'UNAVAILABLE' @{}
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    Complete-RateLimit 'ERROR' @{ error = $_.Exception.Message }
}
