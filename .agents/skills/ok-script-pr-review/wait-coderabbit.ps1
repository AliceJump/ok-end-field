param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [int]$IntervalSeconds = 20,
    [int]$TimeoutSeconds = 900,
    [string]$SinceCommit = ""
)

# wait-coderabbit.ps1 — 短间隔轮询等待 CodeRabbit 完成对最新 commit 的 review。
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit.ps1 -PrNumber 203
# 退出码：
#   0 = 已发现覆盖当前 head 的新 review（输出 review 摘要）
#   1 = 超时仍未等到（可重跑）
#   2 = 异常（API 失败等）
#
# 判断逻辑：CodeRabbit 完成对某 commit 的 review 后，reviews 列表最新一条的
# commit_id 会等于该 commit 的 SHA。若最新 review 的 commit_id 等于 PR 当前
# headRefOid，说明「本次提交已有新 review」；否则继续短间隔轮询。
# 注意：force-push 后 GitHub 不重新触发 CodeRabbit 时，reviews 的 commit_id
# 会停留在旧 SHA，此时可配合 @coderabbitai review 触发词（见 SKILL.md 第 2 节）。

$ErrorActionPreference = "Stop"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

function Get-HeadSha {
    $h = gh pr view $PrNumber --repo $Repo --json headRefOid --jq .headRefOid 2>$null
    if ($h -is [array]) { $h = $h[0] }
    return ([string]$h).Trim()
}

function Get-LatestReview {
    # 最新一条 CodeRabbit review（含 commit_id 与 submitted_at）；无则返回 $null
    $json = gh api "repos/$Repo/pulls/$PrNumber/reviews" --jq '[.[] | select(.user.login == "coderabbitai[bot]" and .user.type == "Bot")] | sort_by(.submitted_at) | .[-1]' 2>$null
    if (-not $json) { return $null }
    return ($json | ConvertFrom-Json)
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

$headSha = Get-HeadSha
if (-not $headSha) { Write-Error "无法获取 PR #$PrNumber 的 head SHA"; exit 2 }
if ($SinceCommit) { $sinceDate = (gh api "repos/$Repo/commits/$SinceCommit" --jq .commit.committer.date 2>$null) } else { $sinceDate = "" }

Write-Host "等待 CodeRabbit 覆盖 $($headSha.Substring(0,7)) (PR #$PrNumber)，间隔 ${IntervalSeconds}s，超时 ${TimeoutSeconds}s ..."

while ((Get-Date) -lt $deadline) {
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
        Write-Host "等待中... 尚未发现 CodeRabbit review"
    }
    Start-Sleep -Seconds $IntervalSeconds
}

Write-Host "超时: ${TimeoutSeconds}s 内未等到覆盖 $($headSha.Substring(0,7)) 的 review。"
exit 1