from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

import cv2
from ok.feature.Box import Box

_PUNCT_TRANSLATION_TABLE = str.maketrans(
    {
        "·": "：",
        ":": "：",
        " ": "",
        "\u3000": "",
        "[": "【",
        "]": "】",
        "(": "（",
        ")": "）",
        "|": "",
    }
)

_ONLY_ASCII_RE = re.compile(r"^[A-Za-z0-9]+$")
_ONLY_NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")
_INT_RE = re.compile(r"\d+")
_CN_RE = re.compile(r"[\u4e00-\u9fff]+")
_CN_OR_DIGIT_RE = re.compile(r"[\u4e00-\u9fff0-9]+")


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
            f"{e.name}+{e.level if e.level is not None else ''}" for e in self.entries
        )
        return f"{self.name}|{self.source or ''}|{entries_key}"


@dataclass(frozen=True)
class _EssencePanelParse:
    name: str
    source: str | None
    entry_boxes: tuple[Box, ...]
    entry_names: tuple[str, ...]
    is_gold: bool


def _normalize_text(text: str) -> str:
    text = (text or "").strip().translate(_PUNCT_TRANSLATION_TABLE)
    # OCR 可能混入不可见空白（如 \r/\n/\t/不间断空格），统一移除避免输出断行/错位
    return re.sub(r"\s+", "", text)


def _looks_like_noise(text: str) -> bool:
    if not text:
        return True
    if len(text) <= 1:
        return True
    if _ONLY_ASCII_RE.match(text) or _ONLY_NUMBER_RE.match(text):
        return True
    return False


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _extract_essence_name(text: str) -> str:
    """
    用正则提取中文信息，避免 OCR 里混入的小圆点/句号/噪声字符。
    目标形态：无暇基质：流转（保留中文冒号）。
    """
    if not text:
        return ""
    text = text.replace(":", "：").replace("·", "：")
    match = re.search(r"([\u4e00-\u9fff]+基质)[:：]([\u4e00-\u9fff]+)", text)
    if match:
        return f"{match.group(1)}：{match.group(2)}"
    return "".join(_CN_RE.findall(text))


def _extract_entry_name(text: str) -> str:
    if not text:
        return ""
    return "".join(_CN_RE.findall(text))


def _extract_source(text: str) -> str:
    if not text:
        return ""
    return "".join(_CN_OR_DIGIT_RE.findall(text))


def _is_gold_by_name(name: str) -> bool:
    # 第一版：用“无暇/无瑕”做金色判定（遍历时会再用图标色判定）
    return "无暇" in name or "无瑕" in name


def _parse_int(text: str) -> int | None:
    matches = _INT_RE.findall(text)
    if not matches:
        return None
    try:
        return max(int(m) for m in matches)
    except ValueError:
        return None


