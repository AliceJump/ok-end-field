---
name: wiki-skill-sync
description: Compare Endfield operator skills with official wiki data and update assets/data/character_skills/*.json. Use for missing skills, descriptions, effects, or numerical values.
---

# Wiki Skill Sync

Use [森空岛](https://wiki.skland.com/endfield) as the primary source; [Endfield Talos Wiki](https://endfield.wiki.gg) and [华法琳 Wiki](https://warfarin.wiki/cn/operators) can help cross-check names and translations. Verify the current source pages rather than relying on an old snapshot.

## Workflow

1. Inspect the target `assets/data/character_skills/<operator>.json` and any existing capture. Capture fresh official data when needed:

   `uv run --locked python scripts/data-capture/capture_skland_operator_details.py`

   Captures go under `tools/wiki_catalog/operator_details/<timestamp>/`. Check the script's supported flags before selecting an individual operator.

2. Compare one operator or the full catalog:

   ```powershell
   uv run --locked python scripts/skill-data/analyze_operator_skills.py --operator <干员名> --stdout
   uv run --locked python scripts/skill-data/analyze_operator_skills.py --stdout
   ```

   Review `operator_skill_analysis.json` and `operator_skill_review.md` alongside the actual wiki page. A flag may be a spacing difference; check the content before editing.

3. Update only verified fields in the operator JSON. Preserve its schema and existing IDs; look up effect IDs in `src/data/effects.py` instead of copying a short example list. Re-run the focused analysis and explain any remaining substantive flags.

For character-name translations from third-party wiki data, use `scripts/i18n/sync_character_langs.py` and check its `ZH_KEY_MAP` when adding an operator. Run scripts with the repository's locked uv environment (`$use-local-venv`).
