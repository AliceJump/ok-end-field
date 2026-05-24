"""Weapon -> graduate essence requirements (loaded from CSV)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WeaponDataLanguageOption:
    label: str
    path: Path


@dataclass(frozen=True)
class WeaponRequirement:
    weapon: str
    star: str
    entries: tuple[str, ...]


_LANGUAGE_LABEL_MAP: dict[str, str] = {
    "zh": "简体中文",
    "zh_cn": "简体中文",
    "zh-hans": "简体中文",
    "zh_hans": "简体中文",
    "zh_tw": "繁體中文",
    "zh-hant": "繁體中文",
    "zh_hant": "繁體中文",
    "en": "English",
    "en_us": "English",
    "ja": "日本語",
    "ja_jp": "日本語",
    "ko": "한국어",
    "ko_kr": "한국어",
    "es": "Español",
    "es_es": "Español",
}


def _language_label_from_path(path: Path) -> str:
    stem = path.stem.strip().lower()

    if stem == "weapon_data":
        return "简体中文"

    suffix = stem.removeprefix("weapon_data")
    suffix = suffix.lstrip("._-").replace("-", "_").strip().lower()
    if not suffix:
        return "简体中文"

    return _LANGUAGE_LABEL_MAP.get(suffix, suffix)


def discover_weapon_data_language_options(csv_dir: str | Path) -> list[WeaponDataLanguageOption]:
    path = Path(csv_dir)
    if not path.exists() or not path.is_dir():
        return [WeaponDataLanguageOption(label="简体中文", path=path / "weapon_data.csv")]

    candidates = sorted(
        p for p in path.glob("*.csv") if p.is_file() and p.stem.lower().startswith("weapon_data")
    )

    if not candidates:
        return [WeaponDataLanguageOption(label="简体中文", path=path / "weapon_data.csv")]

    raw_labels = [_language_label_from_path(candidate) for candidate in candidates]
    label_counts: dict[str, int] = {}
    for label in raw_labels:
        label_counts[label] = label_counts.get(label, 0) + 1

    options: list[WeaponDataLanguageOption] = []
    for candidate, raw_label in zip(candidates, raw_labels):
        label = raw_label
        if label_counts.get(raw_label, 0) > 1:
            label = f"{raw_label} ({candidate.name})"
        options.append(WeaponDataLanguageOption(label=label, path=candidate))

    return options


def resolve_weapon_data_path(csv_dir: str | Path, language_label: str | None) -> Path:
    options = discover_weapon_data_language_options(csv_dir)

    selected = str(language_label or "").strip()
    if selected:
        for option in options:
            if option.label == selected:
                return option.path

    return options[0].path if options else Path(csv_dir) / "weapon_data.csv"


def load_weapon_data(csv_path: str | Path) -> list[WeaponRequirement]:
    path = Path(csv_path)
    if not path.exists():
        return []

    requirements: list[WeaponRequirement] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue

            normalized = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in row.items() if k}
            weapon = normalized.get("武器", "")
            if not weapon:
                continue

            entries = tuple(
                e for e in (
                    normalized.get("毕业词条1", ""),
                    normalized.get("毕业词条2", ""),
                    normalized.get("毕业词条3", ""),
                )
                if e
            )

            requirements.append(
                WeaponRequirement(
                    weapon=weapon,
                    star=normalized.get("星级", ""),
                    entries=entries,
                )
            )
    return requirements


def match_weapon_requirements(
    requirements: list[WeaponRequirement],
    entry_names: tuple[str, ...] | list[str],
) -> list[WeaponRequirement]:
    entry_set = set(entry_names)
    matched: list[WeaponRequirement] = []
    for req in requirements:
        req_set = set(req.entries)
        if req_set and req_set == entry_set:
            matched.append(req)
    return matched
