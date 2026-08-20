param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [ValidateRange(1, 2147483647)][int]$IntervalSeconds = 20,
    [ValidateRange(1, 2147483647)][int]$TimeoutSeconds = 900,
    [string]$SinceCommit = ""
)

# wait-coderabbit.ps1 — 短间隔轮询等待 CodeRabbit 完成对最新 commit 的 review。
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 203
# 退出码：
#   0 = 已发现覆盖当前 head 的新 review（输出 review 摘要）
#   1 = 超时仍未等到（可重跑）
#   2 = 异常（API 失败、认证/限流错误等）
#
# 判断逻辑：CodeRabbit 完成对某 commit 的 review 后，reviews 列表最新一条的
# commit_id 会等于该 commit 的 SHA。若最新 review 的 commit_id 等于 PR 当前
# headRefOid，说明「本次提交已有新 review」；否则继续短间隔轮询。
# 注意：force-push 后 GitHub 不重新触发 CodeRabbit 时，reviews 的 commit_id
# 会停留在旧 SHA，此时可配合 @coderabbitai review 触发词（见 SKILL.md 第 2 节）。

$ErrorActionPreference = "Stop"

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
    # 最新一条 CodeRabbit review（含 commit_id 与 submitted_at）；无则返回 $null
    # --paginate --slurp：合并所有分页为一个大数组；--jq 不能与 --slurp 同用，
    # 过滤与排序在 PowerShell 端做
    $json = Invoke-GhChecked @("api", "--paginate", "--slurp", "repos/$Repo/pulls/$PrNumber/reviews")
    if (-not $json) { return $null }
    $all = $json | ConvertFrom-Json
    $codedRabbit = @($all | Where-Object { $_.user.login -eq "coderabbitai[bot]" -and $_.user.type -eq "Bot" })
    if ($codedRabbit.Count -eq 0) { return $null }
    return ($codedRabbit | Sort-Object submitted_at)[-1]
}

function Get-CodeRabbitStatus {
    # CodeRabbit 完成 review 后会在 head commit 上发布 commit status
    # （context="CodeRabbit"），即使没有发布新的 review 条目也会更新。
    # 返回 status 对象（state/description/created_at）；不存在则返回 $null。
    $s = Invoke-GhChecked @("api", "repos/$Repo/commits/$headSha/status", "--jq", '.statuses[] | select(.context == "CodeRabbit")')
    if (-not $s) { return $null }
    return ($s | ConvertFrom-Json)
}

function Get-ReviewSummary {
    param([object]$Review)
    if (-not $Review) { return "" }
    $body = [string]$Review.body
    # 抽取 "Actionable comments posted: N" 或 "Additional comments (N)" 摘要行
    $m = [regex]::Match($body, "Actionable comments posted: (\d+)")
    $n = [regex]::Match($body, "Additional comments \((\d+)\)")
    $count = if ($m.Success) { "actionable=" + $m.Groups[1].Value } elseif ($n.Success) { "additional=" + $n.Groups[1].Value } else { "no-count" }
    $cid = if ($Review.commit_id) { $Review.commit_id.Substring(0,7) } else { "?" }
    return "[$($Review.state)] $($Review.submitted_at) commit=$cid $count"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$sinceDate = ""
if ($SinceCommit) {
    try { $sinceDate = Invoke-GhChecked @("api", "repos/$Repo/commits/$SinceCommit", "--jq", ".commit.committer.date") } catch { Write-Host "警告: 无法解析 $SinceCommit 的提交时间 ($($_.Exception.Message))" }
}

try {
    $headSha = Get-HeadSha
    Write-Host "等待 CodeRabbit 覆盖 $($headSha.Substring(0,7)) (PR #$PrNumber)，间隔 ${IntervalSeconds}s，超时 ${TimeoutSeconds}s ..."

    while ((Get-Date) -lt $deadline) {
        # 每次轮询刷新 headSha：PR 可能被 push 新 commit
        $fresh = Get-HeadSha
        if ($fresh -and $fresh -ne $headSha) {
            Write-Host "head 已更新: $($headSha.Substring(0,7)) -> $($fresh.Substring(0,7))"
            $headSha = $fresh
        }
        # 判断依据 1（快速）：CodeRabbit commit status 已存在 = review 已完成
        # （即使无新 review 条目也会更新 status，见 SKILL.md 2b 节说明）
        $status = Get-CodeRabbitStatus
        if ($status) {
            Write-Host "OK: CodeRabbit status 已完成 → state=$($status.state) desc='$($status.description)' ($($status.created_at))"
            if ($SinceCommit -and $sinceDate -and $status.created_at -lt $sinceDate) {
                Write-Host "注意: status 早于 $SinceCommit 提交，可能仍是旧 review（force-push 场景），请人工确认"
            }
            # 若 status 存在但 reviews 无对应条目，提示可能无新意见
            $latest = Get-LatestReview
            if ($latest -and $latest.commit_id -eq $headSha) {
                Write-Host "最新 review 已覆盖当前 head → $(Get-ReviewSummary $latest)"
            } else {
                Write-Host "注意: 本次完成可能未发布新 review 条目（无新的 actionable comments），请直接查看 CodeRabbit 评论"
            }
            exit 0
        }
        # 判断依据 2（兜底）：最新 review 的 commit_id 已等于当前 head
        $latest = Get-LatestReview
        if ($latest -and $latest.commit_id -and $latest.commit_id -eq $headSha) {
            $summary = Get-ReviewSummary $latest
            Write-Host "OK: 已有覆盖当前 head 的新 review → $summary"
            if ($SinceCommit -and $sinceDate -and $latest.submitted_at -lt $sinceDate) {
                Write-Host "注意: review 早于 $SinceCommit 提交，可能仍是旧 review（force-push 场景），请人工确认"
            }
            exit 0
        }
        if ($latest -and $latest.commit_id) {
            Write-Host ("等待中... 最新 review 停在 " + $latest.commit_id.Substring(0,7) + " ($($latest.submitted_at))，目标 " + $headSha.Substring(0,7))
        } else {
            Write-Host "等待中... 尚未发现 CodeRabbit review/status"
        }
        Start-Sleep -Seconds $IntervalSeconds
    }

    Write-Host "超时: ${TimeoutSeconds}s 内未等到覆盖 $($headSha.Substring(0,7)) 的 review。"
    exit 1
}
catch {
    Write-Host "错误: $($_.Exception.Message)"
    exit 2
}