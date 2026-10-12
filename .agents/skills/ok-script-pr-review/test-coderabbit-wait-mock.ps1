# End-to-end scenarios for wait-coderabbit.ps1 and wait-coderabbit-rate-limit.ps1.
# A global `gh` function replaces the CLI and a fake clock replaces sleeping; nothing touches GitHub.
$ErrorActionPreference = 'Stop'
$folder = $PSScriptRoot
$waitScript = Join-Path $folder 'wait-coderabbit.ps1'
$quotaScript = Join-Path $folder 'wait-coderabbit-rate-limit.ps1'
# gh runs inside the scripts under test, whose own variables (e.g. $pr) would shadow test-script names.
$global:CrMockRepo = 'example/repo'
$global:CrMockPr = 7
$repo = $global:CrMockRepo
$pr = $global:CrMockPr
$headA = 'a' * 40
$headB = 'b' * 40
$base = 'c' * 40
$t0 = [datetimeoffset]'2026-01-01T00:00:00Z'
$marker = '<!-- This is an auto-generated comment by CodeRabbit for review status -->'
$planText = 'Your plan includes PR reviews subject to rate limits.'

function New-World {
    param([string]$Head = $headA)
    $global:CodeRabbitFakeClock = [pscustomobject]@{ Now = $t0 }
    $global:W = [pscustomobject]@{
        head = $Head; state = 'open'; draft = $false; nextId = 1000
        statuses = @{}; reviews = New-Object System.Collections.ArrayList; comments = New-Object System.Collections.ArrayList
        posts = New-Object System.Collections.ArrayList; events = New-Object System.Collections.ArrayList
        onPost = $null; arrival = @{}; failPost = $null; suiteReads = 0
    }
    $global:W.arrival[$Head] = $t0
}

function Get-Now { return $global:CodeRabbitFakeClock.Now }
function Stamp([datetimeoffset]$Time) { return $Time.ToString('yyyy-MM-ddTHH:mm:ssZ') }
function Next-Id { $global:W.nextId++; return $global:W.nextId }

function Add-Status([string]$Sha, [string]$State, [string]$Description, [string]$Login = 'coderabbitai[bot]', [string]$Type = 'Bot') {
    if (-not $global:W.statuses.ContainsKey($Sha)) { $global:W.statuses[$Sha] = New-Object System.Collections.ArrayList }
    # GitHub lists statuses newest first.
    $global:W.statuses[$Sha].Insert(0, [ordered]@{ id = (Next-Id); context = 'CodeRabbit'; state = $State; description = $Description
        creator = @{ login = $Login; type = $Type }; created_at = (Stamp (Get-Now)) }) | Out-Null
}
function Add-Review([string]$Sha, [string]$Body) {
    $global:W.reviews.Add([ordered]@{ id = (Next-Id); node_id = 'R'; user = @{ login = 'coderabbitai[bot]'; type = 'Bot' }
        commit_id = $Sha; state = 'COMMENTED'; submitted_at = (Stamp (Get-Now)); body = $Body; html_url = 'u' }) | Out-Null
}
function Add-CoveringReview([string]$Sha) {
    Add-Review $Sha "**Actionable comments posted: 1**`nReviewing files that changed from the base of the PR and between $base and $Sha.`n$marker"
}
function Add-Comment([string]$Body, [string]$Login = 'coderabbitai[bot]', [string]$Type = 'Bot', [object]$At = $null) {
    $time = if ($At) { $At } else { Get-Now }
    $item = [ordered]@{ id = (Next-Id); node_id = 'C'; user = @{ login = $Login; type = $Type }; created_at = (Stamp $time)
        updated_at = (Stamp $time); body = $Body; html_url = "https://example.invalid/c/$($global:W.nextId)" }
    $global:W.comments.Add($item) | Out-Null
    return $item
}
function Set-Head([string]$Sha) { $global:W.head = $Sha; $global:W.arrival[$Sha] = Get-Now }
function At([int]$Seconds, [scriptblock]$Action) { $global:W.events.Add(@{ at = $t0.AddSeconds($Seconds); action = $Action; done = $false }) | Out-Null }

function Invoke-Events {
    foreach ($event in $global:W.events) {
        if (-not $event.done -and (Get-Now) -ge $event.at) { $event.done = $true; & $event.action }
    }
}

