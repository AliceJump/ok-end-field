from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Any

from ok.feature.Box import Box
from src.data.lang import normalize as lang_normalize
from src.data.lang import parser as lang_parser


_LEVEL_RE = re.compile(r"\+(\d+)")
_CN_TEXT_RE = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class EssenceEntry:
    name: str
    level: int | None = None


@dataclass(frozen=True)
class EssenceInfo:
    name: str
    source: str | None
    entries: tuple[EssenceEntry, ...]
    is_gold: bool

    @property
    def entry_names(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.entries)

    def key(self) -> str:
        entries_key = "/".join(
            f"{e.name}+{e.level if e.level is not None else ''}"
            for e in self.entries
        )
        return f"{self.name}|{self.source or ''}|{entries_key}"


def _essence_terms(*, context: Any = None, locale: str | None = None) -> dict[str, Any]:
    terms = lang_parser.get_essence_parser_terms(context=context, locale=locale)
    return {
        "essence_keywords": [t for t in terms.get("essence_keywords", []) if t],
        "affix_keywords": [t for t in terms.get("affix_keywords", []) if t],
        "gold_keywords": [t for t in terms.get("gold_keywords", []) if t],
        "name_regex": str(terms.get("name_regex", r"([\u4e00-\u9fff]+)\s*([\u4e00-\u9fff]+)?")),
    }


def _normalize_text(text: str, *, context: Any = None, locale: str | None = None) -> str:
    text = (text or "").strip()

    punctuation_map, t2s_map = lang_normalize.get_text_normalize_tables(context=context, locale=locale)
    if punctuation_map:
        text = text.translate(str.maketrans(punctuation_map))
    if t2s_map:
        text = text.translate(str.maketrans(t2s_map))

    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _contains_any(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _contains_essence(text: str, terms: dict[str, Any]) -> bool:
    return _contains_any(text, terms["essence_keywords"])


def _contains_affix_label(text: str, terms: dict[str, Any]) -> bool:
    return _contains_any(text, terms["affix_keywords"])


def _is_gold(name: str, terms: dict[str, Any]) -> bool:
    return _contains_any(name, terms["gold_keywords"])


def _extract_level(text: str) -> int | None:
    match = _LEVEL_RE.search(text)
    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def _extract_entry_name(text: str) -> str:
    return "".join(_CN_TEXT_RE.findall(text))


def _extract_essence_name(text: str, terms: dict[str, Any]) -> str:
    text = text.replace("：", " ")
    match = re.search(terms["name_regex"], text)
    if not match:
        return ""

    left = match.group(1) or ""
    right = match.group(2) or ""
    if right:
        return f"{left}：{right}"
    return left


def _cluster_rows(
    texts: Sequence[Box],
    *,
    context: Any = None,
    locale: str | None = None,
    y_threshold: int = 18,
) -> list[list[tuple[Box, str, str]]]:
    items: list[tuple[Box, str, str]] = []

    for t in texts:
        raw_text = (getattr(t, "name", "") or "").strip()
        text = _normalize_text(raw_text, context=context, locale=locale)

        if not text:
            continue

        items.append((t, text, raw_text))

    items.sort(key=lambda x: (x[0].y, x[0].x))

    rows: list[list[tuple[Box, str, str]]] = []

    for item in items:
        box, _, _ = item

        if not rows:
            rows.append([item])
            continue

        last_row = rows[-1]
        last_y = last_row[0][0].y

        if abs(box.y - last_y) <= y_threshold:
            last_row.append(item)
        else:
            rows.append([item])

    for row in rows:
        row.sort(key=lambda x: x[0].x)

    return rows


def parse_essence_panel(
    texts: Sequence[Box],
    *,
    context: Any = None,
    locale: str | None = None,
) -> EssenceInfo | None:
    terms = _essence_terms(context=context, locale=locale)
    rows = _cluster_rows(texts, context=context, locale=locale)

    if not rows:
        return None

    name: str | None = None
    source: str | None = None
    entries: list[EssenceEntry] = []

    name_row_index = -1

    for i, row in enumerate(rows):
        row_text = " ".join(t for _, t, _ in row)

        if _contains_essence(row_text, terms):
            parsed = _extract_essence_name(row_text, terms)

            if parsed:
                name = parsed
                name_row_index = i
                break

    if not name:
        return None

    for i in range(name_row_index + 1, len(rows)):
        row = rows[i]
        row_text = " ".join(t for _, t, _ in row)

        if _contains_affix_label(row_text, terms):
            break

        if (
            not _contains_essence(row_text, terms)
            and not _contains_affix_label(row_text, terms)
            and len(row_text) >= 2
        ):
            source = row_text
            break

    affix_start = -1

    for i, row in enumerate(rows):
        row_text = " ".join(t for _, t, _ in row)

        if _contains_affix_label(row_text, terms):
            affix_start = i + 1
            break

    if affix_start < 0:
        affix_start = name_row_index + 1

    for i in range(affix_start, len(rows)):
        row = rows[i]

        row_text = " ".join(t for _, t, _ in row)
        row_raw_text = " ".join(raw for _, _, raw in row)

        if not row_text:
            continue

        if _contains_essence(row_text, terms):
            continue

        if _contains_affix_label(row_text, terms):
            continue

        entry_name = _extract_entry_name(row_raw_text)

        if not entry_name:
            continue

        level = _extract_level(row_text)

        entries.append(
            EssenceEntry(
                name=entry_name,
                level=level,
            )
        )

    return EssenceInfo(
        name=name,
        source=source,
        entries=tuple(entries[:3]),
        is_gold=_is_gold(name, terms),
    )


def ocr_essence_panel(task) -> list[Box]:
    panel_box = task.box_of_screen(
        0.65,
        0.05,
        0.99,
        0.63,
        name="essence_panel",
    )

    return task.ocr(box=panel_box)


def read_essence_info(task) -> EssenceInfo | None:
    texts = ocr_essence_panel(task)

    return parse_essence_panel(texts, context=task)
