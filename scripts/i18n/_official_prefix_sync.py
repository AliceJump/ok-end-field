"""限定模块与词条 key，用中文前缀完整标题同步现有核心名称。"""

import json
import re
from pathlib import Path

import json5

STRONG_REGEX_META = re.compile(r"[|^$*+?\\]")


def load_prefix_overrides(root: Path) -> dict:
    path = root / "scripts" / "i18n" / "official_prefix_overrides.json"
    if not path.exists():
        return {}
    groups = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for module, categories in groups.items():
        rules = {}
        for category, spec in categories.items():
            prefixes = spec["prefixes"]
            if not prefixes or any(not isinstance(p, str) or not p.strip() for p in prefixes):
                raise ValueError(f"前缀配置不能为空: {module}.{category}")
            for key in spec["keys"]:
                if key in rules:
                    raise ValueError(f"重复配置前缀 key: {module}.{key}")
                rules[key] = tuple(prefixes)
        result[module] = rules
    return result


def _core_title(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", "", text).strip()
    # 官方各语言用中点、冒号、空格连字符或书名引号分隔前缀与名称。
    parts = re.split(r"[·・:：]|\s+-\s+|[«“]", text, maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[1].strip(" \t\r\n\u200b").rstrip("»”") or None


def build_prefixed_nodes(raw_texts: dict, data: dict, rules: dict, cn_index: dict | None = None) -> dict:
    if cn_index is None:
        cn_index = {}
        for key, text in raw_texts.get("zh_CN", {}).items():
            cn_index.setdefault(text.strip(), []).append(key)
    result = dict(data)
    for key, prefixes in rules.items():
        if key not in data:
            print(f"[prefix-sync] 配置的词条 key 不存在: {key}")
            continue
        node = dict(data[key])
        cn = node.get("zh_CN", {})
        name = cn.get("string", cn.get("pattern"))
        if not isinstance(name, str) or not name.strip() or STRONG_REGEX_META.search(name):
            continue
        groups = [cn_index.get(f"{prefix}·{name}", []) for prefix in prefixes]
        if not all(groups):
            print(f"[prefix-sync] 缺少完整官方中文标题，保留 {key}: {name}")
            continue
        for lang, table in raw_texts.items():
            if lang == "zh_CN":
                continue  # 中文核心名称/配置锚点保持不变。
            titles = [
                [
                    re.sub(r"<[^>]+>", "", table[k]).strip()
                    for k in keys
                    if isinstance(table.get(k), str) and table[k].strip()
                ]
                for keys in groups
            ]
            if not all(titles):
                continue
            current = node.get(lang, {})
            pattern = current.get("pattern")
            if isinstance(pattern, str):
                if STRONG_REGEX_META.search(pattern):
                    continue  # 保留已有手工正则适配。
                try:
                    matcher = re.compile(pattern)
                except re.error:
                    matcher = None
                if pattern and matcher and all(any(matcher.search(title) for title in group) for group in titles):
                    continue  # 当前核心名仍匹配所有前缀，无实质变化。
                if pattern and matcher:
                    casefold_matcher = re.compile(pattern, re.IGNORECASE)
                    if all(any(casefold_matcher.search(title) for title in group) for group in titles):
                        node[lang] = {"pattern": f"(?i){pattern}"}
                        continue  # 官方标题只存在大小写差异时修正匹配能力，保留原文。
            cores = [_core_title(title) for group in titles for title in group]
            if not cores or any(core is None for core in cores):
                continue  # 未确认译文的标题结构时不猜测。
            options = set(cores)
            existing_string = current.get("string")
            if existing_string in options or len(options) != 1:
                continue
            core = options.pop()
            node[lang] = (
                {"string": core}
                if isinstance(existing_string, str)
                else {"pattern": re.escape(core).replace(r"\ ", " ")}
            )
        result[key] = node
    return result


def sync_prefix_overrides(raw_texts: dict, lang_dir: Path, overrides: dict, dry_run: bool, write_json) -> list:
    cn_index = {}
    for key, text in raw_texts.get("zh_CN", {}).items():
        cn_index.setdefault(text.strip(), []).append(key)
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
