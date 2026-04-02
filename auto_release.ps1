# 统一 UTF8，避免 GitHub tag 中文乱码
param(
    [switch]$DryRun
)
[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [Console]::OutputEncoding

git config i18n.commitEncoding utf-8 | Out-Null
git config i18n.logOutputEncoding utf-8 | Out-Null



# 版本限制
$MAX_MAJOR = 10
$MAX_MINOR = 10
$MAX_PATCH = 99

function Get-AllTags {

    try {
        $tags = git tag -l 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ 获取标签失败" -ForegroundColor Red
            return @()
        }

        $tagList = $tags | Where-Object { $_ -match '\S' }
        Write-Host "✓ 找到 $($tagList.Count) 个标签" -ForegroundColor Green
        return $tagList
    }
    catch {
        Write-Host "✗ 获取标签失败: $_" -ForegroundColor Red
        return @()
    }
}

function Parse-Version {

    param([string]$tag)

    if ($tag -match '^v(\d+)\.(\d+)\.(\d+)$') {

        $major = [int]$matches[1]
        $minor = [int]$matches[2]
        $patch = [int]$matches[3]

        if ($major -le $MAX_MAJOR -and $minor -le $MAX_MINOR -and $patch -le $MAX_PATCH) {
            return @{
                Major = $major
                Minor = $minor
                Patch = $patch
                Valid = $true
            }
        }
    }

    return @{ Valid = $false }
}

function Find-MaxVersion {

    param([array]$tags)

    $versions = @()

    foreach ($tag in $tags) {

        $version = Parse-Version $tag

        if ($version.Valid) {
            $versions += $version
        }
    }

    if ($versions.Count -eq 0) {
        Write-Host "⚠ 未找到有效版本标签" -ForegroundColor Yellow
        return $null
    }

    $maxVersion = $versions |
        Sort-Object { $_.Major * 10000 + $_.Minor * 100 + $_.Patch } |
        Select-Object -Last 1

    Write-Host "✓ 当前最大版本: v$($maxVersion.Major).$($maxVersion.Minor).$($maxVersion.Patch)" -ForegroundColor Green

    return $maxVersion
}

function Increment-Version {

    param($version)

    if ($null -eq $version) {
        return @{ Major = 0; Minor = 1; Patch = 0; Valid = $true }
    }

    if ($version.Patch -lt $MAX_PATCH) {

        return @{
            Major = $version.Major
            Minor = $version.Minor
            Patch = $version.Patch + 1
            Valid = $true
        }
    }

    if ($version.Minor -lt $MAX_MINOR) {

        return @{
            Major = $version.Major
            Minor = $version.Minor + 1
            Patch = 0
            Valid = $true
        }
    }

    if ($version.Major -lt $MAX_MAJOR) {

        return @{
            Major = $version.Major + 1
            Minor = 0
            Patch = 0
            Valid = $true
        }
    }

    Write-Host "✗ 版本号已达到最大值 v$MAX_MAJOR.$MAX_MINOR.$MAX_PATCH" -ForegroundColor Red

    return @{ Valid = $false }
}

function Format-Version {

    param($version)

    return "v$($version.Major).$($version.Minor).$($version.Patch)"
}

function Test-GitStatus {

    $status = git status --porcelain 2>&1

    if ($status) {

        Write-Host "⚠ 工作区有未提交更改：" -ForegroundColor Yellow
        Write-Host $status

        $response = Read-Host "是否继续？(y/N)"

        return $response -ieq "y"
    }

    return $true
}

function Get-LatestCommitMessage {

    try {

        $message = git log -1 --pretty=%B 2>&1

        if ($LASTEXITCODE -ne 0) {
            return "Release"
        }

        return $message.Trim()
    }
    catch {
        return "Release"
    }
}

function New-GitTag {

    param(
        [string]$tag,
        [string]$message
    )

    try {

        git tag -a $tag -m "$message" 2>&1 | Out-Null

        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ 创建标签失败" -ForegroundColor Red
            return $false
        }

        Write-Host "✓ 已创建标签: $tag" -ForegroundColor Green

        return $true
    }
    catch {

        Write-Host "✗ 创建标签失败: $_" -ForegroundColor Red
        return $false
    }
}

function Push-GitTag {

    param([string]$tag)

    try {

        git push origin $tag 2>&1 | Out-Null

        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ 推送标签失败" -ForegroundColor Red
            return $false
        }

        Write-Host "✓ 标签已推送到远程: $tag" -ForegroundColor Green

        return $true
    }
    catch {

        Write-Host "✗ 推送标签失败: $_" -ForegroundColor Red
        return $false
    }
}

Write-Host ""
Write-Host ("=" * 60)
Write-Host "自动版本发布脚本" -ForegroundColor Cyan
Write-Host ("=" * 60)

$tags = Get-AllTags

$maxVersion = Find-MaxVersion $tags

$nextVersion = Increment-Version $maxVersion

if (-not $nextVersion.Valid) {
    exit 1
}

$nextTag = Format-Version $nextVersion

Write-Host ""
Write-Host "📦 新版本: $nextTag" -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {

    Write-Host "🔍 演习模式：" -ForegroundColor Yellow
    Write-Host "  创建标签: $nextTag"
    Write-Host "  推送命令: git push origin $nextTag"

    exit 0
}

if (-not (Test-GitStatus)) {
    exit 1
}

Write-Host "即将发布版本: $nextTag" -ForegroundColor Yellow

$response = Read-Host "确认继续？(Y/n)"

if ($response -ieq "n") {

    Write-Host "✗ 操作已取消" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "⟳ 推送最新 commit..." -ForegroundColor Cyan

git push 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {

    Write-Host "✗ 推送 commit 失败" -ForegroundColor Red
    exit 1
}

Write-Host "✓ commit 已推送" -ForegroundColor Green

$message = Get-LatestCommitMessage

if (-not (New-GitTag $nextTag $message)) {
    exit 1
}

if (-not (Push-GitTag $nextTag)) {

    Write-Host "⚠ 标签已创建但推送失败，可手动执行：" -ForegroundColor Yellow
    Write-Host "git push origin $nextTag"

    exit 1
}

Write-Host ""
Write-Host ("=" * 60)
Write-Host "✓ 发布成功！版本: $nextTag" -ForegroundColor Green
Write-Host ("=" * 60)