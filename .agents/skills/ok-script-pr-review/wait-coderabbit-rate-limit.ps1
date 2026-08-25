param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [ValidateRange(0, 2147483647)][int]$ExtraBufferSeconds = 0,
    [switch]$NoTrigger
)

# wait-coderabbit-rate-limit.ps1 — 读 CodeRabbit 最后一条回复的等待时间与
# 发布时间，综合计算可用时刻，到点后自动触发 review。不额外发评论。
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit-rate-limit.ps1 -PrNumber 209
# 退出码：
#   0 = 已等到限流解除（默认已自动发 @coderabbitai review，除非 -NoTrigger）
#   1 = 评论区无可用等待时间信息（未触发，可手动处理）
#   2 = 异常
#
# 原理：CodeRabbit 对每次限流的 review 尝试会回复一条评论：
#   "More reviews will be available in X minutes."  或
#   "Reviews are available now."
# 直接读评论区最后一条 CodeRabbit 消息即可，无需再发查询。
#
# 时间计算（按用户要求）：
#   - 等待分钟向上取整（ceil）
#   - 取整后再额外 +1 分钟作为缓冲
#   - 可用时刻 = 评论发布时间 + (ceil(X) + 1) 分钟
#   - 若当前时间已 >= 可用时刻 → 立即触发；否则 Sleep 到可用时刻再触发
# 即：不是从"脚本运行时刻"开始等 X 分钟，而是按评论时间推算绝对可用时刻，
# 评论发出后已流逝的时间会被自然扣除。

$ErrorActionPreference = "Stop"

function Invoke-GhChecked {
    param([string[]]$GhArgs)
    $output = gh @GhArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "gh 命令失败 (exit $LASTEXITCODE): gh $($GhArgs -join ' ')`n$output"
    }
    return ($output | Out-String).Trim()
}

function Get-LastCodeRabbitComment {
    # 返回最后一条 CodeRabbit 回复对象 { body, created_at }，无则 $null
    $json = Invoke-GhChecked @("api", "--paginate", "--slurp", "repos/$Repo/issues/$PrNumber/comments")
    # --slurp + --paginate 返回的是「页数组」：外层每个元素是一页评论数组，需展平
    $pages = @($json | ConvertFrom-Json)
    $all = @($pages | ForEach-Object { $_ })
    # 精确匹配 CodeRabbit 机器人身份，避免同名人类用户伪造触发文本
    $cr = @($all | Where-Object { $_.user.login -eq "coderabbitai[bot]" -and $_.user.type -eq "Bot" })
    if ($cr.Count -eq 0) { return $null }
    $last = ($cr | Sort-Object created_at)[-1]
    return @{ body = $last.body; created_at = $last.created_at }
}

function Parse-WaitMinutes {
    param([string]$Text)
    if (-not $Text) { return $null }
    if ($Text -match "available now") { return 0.0 }
    $m = [regex]::Match($Text, "available in (\d+(?:\.\d+)?) (minutes?|seconds?)")
    if (-not $m.Success) { return $null }
    $val = [double]$m.Groups[1].Value
    $unit = $m.Groups[2].Value
    if ($unit.StartsWith("second")) { return ($val / 60.0) }
    return $val
}

$last = Get-LastCodeRabbitComment
if (-not $last) {
    Write-Host "评论区没有 CodeRabbit 回复。"
    exit 1
}

$mins = Parse-WaitMinutes $last.body
if ($null -eq $mins) {
    Write-Host "最后一条 CodeRabbit 回复不含等待时间信息，内容: $($last.body.Substring(0, [Math]::Min(160, $last.body.Length)))"
    exit 1
}

if ($mins -le 0) {
    Write-Host "最后一条回复: Reviews are available now（无需等待）。"
} else {
    # 向上取整 + 1 分钟缓冲
    $ceilMins = [int][Math]::Ceiling($mins)
    $bufferMins = $ceilMins + 1
    # 评论发布时间 + (ceil(X)+1) 分钟 = 可用时刻
    # created_at 带 UTC 偏移，必须显式转 UTC，避免非 UTC 主机上与 UtcNow 比较出错
    $commentTime = [datetimeoffset]::Parse($last.created_at).UtcDateTime
    $availableAt = $commentTime.AddMinutes($bufferMins)
    $now = [datetime]::UtcNow

    Write-Host ("最后一条回复（{0:HH:mm:ssZ} UTC）显示需 {1:N1} 分钟，向上取整 {2} 分钟 +1 = {3} 分钟后可用" -f $commentTime, $mins, $ceilMins, $bufferMins)
    Write-Host ("可用时刻 ≈ {0:HH:mm:ssZ} UTC，当前 {1:HH:mm:ssZ} UTC" -f $availableAt, $now)

    if ($now -ge $availableAt) {
        Write-Host "当前已到可用时刻，立即触发。"
    } else {
        # 向上取整，避免截断导致提前触发
        $waitSec = [int][Math]::Ceiling(($availableAt - $now).TotalSeconds) + $ExtraBufferSeconds
        Write-Host ("还需等待 {0}s ..." -f $waitSec)
        Start-Sleep -Seconds $waitSec
        Write-Host "等待结束。"
    }
}

try {
    if ($NoTrigger) {
        Write-Host "（-NoTrigger，未发 review，可手动触发 @coderabbitai review）"
    } else {
        Write-Host "触发 review ..."
        Invoke-GhChecked @("pr", "comment", "$PrNumber", "--repo", $Repo, "--body", "@coderabbitai review") | Out-Null
    }
} catch {
    # gh 触发失败按约定返回异常退出码 2
    Write-Error ("触发 review 失败: {0}" -f $_.Exception.Message)
    exit 2
}
exit 0