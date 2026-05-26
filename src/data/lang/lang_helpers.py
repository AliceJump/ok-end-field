from __future__ import annotations

import re
from typing import Any, Iterable


def normalize_locale(locale: Any) -> str:
    return str(locale or "").strip().lower().replace("-", "_")


def to_str_list(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values] if values.strip() else []
    if not isinstance(values, list):
        return []

    result: list[str] = []
    for value in values:
        text = str(value)
        if text.strip():
            result.append(text)
    return result


def resolve_alias(key: str, aliases: dict[str, list[str]]) -> str:
    targets = aliases.get(key, [])
    return str(targets[0]) if targets else key


def iter_alias_related_keys(key: str, aliases: dict[str, list[str]]) -> list[str]:
    discovered: list[str] = []
    seen: set[str] = set()
    queue: list[str] = [resolve_alias(key, aliases)]

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        discovered.append(current)

        for nxt in aliases.get(current, []):
            if nxt not in seen:
                queue.append(nxt)

        for alias_key, targets in aliases.items():
            if current in targets and alias_key not in seen:
                queue.append(alias_key)

    return discovered


def collect_alias_terms(key: str, terms_map: dict[str, Any], aliases: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    seen_terms: set[str] = set()
    for related_key in iter_alias_related_keys(key, aliases):
        for term in to_str_list(terms_map.get(related_key, [])):
            if term not in seen_terms:
                seen_terms.add(term)
                result.append(term)
    return result


def get_alias_mapped_value(key: str, value_map: dict[str, Any], aliases: dict[str, list[str]]) -> Any | None:
    for candidate in iter_alias_related_keys(key, aliases):
        if candidate in value_map:
            return value_map[candidate]
    return None


def build_pattern(terms: list[str] | tuple[str, ...] | str) -> re.Pattern[str]:
    tokens = to_str_list(terms) if isinstance(terms, str) else [str(token) for token in terms if str(token).strip()]
    escaped = [re.escape(token) for token in tokens]
    joined = "|".join(escaped) if escaped else r"^$"
    return re.compile(f"(?:{joined})")


def deep_merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_locale_data(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        merged = deep_merge_dict(merged, part)
    return merged


def get_locale_chain(locale: str, default_locale: str, available_locales: Iterable[str]) -> list[str]:
    available = set(available_locales)
    chain: list[str] = []

    if locale in available:
        chain.append(locale)
    if default_locale in available and default_locale not in chain:
        chain.append(default_locale)

    return chain
