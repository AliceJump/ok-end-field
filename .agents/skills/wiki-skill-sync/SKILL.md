---
name: wiki-skill-sync
description: Sync character skill data from official wiki (森空岛/第三方wiki) to local JSON files. Use when updating character skills, fixing skill descriptions, adding missing effects, or verifying skill data accuracy against wiki sources.
---

# Wiki Skill Sync

## Overview

Use this workflow to synchronize character skill data from official wiki sources to local `assets/data/character_skills/*.json` files. This ensures skill names, descriptions, effects, and numerical values match the authoritative wiki data.

## Data Sources

| Source | URL | Content |
|---|---|---|
| 森空岛官方wiki | wiki.skland.com/endfield | Skill names, descriptions, multipliers, effects |
| Endfield Talos Wiki | endfield.wiki.gg | English translations, alternative data |
| 华法琳Wiki | warfarin.wiki/cn/operators | Comprehensive skill data with multipliers |

## Workflow

### 1. Capture Wiki Data

```bash
# Capture official wiki data (requires Playwright)
uv run --locked python scripts/data-capture/capture_skland_operator_details.py

# Or capture specific operators only
uv run --locked python scripts/data-capture/capture_skland_operator_details.py --limit 5
```

This creates a snapshot in `tools/wiki_catalog/operator_details/<timestamp>/`.

### 2. Analyze Skill Differences

```bash
# Analyze specific operator
uv run --locked python scripts/skill-data/analyze_operator_skills.py --operator <干员名> --stdout

# Or analyze all operators
uv run --locked python scripts/skill-data/analyze_operator_skills.py --stdout
```

The script generates:
- `operator_skill_analysis.json` - Structured skill data
- `operator_skill_review.md` - Human-readable review report

### 3. Update Local JSON Files

Based on the analysis, update `assets/data/character_skills/<filename>.json`:

```json
{
    "character_id": "xxx",
    "wiki_item_id": "2116",
    "name": "干员名",
    "star": 6,
    "element": "自然",
    "profession": "突击",
    "weapon_type": "施术单元",
    "skills": [
        {
            "skill_id": "skill_id",
            "name": "技能名",
            "skill_type": "普通攻击|战技|连携技|终结技",
            "element": "自然",
            "has_enhancement": false,
            "enhancement": null,
            "description": "技能描述",
            "damage_multiplier": null,
            "stagger_value": 0,
            "cooldown": null,
            "spirit_cost": 0,
            "effects": []
        }
    ]
}
```

### 4. Verify Changes

```bash
# Re-run analysis to verify changes
uv run --locked python scripts/skill-data/analyze_operator_skills.py --operator <干员名> --stdout
```

Check that:
- `flagged_skills` count is 0 or minimal
- `review_flags` are only cosmetic issues (e.g., whitespace differences)

## Common Skill Fields

| Field | Description | Example |
|---|---|---|
| `skill_id` | Internal identifier | `typhoeus_skill` |
| `name` | Chinese skill name | `风矢穿林` |
| `skill_type` | Skill category | `战技` |
| `stagger_value` | Stagger damage | `17` |
| `cooldown` | Cooldown time | `21秒` |
| `spirit_cost` | SP cost | `100` |
| `effects` | Status effects applied | `[{"effect_id": "STATUS_HOVERING", "value": 1}]` |

## Effect ID Reference

Common effect IDs from `src/data/effects.py`:

| ID | Name |
|---|---|
| `STATUS_HOVERING` | 浮空状态 |
| `STATUS_SLOW` | 缓速 |
| `VULN_NATURAL_BURST` | 自然爆发易伤 |
| `ATTACH_NATURAL` | 自然附着 |
| `TRIGGER_EXECUTION` | 处决攻击 |
| `TRIGGER_POWER_SHOT` | 强化射击 |
| `TRIGGER_HUNTRESS_GAZE` | 猎手的注视 |
| `TRIGGER_HAIL_OF_ARROWS` | 箭雨 |
| `TRIGGER_HAIL_OF_ARROWS_EMPOWERED` | 强化箭雨 |
| `STACK_SIGN` | 启示层数 |
| `STACK_HUNTING_ARROW` | 猎矢数量 |
| `BARRAGE_ARRAY` | 箭阵区域 |

## Third-Party Wiki Scraping

For endfield.wiki.gg data:

```bash
# Sync character language data
uv run --locked python scripts/i18n/sync_character_langs.py
```

This fetches operator infoboxes from MediaWiki API and syncs language translations.

## Tips

- Always run `uv sync --locked` before running scripts
- New operators may need `ZH_KEY_MAP` entry in `sync_character_langs.py`
- Skill descriptions may have minor whitespace differences - check content, not formatting
- Use `--limit N` to capture only first N operators for faster testing
