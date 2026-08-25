# 仓库根 = 本脚本（scripts/testing/）向上两级
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $RepoRoot

try {
    Get-ChildItem -Path (Join-Path $RepoRoot 'tests\Test*.py') | ForEach-Object {
        Write-Host "Running tests in $($_.FullName)"
        try {
            # Run the Python unittest command (via uv, uses the project .venv)
            uv run python -m unittest $_.FullName

            # Check if the previous command succeeded
            if ($LASTEXITCODE -ne 0) {
                throw "Tests failed in $($_.FullName)"
            }
        }
        catch {
            # Stop the loop and return the error
            Write-Error $_
            exit 1
        }
    }
}
finally {
    Pop-Location
}