function ConvertTo-Pages([object[]]$Items) {
    # Two items per page exercises --paginate --slurp flattening.
    $pages = @()
    for ($i = 0; $i -lt $Items.Count; $i += 2) {
        $slice = @($Items[$i..([Math]::Min($i + 1, $Items.Count - 1))])
        $pages += '[' + (($slice | ForEach-Object { ConvertTo-Json -InputObject $_ -Compress -Depth 10 }) -join ',') + ']'
    }
    if ($pages.Count -eq 0) { $pages = @('[]') }
    return '[' + ($pages -join ',') + ']'
}

function global:gh {
    $global:LASTEXITCODE = 0
    Invoke-Events
    $a = @($args)
    $joined = $a -join ' '
    $w = $global:W
    if ($a -contains 'POST') {
        $body = (($a | Where-Object { $_ -like 'body=*' }) -replace '^body=', '')
        if ($w.failPost -and $w.failPost.body -eq $body) {
            $failure = $w.failPost
            $w.failPost = $null
            # A lost response can still leave the comment created on GitHub.
            if ($failure.created) { Add-Comment $body 'alice' 'User' | Out-Null; $w.posts.Add([pscustomobject]@{ body = $body; head = $w.head; at = (Get-Now) }) | Out-Null }
            $global:LASTEXITCODE = 1
            return $failure.message
        }
        $w.posts.Add([pscustomobject]@{ body = $body; head = $w.head; at = (Get-Now) }) | Out-Null
        $item = Add-Comment $body 'alice' 'User'
        if ($w.onPost) { & $w.onPost $body }
        return (ConvertTo-Json -InputObject $item -Compress -Depth 10)
    }
    $endpoint = $a[-1]
    $r = "repos/$($global:CrMockRepo)"
    $n = $global:CrMockPr
    switch -Regex ($endpoint) {
        "^$r/pulls/$n$" { return (@{ head = @{ sha = $w.head }; state = $w.state; draft = $w.draft; merged = $false } | ConvertTo-Json -Compress) }
        "^$r/issues/$n/comments" { return (ConvertTo-Pages @($w.comments)) }
        "^$r/pulls/$n/reviews" { return (ConvertTo-Pages @($w.reviews)) }
        "^$r/commits/([0-9a-f]{40})/statuses" {
            $list = if ($w.statuses.ContainsKey($Matches[1])) { @($w.statuses[$Matches[1]]) } else { @() }
            return (ConvertTo-Pages $list)
        }
        "^$r/commits/([0-9a-f]{40})/check-suites" {
            $w.suiteReads++
            $suites = if ($w.arrival.ContainsKey($Matches[1])) { @(@{ created_at = (Stamp $w.arrival[$Matches[1]]) }) } else { @() }
            return (@{ check_suites = $suites } | ConvertTo-Json -Compress -Depth 5)
        }
        "^$r/commits/([0-9a-f]{40})$" { return (@{ commit = @{ committer = @{ date = (Stamp $global:CodeRabbitFakeClock.Now.AddDays(-1)) } } } | ConvertTo-Json -Compress -Depth 5) }
    }
    throw "Unexpected gh call: $joined"
}

$resolved = Get-Command gh
if ($resolved.CommandType -ne 'Function') {
    Write-Output "setup failed: gh resolves to $($resolved.CommandType) $($resolved.Source); refusing to call real GitHub"
    exit 2
}

