import re
import unittest
from pathlib import Path

import polib


I18N_ROOT = Path("i18n")
HAN_RE = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")
POLLUTED_MSGIDS = {
    "Account",
    "Account Config",
    "Account List",
    "Account List Operations",
    "Account Task Override Operations",
    "Auto-render attribute controls after selecting task",
    "Global Config",
    "One account (phone) per line, no password needed",
    "Override this task config per account. Unset items will use the original task config.",
    "Select from account list or existing overrides",
    "Show only items differing from default config",
    "View",
    "アカウント設定",
    "グローバル設定",
}

# 官方 API/Atlos 提供、且官方各语言译名与英文相同（非英文回退）的条目。
# 由 scripts/sync_world_map_langs.py 同步，保持与官方一致。
OFFICIAL_SAME_AS_ENGLISH = {
    "武陵",  # es 官方 = "Wuling"
}


class PoLocaleConsistencyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {
            path.parents[1].name: polib.pofile(str(path))
            for path in I18N_ROOT.glob("*/LC_MESSAGES/ok.po")
        }

    def test_catalog_structure_is_consistent(self):
        errors = []
        for locale, catalog in self.catalogs.items():
            seen = set()
            for entry in catalog:
                if not entry.msgid:
                    continue
                if entry.msgid in seen:
                    errors.append(f"{locale}: duplicate {entry.msgid!r}")
                seen.add(entry.msgid)
                if not entry.msgstr:
                    errors.append(f"{locale}: empty {entry.msgid!r}")
                if set(PLACEHOLDER_RE.findall(entry.msgid)) != set(PLACEHOLDER_RE.findall(entry.msgstr)):
                    errors.append(f"{locale}: placeholders {entry.msgid!r}")

        self.assertEqual(errors, [], "\n".join(errors))

    def test_target_locales_do_not_copy_english_fallbacks(self):
        english = {entry.msgid: entry.msgstr for entry in self.catalogs["en_US"]}
        errors = []
        for locale, catalog in self.catalogs.items():
            if locale == "en_US":
                continue
            for entry in catalog:
                if not HAN_RE.search(entry.msgid):
                    continue
                english_text = english.get(entry.msgid, "")
                if (
                    english_text
                    and entry.msgstr == english_text
                    and entry.msgstr != entry.msgid
                    and entry.msgid not in OFFICIAL_SAME_AS_ENGLISH
                ):
                    errors.append(f"{locale}: English fallback {entry.msgid!r}")
                if locale in {"es_ES", "ko_KR"} and entry.msgstr == entry.msgid:
                    errors.append(f"{locale}: untranslated source {entry.msgid!r}")

        self.assertEqual(errors, [], "\n".join(errors))

    def test_runtime_pollution_ids_are_absent(self):
        errors = []
        for locale, catalog in self.catalogs.items():
            for entry in catalog:
                if entry.msgid in POLLUTED_MSGIDS or entry.msgid.strip().isdigit():
                    errors.append(f"{locale}: polluted id {entry.msgid!r}")

        self.assertEqual(errors, [], "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
