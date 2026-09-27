# Validate GitHub Actions files on PowerShell

Enumerate both extensions and pass explicit paths to the parser and `actionlint`. Run from the repository root:

```powershell
$workflowPaths = @(
    Get-ChildItem -LiteralPath '.github/workflows' -File |
        Where-Object { $_.Extension -in '.yml', '.yaml' } |
        ForEach-Object FullName
)
if ($workflowPaths.Count -eq 0) { throw 'No workflow YAML files found' }

$validatorPath = Join-Path $env:TEMP 'validate-workflow-yaml.py'
@'
import pathlib
import sys
import yaml

for file in sys.argv[1:]:
    yaml.safe_load(pathlib.Path(file).read_text(encoding="utf-8-sig"))
print(f"Parsed {len(sys.argv) - 1} workflow YAML file(s)")
'@ | Set-Content -LiteralPath $validatorPath -Encoding UTF8
uv run --locked python -X utf8 $validatorPath @workflowPaths
if ($LASTEXITCODE -ne 0) { throw 'Workflow YAML parsing failed' }

$actionlint = Get-Command actionlint -ErrorAction SilentlyContinue
if (-not $actionlint) { throw 'actionlint is required for Actions validation' }
& $actionlint.Source @workflowPaths
if ($LASTEXITCODE -ne 0) { throw 'actionlint failed' }
```

The YAML parser checks syntax. `actionlint` also checks Actions structure, expressions, and contexts such as `matrix.os`. Do not report full validation when either step did not run.
