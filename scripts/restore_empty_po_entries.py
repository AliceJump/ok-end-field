# -*- coding: utf-8 -*-
"""从 git 历史 po 恢复空翻译。

对 i18n/*/LC_MESSAGES/ok.po 的每个条目：
- 只处理「当前 msgstr 为空字符串」的条目；
- 若历史 po（默认 git HEAD）中存在同 msgid 且 msgstr 非空，则补上历史翻译；
- 不新增 msgid、不恢复已删除条目、不覆盖已有非空翻译。

用法：
    python scripts/restore_empty_po_entries.py            # 源 = git HEAD
    python scripts/restore_empty_po_entries.py --ref <commit>   # 指定历史版本
"""

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import polib
except ImportError:
    polib = None

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "i18n"
LOCALES = ("zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES")


def load_history(loc: str, ref: str) -> polib.POFile | None:
    """从 git 读取历史 ok.po 内容并解析。"""
    rel = f"i18n/{loc}/LC_MESSAGES/ok.po"
    proc = subprocess.run(
        ["git", "show", f"{ref}:{rel}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(f"  [po] {loc}: git show {ref}:{rel} 失败: {proc.stderr.strip()}")
        return None
    return polib.pofile(proc.stdout)


def restore_locale(loc: str, ref: str, quiet: bool = False) -> int:
    """恢复单个 locale 的空翻译，返回补回条数。"""
    po_path = I18N_DIR / loc / "LC_MESSAGES" / "ok.po"
    if not po_path.exists():
        if not quiet:
            print(f"  [po] missing {po_path}")
        return 0
    history = load_history(loc, ref)
    if history is None:
        return 0

    cur = polib.pofile(str(po_path))
    hmap = {}
    for e in history:
        hmap[e.msgid.rstrip("\n")] = e

    filled = 0
    for e in cur:
        if not e.msgid or e.msgstr.strip():
            continue
        he = hmap.get(e.msgid.rstrip("\n"))
        if he is not None and he.msgstr.strip():
            e.msgstr = he.msgstr
            filled += 1

    if filled:
        cur.save(str(po_path))
        cur.save_as_mofile(str(po_path).replace(".po", ".mo"))
        print(f"  [po] {loc}: 补回 {filled} 条空翻译")
    elif not quiet:
        print(f"  [po] {loc}: no changes")
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description="从 git 历史 po 恢复空翻译")
    parser.add_argument("--ref", default="HEAD",
                        help="历史版本 ref（默认 HEAD）")
    args = parser.parse_args()

    if polib is None:
        print("polib not installed; skipping")
        return 1

    total = 0
    for loc in LOCALES:
        total += restore_locale(loc, args.ref)
    print(f"\n共补回 {total} 条空翻译（源: {args.ref}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
