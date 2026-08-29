"""从已去重的日志 zip 中恢复完整截图文件集。

用法:
    python scripts/maintenance/restore_log_screenshots.py <日志zip路径> [输出目录]

zip 内应包含 screenshots_dedup_info.json（导出/上传日志时由
src/patches/log_upload_patch.py 自动生成）。脚本会把 zip 全部内容解压到
输出目录，将去重时被删除的重复图片按原文件名恢复，并依据 _boxes.json
侧车信息重绘 _boxed.png（新版 zip 不再保存画框大图）。

示例:
    python scripts/maintenance/restore_log_screenshots.py C:\\Downloads\\20260803-154650-ok-ef-log.zip
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.patches.log_zip_dedup import (
    DEDUP_INFO_FILENAME,
    read_dedup_info,
    restore_duplicates,
)
from src.patches.screenshot_sidecar import rebuild_boxed_images


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复日志 zip 中被去重的重复截图")
    parser.add_argument("zip_path", type=Path, help="日志 zip 文件路径")
    parser.add_argument("output_dir", type=Path, nargs="?", help="输出目录（默认 <zip 名去掉后缀>_restored）")
    parser.add_argument("--no-rebuild-boxed", action="store_true", help="不依据 _boxes.json 重绘 _boxed.png")
    args = parser.parse_args()

    if not args.zip_path.is_file():
        parser.error(f"zip 文件不存在: {args.zip_path}")

    info = read_dedup_info(args.zip_path)
    if info is None:
        print(f"zip 中未找到 {DEDUP_INFO_FILENAME}，无需恢复")
        return 0

    output_dir = args.output_dir or args.zip_path.with_name(args.zip_path.stem + "_restored")
    restored = restore_duplicates(args.zip_path, output_dir)
    print(f"已解压全部文件到 {output_dir}")
    if restored:
        print(f"已恢复 {len(restored)} 个重复图片:")
        for name in restored:
            print(f"  {name}")
    else:
        print("去重信息中没有需要恢复的条目")

    if not args.no_rebuild_boxed:
        rebuilt = rebuild_boxed_images(output_dir)
        if rebuilt:
            print(f"已依据 _boxes.json 重绘 {len(rebuilt)} 个 _boxed.png:")
            for name in rebuilt:
                print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
