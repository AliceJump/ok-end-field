# Merge conflicting gettext catalogs

When Git conflicts in `ok.po` and `ok.mo`, merge the PO sides and recompile MO. `merge_po.py` keys entries by `(msgctxt, msgid, msgid_plural)`, retains metadata, and rejects duplicates.

Export both Git stages as UTF-8. PowerShell 5.1's plain `>` writes UTF-16LE, which `polib` cannot parse:

```powershell
git show :2:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\ours.po"
git show :3:i18n/zh_CN/LC_MESSAGES/ok.po | Out-File -Encoding utf8 "$env:TEMP\theirs.po"
uv run --locked python .agents/skills/ok-script-i18n/scripts/merge_po.py "$env:TEMP\ours.po" "$env:TEMP\theirs.po" --output i18n/zh_CN/LC_MESSAGES/ok.po --prefer ours --compile
```

Choose `--prefer ours` or `--prefer theirs` after inspecting the conflicting translations. Do not use the script's mtime-based `newer` default for files exported from Git. Repeat for each conflicting locale, then run `task_i18n_helper.py check` and `tests.TestPoLocaleConsistency`.
