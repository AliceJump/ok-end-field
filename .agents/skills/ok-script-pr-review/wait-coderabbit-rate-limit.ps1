param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [string]$Repo = "AliceJump/ok-end-field",
    [ValidateRange(0, 2147483647)][int]$ExtraBufferSeconds = 0,
    [switch]$NoTrigger
)

# wait-coderabbit-rate-limit.ps1 — 从 CodeRabbit 全部评论解析限流等待时间，
# 推算可用时刻，到点后自动触发 review。不额外发等待评论。
# 用法：
#   .\.agents\skills\ok-script-pr-review\wait-coderabbit-rate-limit.ps1 -PrNumber 209
# 退出码：
#   0 = 已等到限流解除（默认已自动发 @coderabbitai review，除非 -NoTrigger）
#   1 = 所有 CodeRabbit 评论均不含等待时间信息（未触发，可手动处理）
#   2 = 异常
#
# 原理：等待时间不一定在最后一条评论里——裸的 "Review rate limited." 拒绝
# 不含时间，带倒计时的评论可能出现在更早的主评论或线程回复中，且 CodeRabbit
# 会编辑评论刷新倒计时。因此同时拉取两个端点的 CodeRabbit 评论：
#   issues/<n>/comments  → PR 主 conversation 评论
#   pulls/<n>/comments   → 行内线程回复
# 按编辑时间 updated_at 从新到旧逐条扫描，取最新一条含等待时间的评论：
#   "Review limit reached ... Next included review available in X minutes." /
#   "More reviews will be available in X minutes." / "Reviews are available now."
#
# 时间计算（按用户要求）：
#   - 等待分钟向上取整（ceil）
#   - 取整后再额外 +1 分钟作为缓冲
#   - 可用时刻 = 该评论编辑时间(updated_at) + (ceil(X) + 1) 分钟
#   - 若当前时间已 >= 可用时刻 → 立即触发；否则 Sleep 到可用时刻再触发
# 即：不是从"脚本运行时刻"开始等 X 分钟，而是按评论编辑时间推算绝对可用时刻，
# 编辑之后已流逝的时间会被自然扣除。

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Windows PowerShell 5.1 向原生程序传参会剥掉内嵌双引号（PS 7.3+ 已修复），
# --jq 过滤器一律不写字符串字面量，字符串经环境变量传给 gojq 的 $ENV.*。
$env:CR_LOGIN = 'coderabbitai[bot]'
$env:CR_BOT = 'Bot'

function Invoke-GhChecked {
    param([string[]]$GhArgs)
    $output = gh @GhArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "gh 命令失败 (exit $LASTEXITCODE): gh $($GhArgs -join ' ')`n$output"
    }
    return ($output | Out-String).Trim()
}

function Get-CodeRabbitComments {
    # 返回 CodeRabbit 全部评论（主 conversation + 线程回复），按 updated_at 降序。
    # --paginate 分页拉全；--jq 过滤器不含字符串字面量（PS 5.1 向原生程序传参
    # 会剥内嵌双引号），登录名/Bot 类型经 $ENV.* 传入；body 经 base64 规避
    # TSV 无法承载换行以及 PowerShell 按 ANSI 解码 JSON 的两类问题。
    $lines = @()
    foreach ($endpoint in @("repos/$Repo/issues/$PrNumber/comments", "repos/$Repo/pulls/$PrNumber/comments")) {
        $out = Invoke-GhChecked @(
            "api", "--paginate", $endpoint, "--jq",
            '[.[] | select(.user.login == $ENV.CR_LOGIN and .user.type == $ENV.CR_BOT)] | .[] | [.updated_at, (.body | @base64)] | @tsv'
        )
        if ($out) { $lines += ($out -split "`n") }
    }
    $comments = @()
    foreach ($ln in $lines) {
        $f = $ln.Trim() -split "`t"
        if ($f.Count -lt 2 -or -not $f[0] -or -not $f[1]) { continue }
        $comments += [pscustomobject]@{
            updated_at = $f[0]
            body       = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($f[1]))
        }
    }
    return @($comments | Sort-Object -Property updated_at -Descending)
}

function Get-WaitMinutes {
    param([string]$Text)
    if (-not $Text) { return $null }
    if ($Text -match "available now") { return 0.0 }
    $m = [regex]::Match($Text, "available in\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|seconds?|secs?)", "IgnoreCase")
    if (-not $m.Success) { return $null }
    $val = [double]$m.Groups[1].Value
    $unit = $m.Groups[2].Value
    if ($unit.StartsWith("second", [System.StringComparison]::OrdinalIgnoreCase) -or
        $unit.StartsWith("sec", [System.StringComparison]::OrdinalIgnoreCase)) { return ($val / 60.0) }
    return $val
}

$comments = @(Get-CodeRabbitComments)
if ($comments.Count -eq 0) {
    Write-Host "评论区没有 CodeRabbit 回复。"
    exit 1
}

# 按编辑时间从新到旧逐条找：最新评论可能是裸的 "Review rate limited."（不含
# 时间），带倒计时的评论在其之前；CodeRabbit 编辑刷新倒计时后 updated_at 也
# 随之变新，因此最新含等待时间的评论即当前真实等待时间。
$found = $null
$mins = $null
foreach ($c in $comments) {
    $mins = Get-WaitMinutes $c.body
    if ($null -ne $mins) { $found = $c; break }
}
if (-not $found) {
    Write-Host ("CodeRabbit 的 {0} 条评论均不含等待时间信息，无法推算可用时刻。" -f $comments.Count)
    exit 1
}

if ($mins -le 0) {
    Write-Host "最新含等待时间的回复: Reviews are available now（无需等待）。"
} else {
    # 向上取整 + 1 分钟缓冲
    $ceilMins = [int][Math]::Ceiling($mins)
    $bufferMins = $ceilMins + 1
    # 评论编辑时间 + (ceil(X)+1) 分钟 = 可用时刻
    # updated_at 带 UTC 标记，必须显式转 UTC，避免非 UTC 主机上与 UtcNow 比较出错
    $commentTime = [datetimeoffset]::Parse($found.updated_at).UtcDateTime
    $availableAt = $commentTime.AddMinutes($bufferMins)
    $now = [datetime]::UtcNow

    Write-Host ("最新含等待时间的评论（编辑于 {0:HH:mm:ssZ} UTC）显示需 {1:N1} 分钟，向上取整 {2} 分钟 +1 = {3} 分钟后可用" -f $commentTime, $mins, $ceilMins, $bufferMins)
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
