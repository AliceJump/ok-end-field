#!/usr/bin/env python3
"""
迁移脚本：将 assets/lang/ 下每个模块的多语言 JSON 文件合并为单个文件。

旧结构：
  assets/lang/<module>/<locale>.json
  每个文件 {"k_xxx": {"pattern": "text", ...}}

新结构：
  assets/lang/<module>.json
  每个文件 {"k_xxx": {"zh_CN": {"pattern": "文本"}, "en_US": {"pattern": "text"}, ...}}
"""

import json
import shutil
from pathlib import Path

LANG_ROOT = Path(__file__).resolve().parents[2] / "assets" / "lang"
SUPPORTED_LOCALES = ["zh_CN", "zh_TW", "en_US", "ja_JP", "ko_KR", "es_ES"]


def merge_module(module_name: str) -> dict:
    """读取模块下所有 locale JSON，合并为一个字典。"""
    module_dir = LANG_ROOT / module_name
    if not module_dir.is_dir():
        print(f"  [跳过] {module_name}: 不是目录")
        return {}

    unified = {}

    for locale in SUPPORTED_LOCALES:
        file_path = module_dir / f"{locale}.json"
        if not file_path.exists():
            print(f"  [警告] {module_name}/{locale}.json 不存在")
            continue

        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)

        for key, value in data.items():
            if not isinstance(value, dict):
                print(f"  [警告] {module_name}/{locale}.json: 键 {key} 的值不是 dict，跳过")
                continue
            if key not in unified:
                unified[key] = {}
            unified[key][locale] = value

    return unified


def write_unified(module_name: str, data: dict) -> None:
    """写入合并后的文件到 assets/lang/<module>.json。"""
    if not data:
        print(f"  [跳过] {module_name}: 无数据")
        return

    output_path = LANG_ROOT / f"{module_name}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  [完成] {module_name}.json ({sum(len(v) for v in data.values())} 条翻译)")


def backup_module_dir(module_name: str) -> None:
    """备份旧的多文件目录。"""
    module_dir = LANG_ROOT / module_name
    backup_dir = LANG_ROOT / f"{module_name}_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(module_dir, backup_dir)
    print(f"  [备份] {module_name} -> {module_name}_backup")


def main():
    # 收集所有模块目录
    module_dirs = sorted(
        d.name for d in LANG_ROOT.iterdir()
        if d.is_dir() and not d.name.endswith("_backup")
    )

    print(f"找到 {len(module_dirs)} 个模块目录\n")

    for module_name in module_dirs:
        print(f"处理模块: {module_name}")
        # 1. 备份
        backup_module_dir(module_name)

        # 2. 合并
        unified_data = merge_module(module_name)

        # 3. 写入统一文件
        write_unified(module_name, unified_data)

    print("\n迁移完成！")
    print("\n请验证新文件无误后，可手动删除 _backup 后缀的备份目录。")
    print("若要清理旧目录，运行: python migrate_lang.py cleanup")


def cleanup():
    """删除所有模块目录（保留备份）。"""
    module_dirs = sorted(
        d.name for d in LANG_ROOT.iterdir()
        if d.is_dir() and not d.name.endswith("_backup")
    )

    print(f"将删除 {len(module_dirs)} 个旧模块目录...")
    confirm = input("确认删除？(yes/no): ")
    if confirm.lower() == "yes":
        for module_name in module_dirs:
            module_dir = LANG_ROOT / module_name
            shutil.rmtree(module_dir)
            print(f"  [删除] {module_name}/")
        print("清理完成！")
    else:
        print("已取消。")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup()
    else:
        main()
