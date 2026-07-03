from typing import Any

from src.data.characters import characters
from src.data.lang import get_lang_module_value
from src.data.FeatureList import FeatureList


def _get_localized_character_name(lang_accessor: Any, char_key: str, fallback: str) -> str:
    """Get localized character name from self.lang.characters with fallback."""
    # Use centralized accessor to respect LangNode/build_matcher semantics.
    if lang_accessor is None:
        return fallback
    try:
        val = get_lang_module_value(lang_accessor, "characters", char_key, fallback)
        # Normalize to a string: prefer plain str, else extract pattern if regex-like.
        if isinstance(val, str):
            return val
        # compiled regex (has pattern attribute)
        if hasattr(val, "pattern"):
            try:
                return val.pattern
            except Exception:
                pass
        # list/terms or LangNode fallback to string conversion
        if val is not None:
            try:
                return str(val)
            except Exception:
                pass
    except Exception:
        pass
    return fallback


def get_contact_list_with_feature_list(lang_accessor=None) -> dict[str, str]:
    feature_set = {f.value for f in FeatureList}  # 取 FeatureList 枚举的所有值

    en_to_zh = {
        info["en"] + "_contact": _get_localized_character_name(lang_accessor, char_key, info["zh"])
        for char_key, info in characters.items()
    }
    # 构建英文名 -> 中文名字典，英文名后面加 "_contact"

    common = feature_set & en_to_zh.keys()  # 取 feature_set 和字典 key 的交集

    return {en_to_zh[c]: c for c in common}  # 中文名 -> 英文名字典


def get_localized_name_by_canonical(lang_accessor: Any, canonical_zh: str) -> str | None:
    """Given a canonical Chinese name, return the localized display/pattern.

    Returns localized string when found, otherwise None.
    """
    if not canonical_zh:
        return None
    try:
        for char_key, info in characters.items():
            if canonical_zh == info.get("zh"):
                return _get_localized_character_name(lang_accessor, char_key, info.get("zh"))
    except Exception:
        pass
    return None
