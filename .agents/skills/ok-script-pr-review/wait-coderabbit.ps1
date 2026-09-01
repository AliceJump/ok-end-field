param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [ValidateRange(1, 2147483647)][int]$IntervalSeconds = 20,
    [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 900,
    [string]$SinceCommit = "",
    [string]$SinceTime = "",
    # 等到「可合并」状态：CodeRabbit status=success 且不存在 CHANGES_REQUESTED。
    # 不加此开关时沿用旧行为：status=success 或出现覆盖 head 的评审条目即返回。
    [switch]$WaitMergeReady,
    # 结束时列出本轮新增的 CodeRabbit 顶层（非回复）评审意见，避免人工按时间戳过滤漏看
    [switch]$ListNewComments
)

# wait-coderabbit.ps1 — 轮询等待 CodeRabbit 完成对最新 commit 的 review。
#
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223 -SinceCommit <旧sha>
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223 -SinceTime 2026-09-01T12:34:56Z
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223 -WaitMergeReady -ListNewComments
#
# 退出码：
#   0 = 完成（输出评审摘要；WaitMergeReady 时表示已可合并）
#   1 = 超时仍未等到（可重跑；pending 长期不动时提示重新触发）
#   2 = 异常（API 失败、认证错误等）
#   3 = 评审完成但仍有 CHANGES_REQUESTED（会列出阻塞评审，不自动 dismiss）
#
# 判断依据（普通等待两者取其一；WaitMergeReady 要求 status success 后再检查阻塞评审）：
#   1. CodeRabbit 在 head commit 上的 commit status 变为 success
#      （增量评审无新意见时不发布新条目，只翻 status）
#   2. reviews 列表出现 commit_id 等于当前 headRefOid 的新条目
#
# 编码注意：gh 输出是 UTF-8（评审 body 含中文），PowerShell 默认按系统 ANSI(GBK)
# 解码会把 JSON 弄坏导致 ConvertFrom-Json 报错；因此除强制 UTF-8 外，
# 所有结构化读取一律走服务端 --jq 输出 TSV，PowerShell 只按行拆字段。

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
. (Join-Path $PSScriptRoot "wait-coderabbit-helpers.ps1")

function Invoke-GhChecked {
    param([string[]]$GhArgs)
    $output = gh @GhArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "gh 命令失败 (exit $LASTEXITCODE): gh $($GhArgs -join ' ')`n$output"
    }
    return ($output | Out-String).Trim()
}

function Get-HeadSha {
    $h = Invoke-GhChecked @("pr", "view", $PrNumber, "--repo", $Repo, "--json", "headRefOid", "--jq", ".headRefOid")
    if ($h -is [array]) { $h = $h[0] }
    return ([string]$h).Trim()
}