$script:failures = 0
$script:passed = 0
function Invoke-Wait([hashtable]$Extra = @{}) {
    $stateDir = Join-Path ([IO.Path]::GetTempPath()) ("cr-wait-test-" + [guid]::NewGuid().ToString('N'))
    $script:lastStateDir = $stateDir
    $params = @{ Repo = $repo; PrNumber = $pr; StateDir = $stateDir; PollSeconds = 10; AutoWaitSeconds = 120; ReviewWaitSeconds = 600 }
    foreach ($key in $Extra.Keys) { $params[$key] = $Extra[$key] }
    $lines = @(& $waitScript @params 6>$null)
    return [pscustomobject]@{ exit = $LASTEXITCODE; result = ([string]$lines[-1] | ConvertFrom-Json) }
}
function Invoke-Quota([hashtable]$Extra = @{}) {
    $params = @{ Repo = $repo; PrNumber = $pr; ExpectedHead = $global:W.head; PollSeconds = 10; ReplyTimeoutSeconds = 60 }
    foreach ($key in $Extra.Keys) { $params[$key] = $Extra[$key] }
    $lines = @(& $quotaScript @params 6>&1)
    return [pscustomobject]@{ exit = $LASTEXITCODE; result = ([string]$lines[-1] | ConvertFrom-Json); log = ($lines | ForEach-Object { "$_" }) -join "`n" }
}
function Test-Case([string]$Name, [scriptblock]$Body) {
    try { & $Body; $script:passed++; Write-Output "ok   $Name" }
    catch { $script:failures++; Write-Output "FAIL $Name :: $($_.Exception.Message)" }
    finally { if ($script:lastStateDir) { Remove-TestStateDir $script:lastStateDir } }
}
function Remove-TestStateDir([string]$Path) {
    $target = [IO.Path]::GetFullPath($Path)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\', '/')
    if ([IO.Path]::GetDirectoryName($target) -ne $tempRoot -or
        [IO.Path]::GetFileName($target) -notmatch '^cr-(?:wait-test|ledger)-[0-9a-f]{32}$') {
        throw "Refusing to remove unexpected test state directory: $target"
    }
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
}
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
# The leading comma keeps a one-element result an array; PowerShell 5.1 has no .Count on a lone PSCustomObject.
function Posts([string]$Body) { return , @($global:W.posts | Where-Object { $_.body -eq $Body }) }
function Reply-OnProbe([string[]]$Replies) {
    $global:CrReplyQueue = New-Object System.Collections.Queue
    foreach ($reply in $Replies) { $global:CrReplyQueue.Enqueue($reply) }
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit' -and $global:CrReplyQueue.Count -gt 0) {
            Add-Comment "$planText $($global:CrReplyQueue.Dequeue())" | Out-Null
        }
    }
}

Test-Case 'quota wait displays local date, offset and time remaining' {
    New-World
    Reply-OnProbe @('More reviews will be available in 2 minutes.', 'Reviews are available now.')
    $r = Invoke-Quota @{ MaxWaitSeconds = 300 }
    $expected = $t0.AddSeconds(150).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss zzz', [Globalization.CultureInfo]::InvariantCulture)
    Assert-True ($r.log.Contains("next query at $expected (local time; in 00:02:30)")) 'next query must use the local timezone with a countdown'
    Assert-True ($r.log.Contains('review completion time is unknown')) 'do not present quota recheck as review completion'
    Assert-True ($r.result.state -eq 'AVAILABLE' -and (Get-Now) -eq $t0.AddSeconds(150)) 'display formatting must not change retry scheduling'
}

Test-Case 'stopped quota wait still displays suggested local retry time' {
    New-World
    Reply-OnProbe @('More reviews will be available in 2 minutes.')
    $r = Invoke-Quota @{ MaxWaitSeconds = 60 }
    $expected = $t0.AddSeconds(150).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss zzz', [Globalization.CultureInfo]::InvariantCulture)
    Assert-True ($r.log.Contains("suggested next query at $expected (local time; in 00:02:30); this wait has stopped")) 'stopped wait must give a readable retry time'
    Assert-True ($r.result.nextCheckAt -eq $t0.AddSeconds(150).ToString('o')) 'JSON timestamp must keep its protocol format'
    Assert-True ($r.result.state -eq 'UNAVAILABLE' -and (Get-Now) -eq $t0) 'stopped wait must not keep sleeping'
}

Test-Case 'covered head ends REVIEWED without any write' {
    New-World
    Add-Status $headA 'success' 'Review completed'
    Add-CoveringReview $headA
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 0 -and $r.result.state -eq 'REVIEWED') "got $($r.result.state)"
    Assert-True ($global:W.posts.Count -eq 0) 'no GitHub write expected'
}

