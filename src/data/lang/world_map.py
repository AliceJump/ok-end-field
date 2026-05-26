from __future__ import annotations

from typing import Any

from src.data.lang.locale_data import get_locale_data


def _world_map_payload(locale: str | None = None, context: Any = None) -> dict[str, Any]:
    payload = get_locale_data(locale=locale, context=context)
    world_map = payload.get("world_map", {})
    return world_map if isinstance(world_map, dict) else {}


def get_area_labels(*, locale: str | None = None, context: Any = None) -> dict[str, str]:
    areas = _world_map_payload(locale=locale, context=context).get("areas", {})
    return {str(k): str(v) for k, v in areas.items()}


def canonicalize_area(area_or_id: str, *, locale: str | None = None, context: Any = None) -> str:
    value = str(area_or_id).strip()
    if not value:
        return ""

    payload = _world_map_payload(locale=locale, context=context)
    areas = payload.get("areas", {})
    aliases = payload.get("aliases", {})

    if value in areas:
        return value
    alias_hit = aliases.get(value)
    if alias_hit:
        return str(alias_hit)

    for area_id, label in areas.items():
        if value == str(label):
            return str(area_id)

    return value


def get_area_label(area_or_id: str, *, locale: str | None = None, context: Any = None) -> str:
    area_id = canonicalize_area(area_or_id, locale=locale, context=context)
    areas = get_area_labels(locale=locale, context=context)
    return areas.get(area_id, str(area_or_id))


def get_stage_category_labels(*, locale: str | None = None, context: Any = None) -> dict[str, str]:
    labels = _world_map_payload(locale=locale, context=context).get("stage_categories", {})
    return {str(k): str(v) for k, v in labels.items()}


def canonicalize_stage_category(category_or_id: str, *, locale: str | None = None, context: Any = None) -> str:
    value = str(category_or_id).strip()
    if not value:
        return ""

    payload = _world_map_payload(locale=locale, context=context)
    labels = payload.get("stage_categories", {})
    aliases = payload.get("stage_category_aliases", {})

    if value in labels:
        return value
    alias_hit = aliases.get(value)
    if alias_hit:
        return str(alias_hit)

    for category_id, label in labels.items():
        if value == str(label):
            return str(category_id)

    return value


def get_stage_category_label(category_or_id: str, *, locale: str | None = None, context: Any = None) -> str:
    category_id = canonicalize_stage_category(category_or_id, locale=locale, context=context)
    labels = get_stage_category_labels(locale=locale, context=context)
    return labels.get(category_id, str(category_or_id))