def parse_essence_panel(
    texts: Sequence[Box], *, max_entries: int = 3
) -> _EssencePanelParse | None:
    """
    从OCR结果中解析基质信息（名称/来源/词条名称），不包含词条等级。
    约定：OCR来自右上角基质信息面板的裁剪区域。
    """
    if not texts:
        return None

    normalized: list[tuple[Box, str]] = []
    for t in texts:
        text = _normalize_text(getattr(t, "name", ""))
        if not text:
            continue
        normalized.append((t, text))

    if not normalized:
        return None

    name_candidates: list[tuple[Box, str]] = [
        (b, s)
        for b, s in normalized
        if "基质" in s and s != "基质" and not _looks_like_noise(s) and len(s) >= 4
    ]
    if not name_candidates:
        return None

    name_box, raw_name = min(name_candidates, key=lambda it: (it[0].y, -len(it[1])))
    name = _extract_essence_name(raw_name)
    if not name:
        return None

    # 词条：优先使用“附加技能”标签定位
    affix_label = next((b for b, s in normalized if s == "附加技能"), None)
    affix_start_y = affix_label.y if affix_label else name_box.y + name_box.height + 10

    entry_candidates: list[tuple[Box, str]] = []
    for b, s in normalized:
        if b.y < affix_start_y:
            continue
        if s in {"附加技能", "基质"}:
            continue
        if _looks_like_noise(s):
            continue
        if s == name:
            continue
        entry_name = _extract_entry_name(s)
        if not entry_name:
            continue
        entry_candidates.append((b, entry_name))

    entry_candidates.sort(key=lambda it: (it[0].y, it[0].x))
    entry_names = _dedupe_keep_order([s for _, s in entry_candidates])
    if max_entries > 0:
        entry_names = entry_names[:max_entries]

    # 保持与 entry_names 同序的 box（用于匹配 +x 的 y 坐标）
    entry_box_by_name: dict[str, Box] = {}
    for b, entry_name in entry_candidates:
        if entry_name not in entry_box_by_name:
            entry_box_by_name[entry_name] = b
    entry_boxes = [entry_box_by_name[n] for n in entry_names if n in entry_box_by_name]

    # 来源：在“名称”与“词条”之间，找一个较长的中文短语
    source_end_y = (
        affix_label.y
        if affix_label
        else (entry_candidates[0][0].y if entry_candidates else None)
    )
    source_candidates: list[tuple[Box, str]] = []
    for b, s in normalized:
        if b.y <= name_box.y + name_box.height:
            continue
        if source_end_y is not None and b.y >= source_end_y:
            continue
        if s in {"附加技能", "基质"}:
            continue
        if _looks_like_noise(s):
            continue
        if "基质" in s:
            continue
        if (_extract_entry_name(s) or "") in entry_names:
            continue
        if len(s) < 3:
            continue
        source_candidates.append((b, s))

    source: str | None = None
    if source_candidates:
        _, raw_source = max(
            source_candidates, key=lambda it: (it[0].y, len(it[1]), it[0].confidence)
        )
        cleaned = _extract_source(raw_source)
        source = cleaned or None

    is_gold = _is_gold_by_name(name)
    return _EssencePanelParse(
        name=name,
        source=source,
        entry_boxes=tuple(entry_boxes),
        entry_names=tuple(entry_names),
        is_gold=is_gold,
    )


def ocr_essence_panel(task) -> list[Box]:
    """
    OCR the essence panel at top-right.
    task 需提供 box_of_screen / ocr 等 ok-script Task API。
    """
    # 右侧面板的“附加技能/词条”区域会略低于 0.50，向下多留一些避免漏掉第 3 条
    panel_box = task.box_of_screen(0.65, 0.05, 0.99, 0.63, name="essence_panel")
    return task.ocr(box=panel_box)


def _levels_frame_processor(cv_image):
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    # 反色+自适应阈值，让右侧“+x”等小字更清晰（不改变尺寸，避免坐标系错位）
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        2,
    )
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def ocr_essence_levels(task) -> list[Box]:
    """
    OCR the "+x" levels on the right side.
    """
    level_box = task.box_of_screen(0.90, 0.28, 0.99, 0.63, name="essence_levels")
    return task.ocr(box=level_box, frame_processor=_levels_frame_processor)


def _attach_levels(
    panel: _EssencePanelParse, level_boxes: Sequence[Box]
) -> tuple[EssenceEntry, ...]:
    candidates: list[tuple[Box, int]] = []
    for b in level_boxes:
        text = _normalize_text(getattr(b, "name", ""))
        level = _parse_int(text)
        if level is None or level <= 0 or level > 20:
            continue
        candidates.append((b, level))

    remaining = candidates[:]
    entries: list[EssenceEntry] = []
    for entry_name, entry_box in zip(
        panel.entry_names, panel.entry_boxes, strict=False
    ):
        best_i = -1
        best_dist = 10**9
        for i, (b, _) in enumerate(remaining):
            if b.x <= entry_box.x:
                continue
            dy = abs(b.y - entry_box.y)
            if dy < best_dist:
                best_dist = dy
                best_i = i
        level: int | None = None
        if best_i >= 0:
            _, level = remaining.pop(best_i)
        entries.append(EssenceEntry(name=entry_name, level=level))
    return tuple(entries)


def read_essence_info(task) -> EssenceInfo | None:
    panel_texts = ocr_essence_panel(task)
    panel = parse_essence_panel(panel_texts)
    if not panel:
        return None

    level_boxes = ocr_essence_levels(task)
    entries = _attach_levels(panel, level_boxes)

    return EssenceInfo(
        name=panel.name,
        source=panel.source,
        entries=entries,
        is_gold=panel.is_gold,
    )