Test-Case 'manual review: quota query, one trigger, then the review' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit') { Add-Comment "$planText Reviews are available now." | Out-Null }
        if ($body -eq '@coderabbitai review') {
            $global:W.events.Add(@{ at = (Get-Now).AddSeconds(30); done = $false; action = { Add-Status $headA 'pending' 'Review in progress' } }) | Out-Null
            $global:W.events.Add(@{ at = (Get-Now).AddSeconds(300); done = $false; action = { Add-CoveringReview $headA; Add-Status $headA 'success' 'Review completed' } }) | Out-Null
        }
    }
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 0 -and $r.result.state -eq 'REVIEWED') "got $($r.result.state) $($r.result.detail)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 1) 'exactly one review trigger'
    Assert-True ($global:W.posts[0].body -eq '@coderabbitai rate limit') 'quota query must come first'
}

Test-Case 'commit-time fallback is refreshed until an observed head arrival exists' {
    New-World
    $global:W.arrival.Remove($headA)
    Add-Comment '@coderabbitai review' 'alice' 'User' $t0.AddSeconds(-10) | Out-Null
    At 20 { $global:W.arrival[$headA] = $t0 }
    $r = Invoke-Wait @{ NoTrigger = $true }
    Assert-True ($r.result.state -eq 'NO_SIGNAL') "old-head trigger must be discarded: $($r.result.state)"
    Assert-True ($global:W.suiteReads -eq 3) "fallback is re-read, then observed arrival is cached: $($global:W.suiteReads) reads"
    Assert-True ($global:W.posts.Count -eq 0) 'read-only wait must not post'
}

Test-Case 'thread-reply review on the head does not count as a review' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Add-Review $headA ''
    $r = Invoke-Wait @{ NoTrigger = $true }
    Assert-True ($r.exit -eq 9 -and $r.result.state -eq 'TRIGGER_REQUIRED') "got $($r.result.state)"
    Assert-True ($global:W.posts.Count -eq 0) '-NoTrigger must not write'
}

Test-Case 'rate limit countdown is re-queried, never assumed' {
    New-World
    Add-Status $headA 'success' 'Review rate limited'
    Reply-OnProbe @('More reviews will be available in 2 minutes.', 'More reviews will be available in 1 minute.', 'Reviews are available now.')
    $r = Invoke-Wait
    $probes = Posts '@coderabbitai rate limit'
    $triggers = Posts '@coderabbitai review'
    Assert-True ($probes.Count -eq 3) "expected 3 quota queries, got $($probes.Count)"
    Assert-True (($probes[1].at - $probes[0].at).TotalSeconds -ge 120) 'second query must wait for the countdown'
    Assert-True ($triggers.Count -eq 1 -and $global:W.posts.IndexOf($triggers[0]) -gt $global:W.posts.IndexOf($probes[2])) 'trigger only after the positive reply'
    Assert-True ($r.result.state -eq 'TRIGGER_EXHAUSTED' -and $r.exit -eq 8) "no review arrives, so the single trigger is exhausted: $($r.result.state)"
}

Test-Case 'quota never confirmed within budget ends RATE_LIMITED without trigger' {
    New-World
    Add-Status $headA 'success' 'Review rate limited'
    Reply-OnProbe @('More reviews will be available in 1 minute.', 'More reviews will be available in 1 minute.', 'More reviews will be available in 1 minute.')
    $r = Invoke-Wait @{ MaxQuotaWaitSeconds = 200 }
    Assert-True ($r.exit -eq 7 -and $r.result.state -eq 'RATE_LIMITED') "got $($r.result.state)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 0) 'no trigger without confirmed quota'
}

Test-Case 'head change while waiting discards the old session' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit') { Add-Comment "$planText Reviews are available now." | Out-Null }
        if ($body -eq '@coderabbitai review' -and $global:W.head -eq $headB) {
            $global:W.events.Add(@{ at = (Get-Now).AddSeconds(200); done = $false; action = { Add-CoveringReview $headB; Add-Status $headB 'success' 'Review completed' } }) | Out-Null
        }
        if ($body -eq '@coderabbitai review' -and $global:W.head -eq $headA) {
            $global:W.events.Add(@{ at = (Get-Now).AddSeconds(60); done = $false; action = {
                Set-Head $headB; Add-Status $headB 'success' 'Review skipped: manual review required for this OSS repository' } }) | Out-Null
        }
    }
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 0 -and $r.result.state -eq 'REVIEWED' -and $r.result.head -eq $headB) "got $($r.result.state) on $($r.result.head)"
    $triggers = Posts '@coderabbitai review'
    Assert-True ($triggers.Count -eq 2 -and $triggers[0].head -eq $headA -and $triggers[1].head -eq $headB) 'one trigger per head'
}

