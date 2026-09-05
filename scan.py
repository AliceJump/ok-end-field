from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUTPUT_FILE = Path("docs-history.txt")
TARGET_DIR = "docs"


def get_all_tree_ids() -> list[str]:
    """获取 Git object database 中的全部 tree object。"""
    result = subprocess.run(
        [
            "git",
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    tree_ids: list[str] = []

    for line in result.stdout.splitlines():
        parts = line.split(b" ", 1)

        if len(parts) != 2:
            continue

        oid, obj_type = parts

        if obj_type == b"tree":
            tree_ids.append(oid.decode("ascii"))

    return tree_ids


def read_objects(object_ids: list[str]):
    """通过单个 git cat-file --batch 进程批量读取 objects。"""
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert proc.stdin is not None
    assert proc.stdout is not None

    for oid in object_ids:
        proc.stdin.write(oid.encode("ascii"))
        proc.stdin.write(b"\n")

    proc.stdin.close()

    while True:
        header = proc.stdout.readline()

        if not header:
            break

        header = header.rstrip(b"\n")

        parts = header.split(b" ")

        if len(parts) != 3:
            continue

        oid_raw, obj_type_raw, size_raw = parts

        oid = oid_raw.decode("ascii")
        obj_type = obj_type_raw.decode("ascii")
        size = int(size_raw)

        data = proc.stdout.read(size)

        # cat-file --batch object 后面的换行
        proc.stdout.read(1)

        yield oid, obj_type, data

    proc.wait()


def parse_tree(data: bytes):
    """
    解析 Git tree object。

    entry 格式：

        <mode> SP <name> NUL <20-byte object id>
    """
    pos = 0
    length = len(data)

    while pos < length:
        space = data.find(b" ", pos)

        if space == -1:
            break

        mode = data[pos:space].decode(
            "ascii",
            errors="replace",
        )

        null = data.find(
            b"\0",
            space + 1,
        )

        if null == -1:
            break

        name = data[space + 1 : null].decode(
            "utf-8",
            errors="surrogateescape",
        )

        oid_start = null + 1
        oid_end = oid_start + 20

        if oid_end > length:
            break

        child_oid = data[oid_start:oid_end].hex()

        pos = oid_end

        yield mode, name, child_oid


def main() -> None:
    print(
        "Scanning Git object database...",
        file=sys.stderr,
    )

    # ---------------------------------------------------------
    # 1. 找出全部 tree object
    # ---------------------------------------------------------

    tree_ids = get_all_tree_ids()

    print(
        f"Found {len(tree_ids):,} tree objects",
        file=sys.stderr,
    )

    if not tree_ids:
        print(
            "No tree objects found.",
            file=sys.stderr,
        )
        return

    # ---------------------------------------------------------
    # 2. 批量读取 tree
    #
    # 只保存 tree 的 entries。
    # ---------------------------------------------------------

    trees: dict[
        str,
        list[tuple[str, str, str]],
    ] = {}

    print(
        "Loading trees...",
        file=sys.stderr,
    )

    for index, (oid, obj_type, data) in enumerate(
        read_objects(tree_ids),
        1,
    ):
        if obj_type != "tree":
            continue

        trees[oid] = list(parse_tree(data))

        if index % 500 == 0 or index == len(tree_ids):
            print(
                f"\rLoaded {index:,}/{len(tree_ids):,} trees",
                end="",
                file=sys.stderr,
                flush=True,
            )

    print(file=sys.stderr)

    # ---------------------------------------------------------
    # 3. 找出所有直接包含 docs 的 tree
    #
    # 不扫描所有文件。
    # ---------------------------------------------------------

    docs_trees: set[str] = set()

    for tree_oid, entries in trees.items():
        for mode, name, child_oid in entries:
            if name == TARGET_DIR and mode.startswith("40"):
                docs_trees.add(child_oid)

    print(
        f"Found {len(docs_trees):,} historical docs/ trees",
        file=sys.stderr,
    )

    if not docs_trees:
        print(
            "\nNo docs/ directory found.",
            file=sys.stderr,
        )
        return

    # ---------------------------------------------------------
    # 4. 输出文件
    #
    # 每次运行重新生成，但每找到一个文件立即 flush。
    # ---------------------------------------------------------

    output = OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
        newline="\n",
    )

    # ---------------------------------------------------------
    # 5. 去重
    #
    # 同一路径对应同一个 blob 时只记录一次。
    #
    # 注意：
    # 同一个文件路径在历史上内容改变，
    # 会有多个 blob，所以不会错误去重。
    # ---------------------------------------------------------

    written: set[tuple[str, str]] = set()

    visited: set[tuple[str, str]] = set()

    file_count = 0
    directory_count = 0

    def write_file(
        path: str,
        blob_oid: str,
        parent_tree_oid: str,
    ) -> None:
        nonlocal file_count

        key = (
            path,
            blob_oid,
        )

        if key in written:
            return

        written.add(key)

        output.write(f"{path}\t{blob_oid}\t{parent_tree_oid}\n")

        output.flush()

        file_count += 1

    def walk_docs(
        tree_oid: str,
        prefix: str,
    ) -> None:
        nonlocal directory_count

        key = (
            tree_oid,
            prefix,
        )

        if key in visited:
            return

        visited.add(key)

        entries = trees.get(tree_oid)

        if entries is None:
            return

        for mode, name, child_oid in entries:
            path = f"{prefix}/{name}"

            # -------------------------------------------------
            # 普通文件
            # -------------------------------------------------

            if mode.startswith("100"):
                write_file(
                    path,
                    child_oid,
                    tree_oid,
                )

            # -------------------------------------------------
            # 子目录
            # -------------------------------------------------

            elif mode.startswith("40"):
                directory_count += 1

                walk_docs(
                    child_oid,
                    path,
                )

    # ---------------------------------------------------------
    # 6. 遍历所有历史 docs tree
    # ---------------------------------------------------------

    print(
        "Walking docs/ trees...",
        file=sys.stderr,
    )

    try:
        for index, tree_oid in enumerate(
            docs_trees,
            1,
        ):
            walk_docs(
                tree_oid,
                "docs",
            )

            print(
                f"\rProcessed {index:,}/{len(docs_trees):,} docs trees | files: {file_count:,}",
                end="",
                file=sys.stderr,
                flush=True,
            )

    except KeyboardInterrupt:
        print(
            "\n\nInterrupted.",
            file=sys.stderr,
        )

    finally:
        output.close()

    print(file=sys.stderr)

    # ---------------------------------------------------------
    # 7. 统计
    # ---------------------------------------------------------

    print(
        "========================================",
        file=sys.stderr,
    )

    print(
        "Scan finished.",
        file=sys.stderr,
    )

    print(
        f"All tree objects : {len(tree_ids):,}",
        file=sys.stderr,
    )

    print(
        f"docs/ trees      : {len(docs_trees):,}",
        file=sys.stderr,
    )

    print(
        f"Directories      : {directory_count:,}",
        file=sys.stderr,
    )

    print(
        f"Files            : {file_count:,}",
        file=sys.stderr,
    )

    print(
        f"Output           : {OUTPUT_FILE.resolve()}",
        file=sys.stderr,
    )

    print(
        "========================================",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
