# 逐个 tag 检出并启动 main.py 的冒烟测试脚本
#
# 判定标准（用户指定「仅进程存活」）：
#   检出 tag -> pip install -r requirements.txt -> 启动 python main.py
#   -> 等待 WaitSeconds 秒：进程仍存活 => 可启动(ok)；进程退出/启动失败 => 坏 tag(fail)
#
# 用法:
#   ./scripts/smoke_test_tags.ps1 -Tags "v1.0.18,v1.0.17" -WaitSeconds 90 `
#       -ResultFile tmp/smoke_results.csv -Python ".venv/Scripts/python.exe"
#
# 参数:
#   -Tags          逗号分隔的 tag 列表（必填）
#   -WaitSeconds   判定「可启动」的等待秒数，默认 90
#   -ResultFile    结果 CSV 输出路径，默认 smoke_results.csv
#   -BadTagsFile   坏 tag 纯文本输出路径（每行一个），默认 <ResultFile>.bad.txt
#   -Python        python 可执行文件，默认 "python"
#   -SkipInstall   跳过 pip install（本地快速调试用）

param(
    [Parameter(Mandatory = $true)]
    [string]$Tags,
    [int]$WaitSeconds = 90,
    [string]$ResultFile = "smoke_results.csv",
    [string]$BadTagsFile = "",
    [string]$Python = "python",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $BadTagsFile) {
    $BadTagsFile = "$ResultFile.bad.txt"
}

$repo = (Get-Location).Path
$tagList = @($Tags -split ',' | Where-Object { $_ -match '\S' })
$results = @()
# 注意：tmp/ 会被 git clean -fdx 删除，因此 tmpDir 必须在每次 clean 之后重新创建
$tmpDir = Join-Path $repo "tmp"

function Ensure-TmpDir {
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
}

function Invoke-Git {
    param([string]$ArgsLine)
    git $ArgsLine.Split(' ') 2>&1 | Out-Null
    return $LASTEXITCODE
}

Write-Output "== 冒烟测试开始: $($tagList.Count) 个 tag, WaitSeconds=$WaitSeconds, SkipInstall=$SkipInstall =="

foreach ($tag in $tagList) {
    $entry = [ordered]@{
        tag         = $tag
        status      = "fail"
        reason      = ""
        exit_code   = ""
        duration_s  = ""
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $safeTag = $tag -replace '[^a-zA-Z0-9]', '_'
    try {
        # 1) 检出 tag，清理工作区（含上次 tag 的未跟踪产物）
        $rc = Invoke-Git "reset --hard"
        if ($rc -ne 0) { throw "git reset --hard 失败(exit=$rc)" }
        $rc = Invoke-Git "clean -fdx"
        if ($rc -ne 0) { throw "git clean -fdx 失败(exit=$rc)" }
        # git clean 会删掉 tmp/，先重新创建
        Ensure-TmpDir
        $rc = Invoke-Git "checkout --force $tag"
        if ($rc -ne 0) { throw "git checkout $tag 失败(exit=$rc)" }

        # 2) 安装该 tag 的依赖
        if (-not $SkipInstall) {
            & $Python -m pip install --disable-pip-version-check -q -r requirements.txt *> "$tmpDir\pip_$safeTag.log"
            if ($LASTEXITCODE -ne 0) { throw "pip install 失败(exit=$LASTEXITCODE), 详见 tmp/pip_$safeTag.log" }
        }

        # 3) 启动 main.py（后台）
        $outLog = Join-Path $tmpDir "run_$safeTag.log"
        $errLog = Join-Path $tmpDir "err_$safeTag.log"
        $p = Start-Process -FilePath $Python -ArgumentList "main.py" -WorkingDirectory $repo `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog

        # 4) 等待并判定：进程存活 => 可启动
        $deadline = (Get-Date).AddSeconds($WaitSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
            if ($p.HasExited) { break }
        }

        if ($p.HasExited) {
            $entry.reason = "进程在 ${WaitSeconds}s 内退出"
            $entry.exit_code = $p.ExitCode
            $entry.status = "fail"
        } else {
            $entry.status = "ok"
            $entry.reason = "存活超过 ${WaitSeconds}s（可启动）"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        $entry.reason = $_.Exception.Message
        $entry.status = "fail"
    }
    finally {
        $sw.Stop()
        $entry.duration_s = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $results += [pscustomobject]$entry
        Write-Output ("[{0}] {1}  {2}  ({3}s)" -f $entry.status, $entry.tag, $entry.reason, $entry.duration_s)
    }
}

# 输出结果
$results | Export-Csv -Path $ResultFile -NoTypeInformation -Encoding UTF8
$results | Where-Object { $_.status -eq "fail" } | ForEach-Object { $_.tag } | Set-Content -Path $BadTagsFile -Encoding UTF8

$okCount = @($results | Where-Object { $_.status -eq "ok" }).Count
$failCount = @($results | Where-Object { $_.status -eq "fail" }).Count
Write-Output "== 完成: ok=$okCount fail=$failCount =="
Write-Output "CSV: $ResultFile"
Write-Output "坏 tag 列表: $BadTagsFile"

if ($failCount -gt 0) {
    exit 1  # 有坏 tag 时脚本非零退出，便于 CI 标记
}