function Get-LatestReview {
    param([string]$CommitSha = "")
    # 最新一条 CodeRabbit review；服务端过滤并输出 TSV（id|state|commit_id|submitted_at）
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/reviews", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot") | [.id, .state, (.commit_id // ""), .submitted_at] | @tsv')
    if (-not $out) { return $null }
    $latest = $null
    foreach ($ln in ($out -split "`n")) {
        $f = $ln.Trim() -split "`t"
        if ($f.Count -lt 4 -or -not $f[3]) { continue }
        if ($CommitSha -and $f[2] -ne $CommitSha) { continue }
        if ($null -eq $latest -or [datetime]$f[3] -gt [datetime]$latest.submitted_at) {
            $latest = [pscustomobject]@{ id = $f[0]; state = $f[1]; commit_id = $f[2]; submitted_at = $f[3] }
        }
    }
    return $latest
}

function Get-CodeRabbitStatus {
    # 返回 head 上最新 CodeRabbit commit status 的 state/description/created_at；无则 $null
    $tsv = Invoke-GhChecked @("api", "repos/$Repo/commits/$headSha/status", "--jq",
        '. as $combined | [.statuses[] | select(.context == "CodeRabbit")] | sort_by(.created_at) | reverse | .[0] | if . then [$combined.sha, .state, .description, .created_at] | @tsv else empty end')
    if (-not $tsv) { return $null }
    $first = ($tsv -split "`n")[0].Trim() -split "`t"
    if ($first.Count -lt 4) { return $null }
    return [pscustomobject]@{ sha = $first[0]; state = $first[1]; description = $first[2]; created_at = $first[3] }
}

function Get-ForcePushEvent {
    param([string]$Commit)
    $repoParts = $Repo -split "/", 2
    if ($repoParts.Count -ne 2 -or -not $repoParts[0] -or -not $repoParts[1]) {
        throw "Invalid -Repo value: $Repo"
    }
    $query = 'query($owner:String!, $name:String!, $number:Int!, $endCursor:String) { repository(owner:$owner, name:$name) { pullRequest(number:$number) { timelineItems(first:100, after:$endCursor, itemTypes:[HEAD_REF_FORCE_PUSHED_EVENT]) { nodes { ... on HeadRefForcePushedEvent { beforeCommit { oid } afterCommit { oid } createdAt } } pageInfo { hasNextPage endCursor } } } } }'
    $out = Invoke-GhChecked @(
        "api", "graphql", "--paginate",
        "-f", "query=$query",
        "-f", "owner=$($repoParts[0])",
        "-f", "name=$($repoParts[1])",
        "-F", "number=$PrNumber",
        "--jq", '.data.repository.pullRequest.timelineItems.nodes[] | [(.beforeCommit.oid // ""), (.afterCommit.oid // ""), .createdAt] | @tsv'
    )
    $lines = if ($out) { @($out -split "`n") } else { @() }
    return Select-ForcePushEvent -SinceCommit $Commit -EventLines $lines
}

function Get-ChangesRequestedReviews {
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/reviews", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot" and .state == "CHANGES_REQUESTED") | [.id, .state, (.commit_id // ""), .submitted_at] | @tsv')
    if (-not $out) { return @() }
    $items = @()
    foreach ($ln in ($out -split "`n")) {
        $f = $ln.Trim() -split "`t"
        if ($f.Count -lt 4) { continue }
        $items += [pscustomobject]@{ id = $f[0]; state = $f[1]; commit_id = $f[2]; submitted_at = $f[3] }
    }
    return $items
}

function Get-NewTopLevelComments {
    # 列出 id 大于基线的 CodeRabbit 顶层评审意见（id|path|line）
    param([long]$BaselineId)
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/comments", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot" and .in_reply_to_id == null) | [.id, .path, (.line // .original_line // 0)] | @tsv')
    if (-not $out) { return @() }
    $items = @()
    foreach ($ln in ($out -split "`n")) {
        $f = $ln.Trim() -split "`t"
        if ($f.Count -lt 3) { continue }
        if ([long]$f[0] -gt $BaselineId) { $items += [pscustomobject]@{ id = [long]$f[0]; path = $f[1]; line = $f[2] } }
    }
    return $items
}

function Get-MaxCommentId {
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/comments", "--jq",
        '[.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot") | .id] | max // 0')
    if (-not $out) { return 0 }
    return [long](([string]$out | Select-Object -Last 1).Trim())
}

function Get-ReviewSummary {
    param([object]$Review)
    if (-not $Review) { return "" }
    $cid = if ($Review.commit_id) { $Review.commit_id.Substring(0, [Math]::Min(7, $Review.commit_id.Length)) } else { "?" }
    return "[$($Review.state)] $($Review.submitted_at) commit=$cid"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

try {
    if ($SinceCommit -and $SinceTime) {
        throw "-SinceCommit and -SinceTime cannot be used together"
    }
    $headSha = Get-HeadSha
    $cutoff = $null
    if ($SinceTime) {
        $cutoff = ConvertTo-UtcCutoff $SinceTime
        Write-Host "仅接受晚于显式截止时间 $($cutoff.ToString('o')) 的当前 head 结果"
    } elseif ($SinceCommit) {
        $forcePushEvent = Get-ForcePushEvent -Commit $SinceCommit
        $cutoff = $forcePushEvent.CreatedAt
        Write-Host "匹配 force-push: $($forcePushEvent.BeforeCommit.Substring(0,7)) -> $($forcePushEvent.AfterCommit.Substring(0, [Math]::Min(7, $forcePushEvent.AfterCommit.Length))) at $($cutoff.ToString('o'))"
    }
    $baselineMaxId = Get-MaxCommentId
    $mode = if ($WaitMergeReady) { "等待可合并(status=success 且非CHANGES_REQUESTED)" } else { "等待评审完成" }
    Write-Host "PR #$PrNumber 目标 $($headSha.Substring(0,7))（$mode），间隔 ${IntervalSeconds}s，超时 ${TimeoutSeconds}s ..."

    $pendingSince = $null
    $stallHinted = $false
    $noEntryPolls = 0

    while ((Get-Date) -lt $deadline) {
        # head 可能中途被 push，每次轮询刷新
        $fresh = Get-HeadSha
        if ($fresh -and $fresh -ne $headSha) {
            Write-Host "head 已更新: $($headSha.Substring(0,7)) -> $($fresh.Substring(0,7))"
            $headSha = $fresh
            $pendingSince = $null
            $noEntryPolls = 0
            if ($ListNewComments) { $baselineMaxId = Get-MaxCommentId }
        }

        $status = Get-CodeRabbitStatus
        $latest = Get-LatestReview -CommitSha $headSha
        $entryCoversHead = Test-ReviewCoversHead -Review $latest -HeadSha $headSha -Cutoff $cutoff
        $statusDone = Test-StatusCompletesHead -Status $status -HeadSha $headSha -Cutoff $cutoff
        $completionSignal = if ($WaitMergeReady) { $statusDone } else { $statusDone -or $entryCoversHead }

        if (-not $completionSignal) {
            $statusState = if ($status) { $status.state } else { "none" }
            $statusDescription = if ($status) { $status.description } else { "" }
            if ($statusState -eq "pending" -and -not $pendingSince) { $pendingSince = Get-Date }
            if ($statusState -ne "pending") { $pendingSince = $null; $stallHinted = $false }
            if ($pendingSince -and ((Get-Date) - $pendingSince).TotalMinutes -ge 5 -and -not $stallHinted) {
                Write-Host "提示: status 持续 pending 超过 5 分钟，可能限流；可在 PR 里重新评论 '@coderabbitai review' 后重跑本脚本"
                $stallHinted = $true
            }
            Write-Host "等待中... status=$statusState desc='$statusDescription'；最新评审 $(Get-ReviewSummary $latest)"
            Start-Sleep -Seconds $IntervalSeconds
            continue
        }

        # ---- status 已 success（或无 status），判定完成条件 ----
        if (-not $entryCoversHead) {
            # 无新条目：增量评审无意见的正常情况；再观察两个轮询周期确认不会出现
            $noEntryPolls++
            if ($noEntryPolls -lt 2) { Start-Sleep -Seconds $IntervalSeconds; continue }
            Write-Host "OK: status=success 且本次未发布新评审条目（无新的 actionable comments）"
        }

        Write-Host "最终评审状态: $(Get-ReviewSummary $latest)"

        if ($ListNewComments) {
            $newOnes = Get-NewTopLevelComments -BaselineId $baselineMaxId
            if ($newOnes.Count -eq 0) {
                Write-Host "本轮新增顶层评审意见: 0 条"
            } else {
                Write-Host "本轮新增顶层评审意见: $($newOnes.Count) 条"
                foreach ($c in $newOnes) { Write-Host "  #$($c.id) $($c.path):$($c.line)" }
            }
        }

        if ($WaitMergeReady) {
            $blockingReviews = @(Get-ChangesRequestedReviews)
            if ($blockingReviews.Count -gt 0) {
                Write-Host "阻塞: 仍有 $($blockingReviews.Count) 条 CHANGES_REQUESTED 评审；脚本不会自动 dismiss："
                foreach ($review in $blockingReviews) {
                    $reviewCommit = if ($review.commit_id) { $review.commit_id.Substring(0, [Math]::Min(7, $review.commit_id.Length)) } else { "?" }
                    Write-Host "  review #$($review.id) state=$($review.state) commit=$reviewCommit submitted=$($review.submitted_at)"
                }
                exit 3
            }
        }
        exit 0
    }

    Write-Host "超时: ${TimeoutSeconds}s 内未等到覆盖 $($headSha.Substring(0,7)) 的评审完成。"
    exit 1
}
catch {
    Write-Host "错误: $($_.Exception.Message)"
    exit 2
}
