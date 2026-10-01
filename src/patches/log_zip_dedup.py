"""导出/上传日志 zip 时的重复截图去重与恢复。

- 打 zip 时（仅导出日志/上传日志触发）：对图片文件按内容（MD5）去重，
  只保留第一份，重复项记录到 zip 内的 ``screenshots_dedup_info.json``。
- 恢复时：根据该信息文件把保留文件复制回重复文件名，还原与去重前完全一致的文件集。
  （见 ``scripts/maintenance/restore_log_screenshots.py``）

本模块不依赖 ok-script / Qt，可被脚本独立调用。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path

DEDUP_INFO_FILENAME = "screenshots_dedup_info.json"
DEDUP_INFO_FORMAT = 1


def md5_hex(data: bytes) -> str:
    """计算图片内容 MD5，用于判断两张图是否完全相同。"""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def collect_image_duplicates(entries):
    """按内容对图片条目去重，保留每个内容首次出现的条目。

    Args:
        entries: ``[(arcname, data), ...]`` 的图片条目列表。

    Returns:
        ``(unique_entries, duplicates)``：
        - ``unique_entries``: 去重后保留的条目（顺序不变）。
        - ``duplicates``: 重复记录列表，每项为
          ``{"hash": str, "kept": arcname, "duplicate": arcname}``。
    """
    seen = {}
    unique_entries = []
    duplicates = []
    for arcname, data in entries:
        digest = md5_hex(data)
        if digest in seen:
            duplicates.append(
                {
                    "hash": digest,
                    "kept": seen[digest],
                    "duplicate": arcname,
                }
            )
        else:
            seen[digest] = arcname
            unique_entries.append((arcname, data))
    return unique_entries, duplicates


def build_dedup_info(duplicates: list, note: str = "") -> dict:
    """生成写入 zip 的去重信息文件内容。"""
    return {
        "format": DEDUP_INFO_FORMAT,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note": note
        or ("完全相同的图片已去重，仅保留第一份；重复文件可用 scripts/maintenance/restore_log_screenshots.py 恢复"),
        "duplicates": duplicates,
    }


def read_dedup_info(zip_path: str | Path) -> dict | None:
    """读取 zip 内的去重信息文件，不存在时返回 None。"""
    with zipfile.ZipFile(zip_path) as zipf:
        if DEDUP_INFO_FILENAME not in zipf.namelist():
            return None
        with zipf.open(DEDUP_INFO_FILENAME) as info_file:
            return json.load(info_file)


def resolve_extract_target(root: str | Path, arcname: str) -> Path | None:
    """把 zip 内条目名安全地映射到 ``root`` 之下的路径，越界时返回 ``None``。

    zip 条目名与去重信息都来自外部 zip，属不可信输入：可能带绝对路径、盘符或
    ``..``。不做校验直接拼接会把文件写到 ``root`` 之外（zip slip）。
    """
    name = str(arcname).replace("\\", "/")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if name.startswith("/") or not parts or ".." in parts or ":" in parts[0]:
        return None
    root_path = Path(root)
    target = root_path.joinpath(*parts)
    if root_path.resolve() not in target.resolve().parents:
        return None
    return target


def restore_duplicates(zip_path: str | Path, output_dir: str | Path) -> list[str]:
    """恢复 zip 中被去重的重复图片。

    先把 zip 全部内容解压到 ``output_dir``，再按去重信息把保留文件复制到
    每个重复文件名，得到与去重前完全一致的文件集。

    越界（zip slip）的条目名会被跳过，不会写到 ``output_dir`` 之外。

    Returns:
        已恢复的重复图片 arcname 列表（zip 内没有去重信息时为空列表）。
    """
    zip_path = Path(zip_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    info = read_dedup_info(zip_path)
    restored = []
    with zipfile.ZipFile(zip_path) as zipf:
        for entry in zipf.namelist():
            target = resolve_extract_target(output_dir, entry)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipf.open(entry) as src, open(target, "wb") as dst:
                dst.write(src.read())

        if not info:
            return restored

        for record in info.get("duplicates", []):
            kept = record["kept"]
            duplicate = record["duplicate"]
            if kept not in zipf.namelist():
                continue
            target = resolve_extract_target(output_dir, duplicate)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zipf.read(kept))
            restored.append(duplicate)

    return restored
