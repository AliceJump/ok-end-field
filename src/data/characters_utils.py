from typing import Any

from src.data.characters import characters
from src.data.FeatureList import FeatureList


def _get_localized_character_name(lang_accessor: Any, char_key: str, fallback: str) -> str:
    """Get localized character name from self.lang.characters with fallback."""
    if lang_accessor is None:
        return fallback
    try:
        module = getattr(lang_accessor, "characters")
        node = getattr(module, "_data", {}).get(char_key)
        if not isinstance(node, dict):
            return fallback
        text = node.get("string") or node.get("pattern")
        if isinstance(text, str) and text:
            return text
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
