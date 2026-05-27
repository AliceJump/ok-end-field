"""
Lang Batch Translator - 分阶段扫描 + 缺失key整合翻译版
"""

import json
from pathlib import Path
from typing import Any
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from deep_translator import GoogleTranslator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.lang import SUPPORTED_LOCALES


LANG_ROOT = ROOT / "assets" / "lang"
SOURCE_LOCALE = "zh_CN"
TARGET_LOCALES = [loc for loc in SUPPORTED_LOCALES if loc != SOURCE_LOCALE]

CHINESE_RUN = re.compile(r"[\u4e00-\u9fff]+")
TRANSLATOR_TARGETS = {"en_US": "en", "es_ES": "es", "ja_JP": "ja", "ko_KR": "ko", "zh_TW": "zh-TW"}


def load_json(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def normalized_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_if_changed(path: Path, new_data: dict):
    new_text = normalized_json(new_data)
    if path.exists() and path.read_text(encoding="utf-8") == new_text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def collect_all_keys(node: Any) -> set[str]:
    """递归收集所有顶级 key"""
    keys = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            if isinstance(v, dict):
                keys.update(collect_all_keys(v))
    return keys


def main():
    print("=== Step 1: 扫描 lang 目录 ===")
    modules = sorted([d for d in LANG_ROOT.iterdir() if d.is_dir()])
    print(f"找到 {len(modules)} 个模块\n")

    # Step 2 & 3: 按模块分析缺失 key
    missing_by_locale: dict[str, dict[str, dict]] = defaultdict(dict)  # locale -> {key: source_value}

    for module_dir in modules:
        module_name = module_dir.name
        print(f"【{module_name}】")

        zh_file = module_dir / "zh_CN.json"
        if not zh_file.exists():
            print("  └─ 无 zh_CN.json，跳过")
            continue

        try:
            source_data = json.loads(zh_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  └─ 读取 zh_CN 失败: {e}")
            continue

        source_keys = collect_all_keys(source_data)
        print(f"  → 源文件共有 {len(source_keys)} 个 key")

        # 检查每个目标语言的缺失 key
        for locale in TARGET_LOCALES:
            target_file = module_dir / f"{locale}.json"
            existing = load_json(target_file)
            existing_keys = collect_all_keys(existing)

            missing_keys = source_keys - existing_keys

            if missing_keys:
                missing_by_locale[locale][module_name] = {
                    "source_data": source_data,
                    "missing_keys": missing_keys
                }
                print(f"    → {locale}: 缺失 {len(missing_keys)} 个 key")
            else:
                print(f"    → {locale}: 已完整")

    # Step 4: 整合所有缺失 key 进行批量翻译
    print("\n=== Step 4: 整合缺失 key 并翻译 ===")
    trans_maps = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        for locale in TARGET_LOCALES:
            all_missing_texts = []
            for module_info in missing_by_locale[locale].values():
                # 这里简化收集，需要翻译的文本（你可以根据需要扩展）
                all_missing_texts.extend(collect_texts(module_info["source_data"]))

            unique_texts = list(dict.fromkeys(all_missing_texts))
            print(f"  {locale}: 共 {len(unique_texts)} 条文本需要翻译")
            
            future = executor.submit(translate_batch, locale, unique_texts)
            futures[future] = locale

        for future in as_completed(futures):
            locale = futures[future]
            trans_maps[locale] = future.result()
            print(f"  ✓ {locale} 翻译完成")

    # Step 5: 写回文件
    print("\n=== Step 5: 写入文件 ===")
    for locale in TARGET_LOCALES:
        if locale not in missing_by_locale:
            continue
        print(f"\n正在写入 {locale} 文件...")

        for module_name, info in missing_by_locale[locale].items():
            module_dir = LANG_ROOT / module_name
            target_file = module_dir / f"{locale}.json"
            existing = load_json(target_file)
            source_data = info["source_data"]

            # 合并
            merged = existing.copy()
            for key in info["missing_keys"]:
                if key in source_data:
                    merged[key] = source_data[key]   # 后续可加翻译逻辑

            if save_if_changed(target_file, merged):
                print(f"  ★ {module_name}/{locale}.json 已更新")
            else:
                print(f"  ✓ {module_name}/{locale}.json 无需更新")

    print("\n=== 全部处理完成 ===")


def collect_texts(node: Any) -> list[str]:
    # 保持你原来的收集逻辑...
    texts: list[str] = []
    seen = set()
    def add(t):
        if t and t not in seen:
            seen.add(t)
            texts.append(t)
    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(v, str):
                    add(v)
                    if k == "pattern":
                        for m in CHINESE_RUN.findall(v):
                            add(m)
                else:
                    walk(v)
        elif isinstance(n, list):
            for x in n: walk(x)
    walk(node)
    return texts


def translate_batch(locale: str, texts: list[str]) -> dict[str, str]:
    if not texts:
        return {}
    try:
        translator = GoogleTranslator(source="zh-CN", target=TRANSLATOR_TARGETS.get(locale, "en"))
        results = translator.translate_batch(texts)
        return dict(zip(texts, results))
    except Exception as e:
        print(f"  Translation failed for {locale}: {e}")
        return {t: t for t in texts}


if __name__ == "__main__":
    main()