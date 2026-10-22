"""Official map ``mark/list`` payload helpers for item navigation.

The zipline graph already reads user ``saveMarks`` for route planning. Item
navigation also needs the same payload so user-built industrial facilities
such as power piles and repeaters can be discovered dynamically instead of
being absent from the bundled ``assets/items/map/summary.json``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

MapPoint = dict[str, float]
ItemMap = dict[str, list[MapPoint]]


def _normalize_names(values: str | Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return {values}
    return {value for value in values if value}


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def extract_mark_points(
    payload: Any,
    *,
    map_id: str = "",
    names: str | Iterable[str] | None = None,
    user_only: bool = False,
) -> ItemMap:
    """Return marker points grouped by template name from a ``mark/list`` response.

    Both official ``marks`` and user ``saveMarks`` are included. The latter is
    where user-built facilities such as power piles and repeaters appear. Set
    ``user_only`` to keep only ``saveMarks``.
    """

    if not isinstance(payload, dict):
        return {}

    data = payload.get("data")
    if not isinstance(data, dict):
        return {}

    templates: dict[str, str] = {}
    for template in data.get("markTemplates") or []:
        if not isinstance(template, dict):
            continue
        template_id = template.get("id")
        name = template.get("name")
        if template_id is None or not name:
            continue
        templates[str(template_id).strip()] = str(name).strip()

    name_filter = _normalize_names(names)
    wanted_map = str(map_id or "").strip()
    result: ItemMap = {}

    def add_mark(mark: Any) -> None:
        if not isinstance(mark, dict):
            return
        template_id = str(mark.get("templateId") or "").strip()
        name = templates.get(template_id)
        if not name:
            return
        if name_filter is not None and name not in name_filter:
            return

        node_map_id = str(mark.get("mapId") or "").strip()
        if wanted_map and node_map_id and node_map_id != wanted_map:
            return

        pos = mark.get("pos")
        if not isinstance(pos, dict):
            return
        x = _finite_float(pos.get("x"))
        y = _finite_float(pos.get("y"))
        z = _finite_float(pos.get("z"))
        if x is None or y is None or z is None:
            return

        result.setdefault(name, []).append({"x": x, "y": y, "z": z})

    if not user_only:
        for mark in data.get("marks") or []:
            add_mark(mark)
    for mark in data.get("saveMarks") or []:
        add_mark(mark)

    return result


def merge_item_maps(maps: Iterable[ItemMap]) -> ItemMap:
    """Merge item maps and drop exact coordinate duplicates."""

    merged: ItemMap = {}
    seen: set[tuple[str, int, int, int]] = set()

    for item_map in maps:
        for name, points in item_map.items():
            bucket = merged.setdefault(name, [])
            for point in points:
                key = (
                    name,
                    round(float(point.get("x", 0.0)), 3),
                    round(float(point.get("y", 0.0)), 3),
                    round(float(point.get("z", 0.0)), 3),
                )
                if key in seen:
                    continue
                seen.add(key)
                bucket.append(point)

    return merged


__all__ = ["ItemMap", "MapPoint", "extract_mark_points", "merge_item_maps"]