Test-Case 'head change during the quota wait never triggers the old head' {
    New-World
    Add-Status $headA 'success' 'Review rate limited'
    Reply-OnProbe @('More reviews will be available in 5 minutes.')
    At 100 { Set-Head $headB; Add-Status $headB 'success' 'Review completed'; Add-CoveringReview $headB }
    $r = Invoke-Wait
    Assert-True ($r.result.state -eq 'REVIEWED' -and $r.result.head -eq $headB) "got $($r.result.state) on $($r.result.head)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 0) 'the old head must not be triggered'
}

Test-Case 'StopOnHeadChange reports HEAD_CHANGED' {
    New-World
    Add-Status $headA 'pending' 'Review in progress'
    At 30 { Set-Head $headB }
    $r = Invoke-Wait @{ StopOnHeadChange = $true }
    Assert-True ($r.exit -eq 11 -and $r.result.state -eq 'HEAD_CHANGED' -and $r.result.head -eq $headB) "got $($r.result.state)"
}

Test-Case 'an existing trigger for this head blocks a second one' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Add-Comment '@coderabbitai review' 'bob' 'User' $t0.AddSeconds(5) | Out-Null
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 8 -and $r.result.state -eq 'TRIGGER_EXHAUSTED') "got $($r.result.state)"
    Assert-True ($global:W.posts.Count -eq 0) 'no write expected'
}

Test-Case 'a trigger posted before the head arrived does not count' {
    New-World
    Add-Comment '@coderabbitai review' 'bob' 'User' $t0.AddSeconds(-60) | Out-Null
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Reply-OnProbe @('Reviews are available now.')
    $r = Invoke-Wait @{ ReviewWaitSeconds = 60 }
    Assert-True ((Posts '@coderabbitai review').Count -eq 1) 'a new head gets its own trigger'
    Assert-True ($r.result.state -eq 'TRIGGER_EXHAUSTED') "got $($r.result.state)"
}

Test-Case 'the local ledger blocks a second trigger across runs' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Reply-OnProbe @('Reviews are available now.')
    $stateDir = Join-Path ([IO.Path]::GetTempPath()) ("cr-ledger-" + [guid]::NewGuid().ToString('N'))
    try {
        $first = Invoke-Wait @{ StateDir = $stateDir; ReviewWaitSeconds = 60 }
        # Simulate a lost remote comment: only the ledger remembers the first trigger.
        $global:W.comments.Clear()
        $second = Invoke-Wait @{ StateDir = $stateDir; ReviewWaitSeconds = 60 }
        Assert-True ((Posts '@coderabbitai review').Count -eq 1) 'ledger must prevent a second trigger'
        Assert-True ($first.result.state -eq 'TRIGGER_EXHAUSTED' -and $second.result.state -eq 'TRIGGER_EXHAUSTED') "got $($first.result.state)/$($second.result.state)"
    } finally { Remove-TestStateDir $stateDir }
}

function Invoke-FailedSend([hashtable]$Failure) {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Reply-OnProbe @('Reviews are available now.', 'Reviews are available now.')
    $Failure.body = '@coderabbitai review'
    $global:W.failPost = $Failure
    $stateDir = Join-Path ([IO.Path]::GetTempPath()) ("cr-ledger-" + [guid]::NewGuid().ToString('N'))
    try {
        $first = Invoke-Wait @{ StateDir = $stateDir; ReviewWaitSeconds = 60 }
        $ledger = @(Get-ChildItem $stateDir -ErrorAction SilentlyContinue)
        $second = Invoke-Wait @{ StateDir = $stateDir; ReviewWaitSeconds = 60 }
        return [pscustomobject]@{ first = $first; second = $second; ledger = $ledger; triggers = (Posts '@coderabbitai review') }
    } finally { Remove-TestStateDir $stateDir }
}

