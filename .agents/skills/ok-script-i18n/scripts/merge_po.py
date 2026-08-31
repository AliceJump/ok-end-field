"""PO 目录合并工具：按上下文、单复数 msgid 合并两个 PO 文件。

用途：git merge / stash pop 等场景下同一 ok.po 被双方修改时，以「较新者优先」
的规则合并翻译目录，避免手工处理冲突：

- 读取两方的 (msgctxt, msgid, msgid_plural) -> POEntry 字典；
- 同一复合键两边都有且完整条目内容不同：默认取 mtime 较新的文件一侧（可用
  ``--prefer`` 显式指定）；
- 重复复合键视为输入错误，不静默丢弃；
- 只在某一侧存在的 msgid 全部保留；
- 输出文件条目顺序：较新一侧原有顺序，较旧一侧独有条目追加在末尾；
- 可选 ``--compile``：合并后立即把输出 po 编译为同目录 ok.mo。

示例（合并冲突两侧的目录并重新编译）::

    python merge_po.py ours.po theirs.po --output merged.po --compile
"""
import argparse
import copy
import os
import sys

import polib


def load_catalog(path):
    po = polib.pofile(path)
    entries = {}
    for entry in po:
        if not entry.msgid or entry.obsolete:
            continue
        key = (entry.msgctxt or "", entry.msgid, entry.msgid_plural or "")
        if key in entries:
            raise ValueError(f"{path} contains duplicate PO entry: {key!r}")
        entries[key] = entry
    return po, entries


def entries_differ(left, right):
    fields = (
        "msgstr",
        "msgstr_plural",
        "flags",
        "comment",
        "tcomment",
        "occurrences",
        "previous_msgid",
        "previous_msgctxt",
        "previous_msgid_plural",
    )
    return any(getattr(left, field, None) != getattr(right, field, None) for field in fields)


def merge_po(ours_path, theirs_path, prefer="newer"):
    """合并两个 po 文件，返回 (输出 po, 采用规则说明列表)。"""
    ours_po, ours = load_catalog(ours_path)
    theirs_po, theirs = load_catalog(theirs_path)

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
    first_name = os.path.basename(ours_path) if newer_first else os.path.basename(theirs_path)
    second_name = os.path.basename(theirs_path) if newer_first else os.path.basename(ours_path)

    notes = []
    merged = polib.POFile()
    merged.metadata = dict(first_po.metadata or {})
    merged.metadata_is_fuzzy = first_po.metadata_is_fuzzy
    merged.header = first_po.header
    merged.encoding = first_po.encoding
    merged.wrapwidth = first_po.wrapwidth

    used = set()
    for key, entry in first.items():
        if key in second and entries_differ(entry, second[key]):
            notes.append(f"冲突取 {first_name}: {key!r}")
        merged.append(copy.deepcopy(entry))
        used.add(key)
    appended = 0
    for key, entry in second.items():
        if key in used:
            continue
        merged.append(copy.deepcopy(entry))
        appended += 1
    if appended:
        notes.append(f"追加 {second_name} 独有条目 {appended} 条")
    return merged, notes


def main():
    parser = argparse.ArgumentParser(description="合并两个 po 目录文件，较新者优先")
    parser.add_argument("ours", help="己方 po 文件路径")
    parser.add_argument("theirs", help="对方 po 文件路径")
    parser.add_argument("--output", required=True, help="合并结果输出路径")
    parser.add_argument("--prefer", choices=("newer", "ours", "theirs"), default="newer",
                        help="同一 msgid 冲突时的取舍（默认较新者，按文件 mtime）")
    parser.add_argument("--compile", action="store_true",
                        help="合并后编译输出 po 的同目录 ok.mo")
    args = parser.parse_args()

    try:
        merged, notes = merge_po(args.ours, args.theirs, prefer=args.prefer)
        for note in notes:
            print(f"[merge] {note}")
        output_dir = os.path.dirname(os.path.abspath(args.output))
        os.makedirs(output_dir, exist_ok=True)
        merged.save(args.output)
        print(f"[merge] saved {args.output}（{len([e for e in merged if e.msgid])} 条）")
        if args.compile:
            mo_path = os.path.join(output_dir, "ok.mo")
            merged.save_as_mofile(mo_path)
            print(f"[merge] compiled {mo_path}")
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"[merge] error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())