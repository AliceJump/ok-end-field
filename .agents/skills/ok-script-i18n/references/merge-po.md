# Merge conflicting gettext catalogs

When Git conflicts in `ok.po` and `ok.mo`, merge the PO sides and recompile MO. `merge_po.py` keys entries by `(msgctxt, msgid, msgid_plural)`, retains metadata, and rejects duplicates.

Decode Git's UTF-8 output explicitly, then write both stages as UTF-8 without a BOM. PowerShell 5.1's plain `>` writes UTF-16LE, and `Out-File -Encoding utf8` adds a BOM:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$ours = [string[]](git show :2:i18n/zh_CN/LC_MESSAGES/ok.po)
$theirs = [string[]](git show :3:i18n/zh_CN/LC_MESSAGES/ok.po)
[System.IO.File]::WriteAllLines("$env:TEMP\ours.po", $ours, $utf8NoBom)
[System.IO.File]::WriteAllLines("$env:TEMP\theirs.po", $theirs, $utf8NoBom)
uv run --locked python .agents/skills/ok-script-i18n/scripts/merge_po.py "$env:TEMP\ours.po" "$env:TEMP\theirs.po" --output i18n/zh_CN/LC_MESSAGES/ok.po --prefer ours --compile
```

Choose `--prefer ours` or `--prefer theirs` after inspecting the conflicting translations. Do not use the script's mtime-based `newer` default for files exported from Git. Repeat for each conflicting locale, then run `task_i18n_helper.py check` and `tests.TestPoLocaleConsistency`.
