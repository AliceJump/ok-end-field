# PowerShell PR body safety

Inside a double-quoted PowerShell string, Markdown backticks are escapes and can corrupt the body. Store the exact text in a single-quoted here-string, write UTF-8, and pass `--body-file`:

```powershell
$body = @'
Markdown containing `code`.
'@
$bodyPath = Join-Path $env:TEMP 'codex-pr-body.md'
[IO.File]::WriteAllText($bodyPath, $body, [Text.UTF8Encoding]::new($false))
$pr = gh pr create --base master --head $branch --title $title --body-file $bodyPath
```

For an edit, set `$pr` to the PR number and use `gh pr edit $pr --body-file $bodyPath`. Fetch and compare the entire remote body after either operation:

```powershell
$remoteBase64 = gh pr view $pr --json body --jq '.body | @base64'
$localBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($body.Replace("`r`n", "`n")))
if ($remoteBase64 -cne $localBase64) {
    throw 'Remote PR body differs from the intended body'
}
```

A search for a surviving backtick is insufficient; another backtick may already have been lost.
