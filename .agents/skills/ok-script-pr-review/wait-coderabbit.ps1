param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [ValidateRange(1, 2147483647)][int]$IntervalSeconds = 20,
    [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 900,
    [string]$SinceCommit = "",
    # 等到「可合并」状态：CodeRabbit status=success 且最新评审不是 CHANGES_REQUESTED。
    # 不加此开关时沿用旧行为：status=success 或出现覆盖 head 的评审条目即返回。
    [switch]$WaitMergeReady,
    # 完成后将 coderabbitai 历史 CHANGES_REQUESTED 评审全部 dismiss（需管理员权限）。
    # 背景：COMMENTED/APPROVED 之外，旧的 CHANGES_REQUESTED 会一直阻塞合并，
    # 而 GitHub 规则下仅靠对方评论无法清除该状态。
    [switch]$DismissChangesRequested,
    # 结束时列出本轮新增的 CodeRabbit 顶层（非回复）评审意见，避免人工按时间戳过滤漏看
    [switch]$ListNewComments
)

# wait-coderabbit.ps1 — 轮询等待 CodeRabbit 完成对最新 commit 的 review。
#
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223 -SinceCommit <旧sha>
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 223 -WaitMergeReady -DismissChangesRequested -ListNewComments
#
# 退出码：
#   0 = 完成（输出评审摘要；WaitMergeReady 时表示已可合并）
#   1 = 超时仍未等到（可重跑；pending 长期不动时提示重新触发）
#   2 = 异常（API 失败、认证错误等）
#   3 = 评审完成但最新评审为 CHANGES_REQUESTED（会阻塞合并；
#       可用 -DismissChangesRequested 清除，或让 reviewer 重新提交评审）
#
# 判断依据（两者取其一即认为完成；WaitMergeReady 时两者都要求）：
#   1. CodeRabbit 在 head commit 上的 commit status 变为 success
#      （增量评审无新意见时不发布新条目，只翻 status）
#   2. reviews 列表出现 commit_id 等于当前 headRefOid 的新条目
#
# 编码注意：gh 输出是 UTF-8（评审 body 含中文），PowerShell 默认按系统 ANSI(GBK)
# 解码会把 JSON 弄坏导致 ConvertFrom-Json 报错；因此除强制 UTF-8 外，
# 所有结构化读取一律走服务端 --jq 输出 TSV，PowerShell 只按行拆字段。

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

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
    # 最新一条 CodeRabbit review；服务端过滤并输出 TSV（id|state|commit_id|submitted_at）
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/reviews", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot") | [.id, .state, (.commit_id // ""), .submitted_at] | @tsv')
    if (-not $out) { return $null }
    $latest = $null
    foreach ($ln in ($out -split "`n")) {
        $f = $ln.Trim() -split "`t"
        if ($f.Count -lt 4 -or -not $f[3]) { continue }
        if ($null -eq $latest -or [datetime]$f[3] -gt [datetime]$latest.submitted_at) {
            $latest = [pscustomobject]@{ id = $f[0]; state = $f[1]; commit_id = $f[2]; submitted_at = $f[3] }
        }
    }
    return $latest
}

function Get-CodeRabbitStatus {
    # 返回 head 上 CodeRabbit commit status 的 state/description/created_at；无则 $null
    $tsv = Invoke-GhChecked @("api", "repos/$Repo/commits/$headSha/status", "--jq",
        '.statuses[] | select(.context == "CodeRabbit") | [.state, .description, .created_at] | @tsv')
    if (-not $tsv) { return $null }
    $first = ($tsv -split "`n")[0].Trim() -split "`t"
    if ($first.Count -lt 3) { return $null }
    return [pscustomobject]@{ state = $first[0]; description = $first[1]; created_at = $first[2] }
}

function Get-NewTopLevelComments {
    # 列出 id 大于基线的 CodeRabbit 顶层评审意见（id|path|line）
    param([long]$BaselineId)
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/comments", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .in_reply_to_id == null) | [.id, .path, (.line // .original_line // 0)] | @tsv')
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
        '[.[] | select(.user.login == "coderabbitai[bot]") | .id] | max // 0')
    if (-not $out) { return 0 }
    return [long](([string]$out | Select-Object -Last 1).Trim())
}

