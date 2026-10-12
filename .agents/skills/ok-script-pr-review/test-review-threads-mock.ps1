# Read-only end-to-end tests: paginated API responses and a fake clock, no GitHub writes.
$ErrorActionPreference = 'Stop'
$waitScript = Join-Path $PSScriptRoot 'wait-review-threads.ps1'
$headA = 'a' * 40
$headB = 'b' * 40
$script:passed = 0
$script:failures = 0
function Reset-World {
    $global:CodeRabbitFakeClock = [pscustomobject]@{Now = [datetimeoffset]'2026-10-01T00:00:00Z'}
    $global:CrThreadMock = [pscustomobject]@{
        head = $headA; state = 'open'; draft = $false; prReads = 0; queries = 0
        threads = @(); pages = $false; headChangeAt = 0; failGraphQl = $false
        nullGraphQl = $false; settleAt = -1; lastHead = $null; paginationFault = ''
    }
}
function Comment([string]$Body, [int]$N = 1, [string]$Login = 'coderabbitai', [string]$Type = 'Bot') {
    [pscustomobject]@{id = "C$N"; body = $Body; createdAt = "2026-10-01T00:00:0${N}Z"
        url = "https://example.invalid/#$N"; author = @{login = $Login; __typename = $Type}}
}
function Thread([string]$Id, [object[]]$Comments, [bool]$Resolved = $false) {
    [pscustomobject]@{id = $Id; isResolved = $Resolved; isOutdated = $false; path = 'file.ps1'; line = 1
        resolvedBy = $(if ($Resolved) { @{login = 'coderabbitai[bot]'; __typename = 'User'} } else { $null })
        comments = [pscustomobject]@{nodes = $Comments; pageInfo = @{hasNextPage = $false; endCursor = $null}}}
}
function global:gh {
    $a = @($args)
    if ($a -contains 'POST' -or ($a -join ' ') -match 'mutation') { throw 'Read-only waiter attempted a write' }
    $global:LASTEXITCODE = 0
    $w = $global:CrThreadMock
    if ($a -contains 'graphql') {
        $w.queries++
        if ($w.failGraphQl) { return '{"errors":[{"message":"mock permission error"}]}' }
        if ($w.nullGraphQl) { return '{"data":{"repository":{"pullRequest":null}}}' }
        if ($w.settleAt -ge 0 -and $global:CodeRabbitFakeClock.Now.Second -ge $w.settleAt) {
            foreach ($t in $w.threads) { $t.isResolved = $true; $t.resolvedBy = @{login = 'coderabbitai[bot]'; __typename = 'User'} }
        }
        $query = [string]($a | Where-Object { $_ -like 'query=*' })
        if ($query -like '*node(id:*') {
            $page = @{ nodes = @($w.threads[0].comments.nodes[1]); pageInfo = @{hasNextPage = $false; endCursor = 'c2'} }
            if ($w.paginationFault -eq 'comment-repeat') { $page.pageInfo = @{hasNextPage = $true; endCursor = 'c1'} }
            if ($w.paginationFault -eq 'comment-empty') { $page.pageInfo = @{hasNextPage = $true; endCursor = $null} }
            if ($w.paginationFault -eq 'comment-cycle') {
                $page.pageInfo = @{hasNextPage = $true; endCursor = $(if ($a -contains 'after=c1') { 'c2' } else { 'c1' })}
            }
            return @{data = @{node = @{comments = $page}}} | ConvertTo-Json -Compress -Depth 15
        }
        if ($w.pages) {
            if ($a -contains 'after=t1') { $nodes = @($w.threads[1]); $next = $false }
            else {
                $t = $w.threads[0]
                $nodes = @([pscustomobject]@{id = $t.id; isResolved = $t.isResolved; isOutdated = $t.isOutdated
                    path = $t.path; line = $t.line; resolvedBy = $t.resolvedBy
                    comments = @{nodes = @($t.comments.nodes[0]); pageInfo = @{hasNextPage = $true; endCursor = 'c1'}}})
                $next = $true
            }
            $page = @{nodes = $nodes; pageInfo = @{hasNextPage = $next; endCursor = 't1'}}
            if ($w.paginationFault -eq 'thread-repeat') { $page.pageInfo.hasNextPage = $true }
            if ($w.paginationFault -eq 'thread-empty') { $page.pageInfo.endCursor = $null }
            if ($w.paginationFault -eq 'comment-first-empty' -and $next) { $page.nodes[0].comments.pageInfo.endCursor = $null }
        } else { $page = @{nodes = $w.threads; pageInfo = @{hasNextPage = $false; endCursor = $null}} }
        return @{data = @{repository = @{pullRequest = @{reviewThreads = $page}}}} | ConvertTo-Json -Compress -Depth 15
    }
    if (($a -join ' ') -match '^api repos/example/repo/pulls/7$') {
        $w.prReads++
        if ($w.headChangeAt -gt 0 -and $w.prReads -ge $w.headChangeAt) { $w.head = 'b' * 40 }
        return @{head = @{sha = $w.head}; state = $w.state; draft = $w.draft; merged = $false} | ConvertTo-Json -Compress
    }
    if ($a -contains '--slurp') { return '[[]]' }
    throw "Unexpected API request: $($a -join ' ')"
}
function Run([hashtable]$Extra = @{}) {
    $params = @{Repo = 'example/repo'; PrNumber = 7; PollSeconds = 1; TimeoutSeconds = 3}
    foreach ($key in $Extra.Keys) { $params[$key] = $Extra[$key] }
    $lines = @(& $waitScript @params)
    if ($lines.Count -ne 1) { throw 'Expected exactly one compact JSON output line' }
    return [pscustomobject]@{code = $LASTEXITCODE; result = ([string]$lines[-1] | ConvertFrom-Json)}
}
function Check([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Run-Data {
    $dataScript = Join-Path $PSScriptRoot 'get-coderabbit-review-data.ps1'
    $null = & $dataScript -Repo example/repo -PrNumber 7
    return $LASTEXITCODE
}
function Case([string]$Name, [scriptblock]$Body) {
    Reset-World
    try { & $Body; $script:passed++; Write-Output "ok   $Name" }
    catch { $script:failures++; Write-Output "FAIL $Name :: $($_.Exception.Message)" }
}
Case 'independent pagination of threads and comments' {
    $global:CrThreadMock.pages = $true
    $global:CrThreadMock.threads = @(
        (Thread 'T1' @((Comment 'finding'), (Comment 'Thanks for the fix.' 2)) $true),
        (Thread 'T2' @((Comment 'finding')) $true))
    $r = Run @{Once = $true}
    Check ($r.code -eq 0 -and $r.result.counts.threads -eq 2) 'both thread pages required'
    Check ($r.result.threads[0].totalComments -eq 2 -and $r.result.threads[0].outcome -eq 'ACCEPTED') 'second comment page required'
    Check ($global:CrThreadMock.queries -eq 3) 'two thread pages and one comment page'
}
foreach ($fault in @('thread-repeat', 'thread-empty', 'comment-repeat', 'comment-empty', 'comment-first-empty', 'comment-cycle')) {
    Case "pagination stops on $fault" {
        $global:CrThreadMock.pages = $true
        $global:CrThreadMock.paginationFault = $fault
        $global:CrThreadMock.threads = @(
            (Thread 'T1' @((Comment 'finding'), (Comment 'Thanks for the fix.' 2)) $true),
            (Thread 'T2' @((Comment 'finding')) $true))
        $r = Run @{Once = $true}
        Check ($r.code -eq 2 -and $r.result.observation -eq 'ERROR') 'bad cursors must produce an error'
        Check ($global:CrThreadMock.queries -le 4) 'bad pagination must stop without waiting for the deadline'
        $global:CrThreadMock.queries = 0
        Check ((Run-Data) -eq 2) 'review-data query must also reject the same bad cursors'
        Check ($global:CrThreadMock.queries -le 4) 'review-data query must stop promptly'
    }
}
Case 'once is a waiting snapshot, not a timeout' {
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'))))
    $r = Run @{Once = $true}
    Check ($r.code -eq 6 -and -not $r.result.timedOut -and $r.result.polls -eq 1) 'once must not sleep'
    Check ($r.result.observation -eq 'WAITING') 'snapshot status'
}
Case 'accepted but open waits until actual resolution' {
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'), (Comment 'Thanks for the fix.' 2))))
    $global:CrThreadMock.settleAt = 2
    $r = Run
    Check ($r.code -eq 0 -and $r.result.polls -eq 3 -and $r.result.counts.resolved -eq 1) 'acknowledgement alone is not resolution'
}
Case 'timeout reports the latest snapshot' {
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'))))
    $r = Run
    Check ($r.code -eq 6 -and $r.result.timedOut -and $r.result.observation -eq 'TIMEOUT') 'deadline respected'
}
Case 'custom flags and selected IDs apply to the same snapshot' {
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'), (Comment 'Thanks for the fix.' 2))), (Thread 'T2' @((Comment 'finding'))))
    $r = Run @{ThreadId = @('T1'); WaitFor = @('ACCEPTED')}
    Check ($r.code -eq 0 -and $r.result.counts.threads -eq 1 -and $r.result.counts.awaiting -eq 1) 'requested observation does not imply merge readiness'
}
Case 'push during pagination discards the snapshot' {
    $global:CrThreadMock.headChangeAt = 3
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding')) $true))
    $r = Run
    Check ($r.code -eq 11 -and $r.result.observation -eq 'HEAD_CHANGED' -and $r.result.counts.threads -eq 0) 'mixed-head data must not be published'
}
Case 'push between polls stops at the new head' {
    $global:CrThreadMock.headChangeAt = 4
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'))))
    $r = Run
    Check ($r.code -eq 11 -and $r.result.head -eq $headB -and $r.result.expectedHead -eq $headA) 'session must stay on its initial head'
}
Case 'expected head mismatch performs no thread queries' {
    $r = Run @{ExpectedHead = $headB}
    Check ($r.code -eq 11 -and $global:CrThreadMock.queries -eq 0) 'expected head enforced'
}
Case 'closed PR with unfinished threads does not poll forever' {
    $global:CrThreadMock.state = 'closed'
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'))))
    $r = Run
    Check ($r.code -eq 4 -and $r.result.polls -eq 1) 'closed wait stops'
}
Case 'once preserves the closed state with unfinished threads' {
    $global:CrThreadMock.state = 'closed'
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'))))
    $r = Run @{Once = $true}
    Check ($r.code -eq 4 -and $r.result.observation -eq 'CLOSED' -and $r.result.counts.threads -eq 1) 'historical snapshot retains its closed status and threads'
}
Case 'accepted or withdrawn replies remain matchable after resolution' {
    foreach ($answer in @('Thanks for the fix.', 'I withdraw this finding.')) {
        $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'), (Comment $answer 2)) $true))
        $r = Run @{WaitFor = @('ACCEPTED', 'WITHDRAWN')}
        Check ($r.code -eq 0 -and $r.result.threads[0].outcome -in @('ACCEPTED', 'WITHDRAWN')) 'resolution does not discard the visible peer reply'
    }
}
Case 'historical acceptance does not satisfy a newer pending reply' {
    $global:CrThreadMock.threads = @((Thread 'T1' @((Comment 'finding'), (Comment 'Thanks for the fix.' 2), (Comment 'Please check another change.' 3 'AliceJump' 'User'))))
    $r = Run @{WaitFor = @('ACCEPTED'); Once = $true}
    Check ($r.code -eq 6 -and $r.result.threads[0].outcome -eq 'AWAITING_PEER_REPLY') 'matching lastPeerOutcome here would mask pending work'
}
Case 'no threads is an empty observation, not review completion' {
    $r = Run @{Once = $true}
    Check ($r.code -eq 0 -and $r.result.counts.threads -eq 0 -and $r.result.observation -eq 'SETTLED') 'empty snapshot'
}
Case 'unknown thread is an error' {
    $r = Run @{ThreadId = @('missing')}
    Check ($r.code -eq 2 -and $r.result.observation -eq 'ERROR') 'selection must not silently become empty'
}
Case 'unknown flag is a structured error' {
    $r = Run @{WaitFor = @('unknown')}
    Check ($r.code -eq 2 -and $r.result.observation -eq 'ERROR') 'validation must reach the error handler'
}
Case 'GraphQL error is never an empty successful observation' {
    $global:CrThreadMock.failGraphQl = $true
    $r = Run
    Check ($r.code -eq 2) 'GraphQL errors propagated'
}
Case 'null PR is never an empty successful observation' {
    $global:CrThreadMock.nullGraphQl = $true
    $r = Run
    Check ($r.code -eq 2) 'missing access propagated'
}
Remove-Item Function:\gh
Remove-Variable CodeRabbitFakeClock,CrThreadMock -Scope Global
Write-Output "$script:passed scenario(s) passed, $script:failures failed."
if ($script:failures -gt 0) { exit 1 }
