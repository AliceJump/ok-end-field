# 仓库根 = 本脚本（scripts/testing/）向上两级
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $RepoRoot

try {
    # 单次启动 uv/Python 完成全部 discovery，避免每个测试文件重复创建解释器。
    uv run --locked python -u -m unittest discover -s tests -p "Test*.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Test suite failed with exit code $LASTEXITCODE"
    }
}
catch {
    Write-Error $_
    exit 1
}
finally {
    Pop-Location
}