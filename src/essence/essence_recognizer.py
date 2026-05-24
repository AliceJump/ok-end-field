from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from ok.feature.Box import Box

try:
    from opencc import OpenCC  # type: ignore[import-untyped]
except ImportError:
    OpenCC = None


# =========================
# OpenCC
# =========================

_T2S_FALLBACK_TRANSLATION_TABLE = str.maketrans(
    {
        "擊": "击",
        "無": "无",
        "質": "质",
        "轉": "转",
        "號": "号",
        "襲": "袭",
        "術": "术",
        "傷": "伤",
        "熱": "热",
        "電": "电",
        "終": "终",
        "結": "结",
        "識": "识",
    }
)

if OpenCC:
    try:
        _OPENCC_T2S = OpenCC("t2s")
    except Exception:
        _OPENCC_T2S = None
else:
    _OPENCC_T2S = None


# =========================
# normalize
# =========================

_PUNCT_TRANSLATION_TABLE = str.maketrans(
    {
        "·": "：",
        ":": "：",
        "[": "【",
        "]": "】",
        "(": "（",
        ")": "）",
        "\u3000": " ",
    }
)

_LEVEL_RE = re.compile(r"\+(\d+)")
_CN_TEXT_RE = re.compile(r"[\u4e00-\u9fff]+")


def _normalize_text(text: str) -> str:
    text = (text or "").strip()

    text = text.translate(_PUNCT_TRANSLATION_TABLE)

    if _OPENCC_T2S:
        text = _OPENCC_T2S.convert(text)
    else:
        text = text.translate(_T2S_FALLBACK_TRANSLATION_TABLE)

    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# =========================
# data
# =========================

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


# =========================
# helpers
# =========================

def _contains_essence(text: str) -> bool:
    return "基质" in text


def _contains_affix_label(text: str) -> bool:
    return "附加技能" in text


def _is_gold(name: str) -> bool:
    return "无瑕" in name or "无暇" in name


def _extract_level(text: str) -> int | None:
    match = _LEVEL_RE.search(text)
    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def _extract_entry_name(text: str) -> str:
    """
    保留中文，不保留 +3
    """
    return "".join(_CN_TEXT_RE.findall(text))


def _extract_essence_name(text: str) -> str:
    """
    支持：

    无瑕基质：追袭
    无瑕基质追袭
    无暇基质·追袭
    无瑕基质 追袭
    """

    text = text.replace("：", " ")

    match = re.search(
        r"([\u4e00-\u9fff]{1,20}基质)(?:\s*([\u4e00-\u9fff]{1,20}))?",
        text,
    )

    if not match:
        return ""

    left = match.group(1) or ""
    right = match.group(2) or ""

    if right:
        return f"{left}：{right}"

    return left


# =========================
# row cluster
# =========================

def _cluster_rows(
    texts: Sequence[Box],
    *,
    y_threshold: int = 18,
) -> list[list[tuple[Box, str, str]]]:
    """
    OCR 是二维布局，不是纯文本。

    先按 y 聚类成行。
    """

    items: list[tuple[Box, str, str]] = []

    for t in texts:
        raw_text = (getattr(t, "name", "") or "").strip()
        text = _normalize_text(raw_text)

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


# =========================
# parser
# =========================

def parse_essence_panel(texts: Sequence[Box]) -> EssenceInfo | None:
    rows = _cluster_rows(texts)

    if not rows:
        return None

    name: str | None = None
    source: str | None = None
    entries: list[EssenceEntry] = []

    # =========
    # 找 name
    # =========

    name_row_index = -1

    for i, row in enumerate(rows):
        row_text = " ".join(t for _, t, _ in row)

        if _contains_essence(row_text):
            parsed = _extract_essence_name(row_text)

            if parsed:
                name = parsed
                name_row_index = i
                break

    if not name:
        return None

    # =========
    # source
    # =========

    for i in range(name_row_index + 1, len(rows)):
        row = rows[i]

        row_text = " ".join(t for _, t, _ in row)

        if _contains_affix_label(row_text):
            break

        if (
            "基质" not in row_text
            and "附加技能" not in row_text
            and len(row_text) >= 2
        ):
            source = row_text
            break

    # =========
    # entries
    # =========

    affix_start = -1

    for i, row in enumerate(rows):
        row_text = " ".join(t for _, t, _ in row)

        if _contains_affix_label(row_text):
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

        if "基质" in row_text:
            continue

        if "附加技能" in row_text:
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
        is_gold=_is_gold(name),
    )


# =========================
# OCR
# =========================

def ocr_essence_panel(task) -> list[Box]:
    """
    右上角基质面板 OCR
    """

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

    return parse_essence_panel(texts)