Test-Case 'an explicit 4xx send failure releases the ledger for a later retry' {
    $r = Invoke-FailedSend @{ message = 'HTTP 422: Validation Failed'; created = $false }
    Assert-True ($r.first.exit -eq 2 -and $r.ledger.Count -eq 0) "got $($r.first.result.state), ledger $($r.ledger.Count)"
    Assert-True ($r.triggers.Count -eq 1 -and $r.second.result.state -eq 'TRIGGER_EXHAUSTED') "second run: $($r.second.result.state), triggers $($r.triggers.Count)"
}

Test-Case 'an uncertain send failure that did create the comment continues as triggered' {
    $r = Invoke-FailedSend @{ message = 'HTTP 502: Bad Gateway'; created = $true }
    Assert-True ($r.first.result.state -eq 'TRIGGER_EXHAUSTED' -and $r.ledger.Count -eq 1) "got $($r.first.result.state), ledger $($r.ledger.Count)"
    Assert-True ($r.triggers.Count -eq 1) "no second trigger, got $($r.triggers.Count)"
}

Test-Case 'an uncertain send failure without a visible comment keeps blocking the head' {
    $r = Invoke-FailedSend @{ message = 'connection reset by peer'; created = $false }
    Assert-True ($r.first.exit -eq 2 -and $r.ledger.Count -eq 1) "got $($r.first.result.state), ledger $($r.ledger.Count)"
    Assert-True ($r.triggers.Count -eq 0 -and $r.second.result.state -eq 'TRIGGER_EXHAUSTED') "second run: $($r.second.result.state), triggers $($r.triggers.Count)"
}

Test-Case 'confirming an uncertain send respects the overall deadline' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Reply-OnProbe @('Reviews are available now.')
    $global:W.failPost = @{ body = '@coderabbitai review'; message = 'HTTP 504: Gateway Timeout'; created = $false }
    $start = Get-Now
    $r = Invoke-Wait @{ TimeoutSeconds = 60; PollSeconds = 300 }
    $elapsed = ((Get-Now) - $start).TotalSeconds
    Assert-True ($r.exit -eq 2 -and $elapsed -le 60) "got $($r.result.state) after $elapsed s"
}

Test-Case 'a head change while confirming an uncertain send starts a new session' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    Reply-OnProbe @('Reviews are available now.', 'Reviews are available now.')
    $global:W.failPost = @{ body = '@coderabbitai review'; message = 'HTTP 502: Bad Gateway'; created = $false }
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit' -and $global:CrReplyQueue.Count -gt 0) {
            Add-Comment "$planText $($global:CrReplyQueue.Dequeue())" | Out-Null
            if ($global:W.head -eq $headA) {
                $global:W.events.Add(@{ at = (Get-Now).AddSeconds(5); done = $false; action = {
                    Set-Head $headB; Add-Status $headB 'success' 'Review skipped: manual review required for this OSS repository' } }) | Out-Null
            }
        }
    }
    $r = Invoke-Wait @{ ReviewWaitSeconds = 60 }
    $triggers = Posts '@coderabbitai review'
    Assert-True ($r.result.head -eq $headB -and $r.result.state -eq 'TRIGGER_EXHAUSTED') "got $($r.result.state) on $($r.result.head)"
    Assert-True ($triggers.Count -eq 1 -and $triggers[0].head -eq $headB) 'only the new head gets a confirmed trigger'
}

Test-Case 'a review landing during the quota wait cancels the trigger' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit') {
            Add-Comment "$planText Reviews are available now." | Out-Null
            Add-CoveringReview $headA
            Add-Status $headA 'success' 'Review completed'
        }
    }
    $r = Invoke-Wait
    Assert-True ($r.result.state -eq 'REVIEWED') "got $($r.result.state)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 0) 'no trigger after the head was reviewed'
}

Test-Case 'trigger answered by a rate limit stops instead of re-triggering' {
    New-World
    Add-Status $headA 'success' 'Review skipped: manual review required for this OSS repository'
    $global:W.onPost = {
        param($body)
        if ($body -eq '@coderabbitai rate limit') { Add-Comment "$planText Reviews are available now." | Out-Null }
        if ($body -eq '@coderabbitai review') {
            $global:W.events.Add(@{ at = (Get-Now).AddSeconds(5); done = $false; action = { Add-Status $headA 'success' 'Review rate limited' } }) | Out-Null
        }
    }
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 7 -and $r.result.state -eq 'TRIGGER_RATE_LIMITED') "got $($r.result.state)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 1) 'single trigger only'
}

