"""po 目录合并工具：按 msgid->msgstr 字典合并两个 po 文件。

用途：git merge / stash pop 等场景下同一 ok.po 被双方修改时，以「较新者优先」
的规则合并翻译目录，避免手工处理冲突：

- 读取两方的 msgid -> POEntry 字典（跳过 header 条目）；
- 同一 msgid 两边都有且内容不同：默认取 mtime 较新的文件一侧（可用
  ``--prefer`` 显式指定）；
- 只在某一侧存在的 msgid 全部保留；
- 输出文件条目顺序：较新一侧原有顺序，较旧一侧独有条目追加在末尾；
- 可选 ``--compile``：合并后立即把输出 po 编译为同目录 ok.mo。

示例（合并冲突两侧的目录并重新编译）::

    python merge_po.py ours.po theirs.po --output merged.po --compile
"""

import argparse
import os
import sys

import polib


def load_catalog(path):
    po = polib.pofile(path)
    header = None
    entries = {}
    for entry in po:
        if not entry.msgid:
            header = entry
            continue
        if entry.msgid not in entries:
            entries[entry.msgid] = entry
    return po, header, entries


def merge_po(ours_path, theirs_path, prefer="newer"):
    """合并两个 po 文件，返回 (输出 po, 采用规则说明列表)。"""
    ours_po, ours_header, ours = load_catalog(ours_path)
    theirs_po, theirs_header, theirs = load_catalog(theirs_path)

    ours_mtime = os.path.getmtime(ours_path)
    theirs_mtime = os.path.getmtime(theirs_path)
    if prefer == "newer":
        newer_first = ours_mtime >= theirs_mtime
    elif prefer == "ours":
        newer_first = True
    elif prefer == "theirs":
        newer_first = False
    else:
        raise ValueError("--prefer 仅支持 newer / ours / theirs")

    first, second = (ours, theirs) if newer_first else (theirs, ours)
    first_po = ours_po if newer_first else theirs_po
    second_po = theirs_po if newer_first else ours_po
    first_name = os.path.basename(ours_path) if newer_first else os.path.basename(theirs_path)
    second_name = os.path.basename(theirs_path) if newer_first else os.path.basename(ours_path)

    notes = []
    merged = polib.POFile()
    merged.metadata = dict(first_po.metadata or {})
    header = ours_header if newer_first else theirs_header
    if header is not None:
        merged.insert(0, header)

    used = set()
    for msgid, entry in first.items():
        if msgid in second:
            other = second[msgid]
            if other.msgstr != entry.msgstr or bool(other.flags) != bool(entry.flags):
                notes.append(f"冲突取 {first_name}: {msgid!r}")
        merged.append(entry)
        used.add(msgid)
    appended = 0
    for msgid, entry in second.items():
        if msgid in used:
            continue
        merged.append(entry)
        appended += 1
    if appended:
        notes.append(f"追加 {second_name} 独有条目 {appended} 条")
    return merged, notes


def main():
    parser = argparse.ArgumentParser(description="合并两个 po 目录文件，较新者优先")
    parser.add_argument("ours", help="己方 po 文件路径")
    parser.add_argument("theirs", help="对方 po 文件路径")
    parser.add_argument("--output", required=True, help="合并结果输出路径")
    parser.add_argument(
        "--prefer",
        choices=("newer", "ours", "theirs"),
        default="newer",
        help="同一 msgid 冲突时的取舍（默认较新者，按文件 mtime）",
    )
    parser.add_argument("--compile", action="store_true", help="合并后编译输出 po 的同目录 ok.mo")
    args = parser.parse_args()

    merged, notes = merge_po(args.ours, args.theirs, prefer=args.prefer)
    for note in notes:
        print(f"[merge] {note}")
    merged.save(args.output)
    print(f"[merge] saved {args.output}（{len([e for e in merged if e.msgid])} 条）")
    if args.compile:
        mo_path = os.path.join(os.path.dirname(args.output) or ".", "ok.mo")
        merged.save_as_mofile(mo_path)
        print(f"[merge] compiled {mo_path}")


if __name__ == "__main__":
    sys.exit(main())