function Hide-ChangesRequestedReviews {
    # 以管理员身份 dismiss coderabbitai 的全部 CHANGES_REQUESTED 评审，
    # 解除其对合并的阻塞（COMMENTED 无法清除该状态，只有 approve 或 dismiss 可以）
    $out = Invoke-GhChecked @("api", "--paginate", "repos/$Repo/pulls/$PrNumber/reviews", "--jq",
        '.[] | select(.user.login == "coderabbitai[bot]" and .state == "CHANGES_REQUESTED") | .id')
    if (-not $out) { Write-Host "没有需要 dismiss 的 CHANGES_REQUESTED 评审"; return }
    foreach ($rid in (($out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        Invoke-GhChecked @("api", "-X", "PUT", "repos/$Repo/pulls/$PrNumber/reviews/$rid/dismissals",
            "-f", "message=意见已在后续提交中处理完毕（见各线程回复），关闭过期的阻塞状态") | Out-Null
        Write-Host "已 dismiss CHANGES_REQUESTED review #$rid"
    }
}

function Get-ReviewSummary {
    param([object]$Review)
    if (-not $Review) { return "" }
    $cid = if ($Review.commit_id) { $Review.commit_id.Substring(0, [Math]::Min(7, $Review.commit_id.Length)) } else { "?" }
    return "[$($Review.state)] $($Review.submitted_at) commit=$cid"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$sinceDate = ""
if ($SinceCommit) {
    try { $sinceDate = Invoke-GhChecked @("api", "repos/$Repo/commits/$SinceCommit", "--jq", ".commit.committer.date") } catch { Write-Host "警告: 无法解析 $SinceCommit 的提交时间 ($($_.Exception.Message))" }
}
$baselineMaxId = Get-MaxCommentId

try {
    $headSha = Get-HeadSha
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
        }

        $status = Get-CodeRabbitStatus
        $latest = Get-LatestReview
        $entryCoversHead = ($latest -and $latest.commit_id -eq $headSha)
        $statusDone = ($status -and $status.state -eq "success")
        $changesRequested = ($latest -and $latest.state -eq "CHANGES_REQUESTED")

        if (-not $statusDone -and $status) {
            if ($status.state -eq "pending" -and -not $pendingSince) { $pendingSince = Get-Date }
            if ($status.state -ne "pending") { $pendingSince = $null; $stallHinted = $false }
            if ($pendingSince -and ((Get-Date) - $pendingSince).TotalMinutes -ge 5 -and -not $stallHinted) {
                Write-Host "提示: status 持续 pending 超过 5 分钟，可能限流；可在 PR 里重新评论 '@coderabbitai review' 后重跑本脚本"
                $stallHinted = $true
            }
            Write-Host "等待中... status=$($status.state) desc='$($status.description)'；最新评审 $(Get-ReviewSummary $latest)"
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

        if ($SinceCommit -and $sinceDate) {
            if ($statusDone -and $status.created_at -lt $sinceDate) { Write-Host "注意: status 早于 $SinceCommit，可能是 force-push 前的旧结果，请人工确认" }
            if ($latest -and $latest.submitted_at -lt $sinceDate) { Write-Host "注意: 评审早于 $SinceCommit，可能是 force-push 前的旧结果，请人工确认" }
        }

        if ($DismissChangesRequested -and $changesRequested) { Hide-ChangesRequestedReviews; $latest = Get-LatestReview }

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

        if ($WaitMergeReady -and $latest -and $latest.state -eq "CHANGES_REQUESTED") {
            Write-Host "阻塞: 最新评审仍是 CHANGES_REQUESTED（COMMENTED 无法清除该状态）。可加 -DismissChangesRequested 重跑，或由维护者在 UI 关闭。"
            exit 3
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
