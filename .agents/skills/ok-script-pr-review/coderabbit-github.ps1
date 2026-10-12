# GitHub reads/writes for the CodeRabbit scripts. All list endpoints are fully paginated.

function Assert-CrPaginationProgress {
    param([object]$PageInfo, [System.Collections.Generic.HashSet[string]]$Seen, [string]$Surface)
    if ($null -eq $PageInfo) { throw "Missing pagination info for $Surface" }
    if (-not $PageInfo.hasNextPage) { return }
    $cursor = [string]$PageInfo.endCursor
    if ([string]::IsNullOrWhiteSpace($cursor) -or -not $Seen.Add($cursor)) {
        throw "Pagination cursor did not advance for $Surface"
    }
}

function Invoke-CrGh {
    param([string[]]$GhArgs)
    $output = & gh @GhArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "gh $($GhArgs -join ' ') failed: $($output | Out-String)" }
    return (@($output) -join "`n")
}

function Get-CrJson {
    param([string]$Endpoint, [switch]$Paginate)
    $ghArgs = @('api')
    if ($Paginate) { $ghArgs += @('--paginate', '--slurp') }
    $text = Invoke-CrGh -GhArgs ($ghArgs + @($Endpoint))
    if (-not $text.Trim()) { return @() }
    $parsed = $text | ConvertFrom-Json
    if (-not $Paginate) { return $parsed }
    $items = New-Object System.Collections.Generic.List[object]
    foreach ($page in @($parsed)) { foreach ($item in @($page)) { if ($null -ne $item) { $items.Add($item) } } }
    return $items.ToArray()
}

function Get-CrPr {
    param([string]$Repo, [int]$PrNumber)
    $pr = Get-CrJson "repos/$Repo/pulls/$PrNumber"
    if ([string]$pr.head.sha -notmatch '^[0-9a-f]{40}$') { throw "Cannot read head SHA of $Repo#$PrNumber" }
    return [pscustomobject]@{ head = [string]$pr.head.sha; state = [string]$pr.state; draft = [bool]$pr.draft; merged = [bool]$pr.merged }
}

function Get-CrIssueComments {
    param([string]$Repo, [int]$PrNumber)
    return @(Get-CrJson "repos/$Repo/issues/$PrNumber/comments?per_page=100" -Paginate | ForEach-Object {
        [pscustomobject]@{
            id = [long]$_.id; node_id = [string]$_.node_id; login = [string]$_.user.login; type = [string]$_.user.type
            created = ConvertTo-CrTime $_.created_at; updated = ConvertTo-CrTime $_.updated_at; body = [string]$_.body
            url = [string]$_.html_url
        }
    })
}

function Get-CrReviews {
    param([string]$Repo, [int]$PrNumber)
    return @(Get-CrJson "repos/$Repo/pulls/$PrNumber/reviews?per_page=100" -Paginate | ForEach-Object {
        [pscustomobject]@{
            id = [long]$_.id; node_id = [string]$_.node_id; login = [string]$_.user.login; type = [string]$_.user.type
            commit_id = [string]$_.commit_id; state = [string]$_.state; created = ConvertTo-CrTime $_.submitted_at
            body = [string]$_.body; url = [string]$_.html_url
        }
    })
}

function Get-CrStatuses {
    param([string]$Repo, [string]$Sha)
    return @(Get-CrJson "repos/$Repo/commits/$Sha/statuses?per_page=100" -Paginate | ForEach-Object {
        [pscustomobject]@{
            id = [long]$_.id; context = [string]$_.context; state = [string]$_.state; description = [string]$_.description
            login = [string]$_.creator.login; type = [string]$_.creator.type; created = ConvertTo-CrTime $_.created_at
        }
    })
}

function Get-CrHeadArrival {
    param([string]$Repo, [string]$Sha, [object[]]$Statuses)
    $times = @($Statuses | ForEach-Object { $_.created })
    $suites = Get-CrJson "repos/$Repo/commits/$Sha/check-suites?per_page=100"
    $times += @($suites.check_suites | ForEach-Object { ConvertTo-CrTime $_.created_at })
    $commit = Get-CrJson "repos/$Repo/commits/$Sha"
    return Get-CodeRabbitHeadArrival -ObservedTimes $times -CommitTime (ConvertTo-CrTime $commit.commit.committer.date)
}

function New-CrIssueComment {
    param([string]$Repo, [int]$PrNumber, [string]$Body)
    $text = Invoke-CrGh -GhArgs @('api', '-X', 'POST', "repos/$Repo/issues/$PrNumber/comments", '-f', "body=$Body")
    $comment = $text | ConvertFrom-Json
    if (-not $comment.id) { throw "Cannot parse the created comment for $Repo#$PrNumber" }
    return [pscustomobject]@{ id = [long]$comment.id; created = ConvertTo-CrTime $comment.created_at; url = [string]$comment.html_url }
}
