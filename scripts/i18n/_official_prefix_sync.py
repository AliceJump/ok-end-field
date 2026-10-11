"""限定模块与词条 key，用中文前缀完整标题同步现有核心名称。"""

import json
import re
from pathlib import Path

import json5

STRONG_REGEX_META = re.compile(r"[|^$*+?\\]")


def _category_rules(module: str, categories: dict) -> dict:
    rules = {}
    for category, spec in categories.items():
        prefixes = spec["prefixes"]
        if not prefixes or any(not isinstance(p, str) or not p.strip() for p in prefixes):
            raise ValueError(f"前缀配置不能为空: {module}.{category}")
        for key in spec["keys"]:
            if key in rules:
                raise ValueError(f"重复配置前缀 key: {module}.{key}")
            rules[key] = tuple(prefixes)
    return rules


def load_prefix_overrides(root: Path) -> dict:
    path = root / "scripts" / "i18n" / "official_prefix_overrides.json"
    if not path.exists():
        return {}
    groups = json.loads(path.read_text(encoding="utf-8"))
    return {module: _category_rules(module, categories) for module, categories in groups.items()}


def core_title(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", "", text).strip()
    # 官方各语言用中点、冒号、空格连字符或书名引号分隔前缀与名称。
    parts = re.split(r"[·・:：«“]|(?<=\s)-(?=\s)", text, maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[1].strip(" \t\r\n\u200b").rstrip("»”") or None


def _chinese_index(raw_texts: dict) -> dict:
    index = {}
    for key, text in raw_texts.get("zh_CN", {}).items():
        if isinstance(text, str):
            index.setdefault(text.strip(), []).append(key)
    return index


def _translated_cores(table: dict, groups: list) -> list[list[str]] | None:
    cores = []
    for keys in groups:
        titles = [table[key] for key in keys if isinstance(table.get(key), str) and table[key].strip()]
        names = [core_title(title) for title in titles]
        if not names or any(name is None for name in names):
            return None  # 未确认译文的标题结构时不猜测。
        cores.append(names)
    return cores


def _matches_cores(matcher, groups: list) -> bool:
    return all(any(matcher.fullmatch(core) for core in group) for group in groups)


def _matching_pattern(pattern: str, cores: list) -> dict | None:
    if STRONG_REGEX_META.search(pattern):
        return {"pattern": pattern}  # 保留已有手工正则适配。
    if not pattern:
        return None
    try:
        matcher = re.compile(pattern)
    except re.error:
        return None
    if _matches_cores(matcher, cores):
        return {"pattern": pattern}
    if _matches_cores(re.compile(pattern, re.IGNORECASE), cores):
        return {"pattern": f"(?i){pattern}"}  # 仅大小写差异，保留原文。
    return None


def _updated_locale(current: dict, cores: list) -> dict:
    pattern = current.get("pattern")
    if isinstance(pattern, str):
        matching = _matching_pattern(pattern, cores)
        if matching:
            return matching
    options = {core for group in cores for core in group}
    existing_string = current.get("string")
    if existing_string in options or len(options) != 1:
        return current
    core = options.pop()
    if isinstance(existing_string, str):
        return {"string": core}
    return {"pattern": re.escape(core).replace(r"\ ", " ")}


def _prefixed_node(raw_texts: dict, node: dict, groups: list) -> dict:
    result = dict(node)
    for lang, table in raw_texts.items():
        if lang == "zh_CN":
            continue  # 中文核心名称/配置锚点保持不变。
        cores = _translated_cores(table, groups)
        if cores:
            updated = _updated_locale(node.get(lang, {}), cores)
            if updated:
                result[lang] = updated
    return result


def build_prefixed_nodes(raw_texts: dict, data: dict, rules: dict, cn_index: dict | None = None) -> dict:
    if cn_index is None:
        cn_index = _chinese_index(raw_texts)
    result = dict(data)
    for key, prefixes in rules.items():
        if key not in data:
            print(f"[prefix-sync] 配置的词条 key 不存在: {key}")
            continue
        cn = data[key].get("zh_CN", {})
        name = cn.get("string", cn.get("pattern"))
        if not isinstance(name, str) or not name.strip() or STRONG_REGEX_META.search(name):
            continue
        groups = [cn_index.get(f"{prefix}·{name}", []) for prefix in prefixes]
        if not all(groups):
            print(f"[prefix-sync] 缺少完整官方中文标题，保留 {key}: {name}")
            continue
        result[key] = _prefixed_node(raw_texts, data[key], groups)
    return result


def sync_prefix_overrides(raw_texts: dict, lang_dir: Path, overrides: dict, dry_run: bool, write_json) -> list:
    cn_index = _chinese_index(raw_texts)
    touched = []
    for module, rules in overrides.items():
        path = lang_dir / f"{module}.json"
        if not path.exists():
            continue
        before = json5.loads(path.read_text(encoding="utf-8"))
        after = build_prefixed_nodes(raw_texts, before, rules, cn_index)
        changed = [
            (
                path.name,
                key,
                before[key]["zh_CN"].get("string", before[key]["zh_CN"].get("pattern")),
                lang,
                next(iter(value.values())),
            )
            for key, node in after.items()
            for lang, value in node.items()
            if before.get(key, {}).get(lang) != value
        ]
        touched.extend(changed)
        if changed and not dry_run:
            write_json(path, after)
    return touched