Test-Case 'a quota query error is reported as ERROR, not as a rate limit' {
    New-World
    Add-Status $headA 'success' 'Review rate limited'
    $global:W.failPost = @{ body = '@coderabbitai rate limit'; message = 'HTTP 500: Internal Server Error'; created = $false }
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 2 -and $r.result.state -eq 'ERROR' -and $r.result.error -match 'HTTP 500') "got $($r.result.state) $($r.result.error)"
    Assert-True ((Posts '@coderabbitai review').Count -eq 0) 'no trigger after a quota error'
}

Test-Case 'draft and closed PRs stop without writes' {
    New-World
    $global:W.draft = $true
    $draft = Invoke-Wait
    New-World
    $global:W.state = 'closed'
    $closed = Invoke-Wait
    Assert-True ($draft.exit -eq 3 -and $closed.exit -eq 4) "got $($draft.exit)/$($closed.exit)"
    Assert-True ($global:W.posts.Count -eq 0) 'no write expected'
}

Test-Case 'no CodeRabbit signal ends NO_SIGNAL instead of a blind trigger' {
    New-World
    Add-Status $headA 'success' 'Review completed' 'mallory' 'User'
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 6 -and $r.result.state -eq 'NO_SIGNAL') "got $($r.result.state)"
    Assert-True ($global:W.posts.Count -eq 0) 'no write expected'
}

Test-Case 'completed status without a review record ends UNCONFIRMED' {
    New-World
    Add-Status $headA 'success' 'Review completed'
    Add-Review $headA ''
    $r = Invoke-Wait
    Assert-True ($r.exit -eq 6 -and $r.result.state -eq 'UNCONFIRMED') "got $($r.result.state)"
}

Test-Case 'rate-limit script: negative and unknown replies are not availability' {
    New-World
    Reply-OnProbe @('No reviews are available now.', 'Reviews are available now.')
    $r = Invoke-Quota @{ DefaultRecheckSeconds = 60 }
    Assert-True ($r.exit -eq 0 -and $r.result.state -eq 'AVAILABLE') "got $($r.result.state)"
    Assert-True ((Posts '@coderabbitai rate limit').Count -eq 2 -and (Posts '@coderabbitai review').Count -eq 0) 'two queries, never a review'

    New-World
    Reply-OnProbe @('Thanks for the update!')
    $unknown = Invoke-Quota
    Assert-True ($unknown.exit -eq 12 -and $unknown.result.state -eq 'UNKNOWN_REPLY') "got $($unknown.result.state)"

    New-World
    $global:W.onPost = { param($body) Add-Comment "$planText Reviews are available now." 'mallory' 'User' | Out-Null }
    $spoofed = Invoke-Quota
    Assert-True ($spoofed.exit -eq 12 -and $spoofed.result.state -eq 'NO_REPLY') "a human reply is not quota: $($spoofed.result.state)"
}

Test-Case 'rate-limit script: old replies before the query are ignored' {
    New-World
    Add-Comment "$planText Reviews are available now." | Out-Null
    Reply-OnProbe @('More reviews will be available in 10 minutes.')
    $r = Invoke-Quota @{ MaxWaitSeconds = 300 }
    Assert-True ($r.exit -eq 7 -and $r.result.state -eq 'UNAVAILABLE') "got $($r.result.state)"
}

Test-Case 'rate-limit script: head change while waiting' {
    New-World
    Reply-OnProbe @('More reviews will be available in 5 minutes.')
    At 60 { Set-Head $headB }
    $r = Invoke-Quota
    Assert-True ($r.exit -eq 11 -and $r.result.state -eq 'HEAD_CHANGED' -and $r.result.actualHead -eq $headB) "got $($r.result.state)"
    Assert-True ((Posts '@coderabbitai rate limit').Count -eq 1) 'no query for a stale head'
}

Remove-Variable -Name CodeRabbitFakeClock -Scope Global -ErrorAction SilentlyContinue
Remove-Item function:global:gh -ErrorAction SilentlyContinue
Write-Output "$script:passed scenario(s) passed, $script:failures failed."
if ($script:failures -gt 0) { exit 1 }
