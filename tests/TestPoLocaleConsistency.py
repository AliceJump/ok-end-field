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
# 由 scripts/i18n/sync_world_map_langs.py / sync_map_mark_langs.py 同步，保持与官方一致。
OFFICIAL_SAME_AS_ENGLISH = {
    "武陵",  # es 官方 = "Wuling"
    # mark 模板名（es_MX 官方未本地化，与英文同名）
    "三位一体",  # Triaggelos
    "钱币收集",  # T-Creds
    "聂菲斯",  # Nefarith
    "阮一",  # Ruan Yi
    "晶锥天使",  # Hedron
    "晶锥天使δ",  # Hedron δ
    "芽针",  # Yazhen
    "锦草",  # Jincao
    "息壤气",  # Xiragen
    "惰气",  # Inergen
    # 干员名（wiki 官方 es 译名与英文相同）
    "安塔尔",  # Antal
    "陈千语",  # Chen Qianyu
    "伊冯",  # Yvonne
    "庄方宜",  # Zhuang Fangyi
    "否",  # 西英同形词 "No"
    # ---- 以下由 scripts/i18n/sync_official_i18n_langs.py 从官方解包数据同步 ----
    # 官方 es_MX 未本地化，直接沿用英文译名（与英文相同，非回退）
    "余烬",  # Ember
    "佩丽卡",  # Perlica
    "别礼",  # Last Rite
    "卡契尔",  # Catcher
    "卡缪",  # Camille
    "埃特拉",  # Estella
    "弧光",  # Arclight
    "弭弗",  # Mi Fu
    "昼雪",  # Snowshine
    "梨诺",  # Liino
    "汤汤",  # Tangtang
    "洁尔佩塔",  # Gilberta
    "洛茜",  # Rossi
    "狼卫",  # Wulfgard
    "秋栗",  # Akekuri
    "管理员",  # Endministrator
    "罗丹",  # Rhodagn
    "艾尔黛拉",  # Ardelia
    "艾维文娜",  # Avywenna
    "莱万汀",  # Laevatain
    "萤石",  # Fluorite
    "诀",  # Arcane
    "赛希",  # Xaihi
    "阿列什",  # Alesh
    "骏卫",  # Pogranichnik
    "黎风",  # Lifeng
    "大潘",  # Da Pan
    "天",  # d（官方 MX 缩写）
    "开",  # ON（开关）
    "帝江号",  # Dijiang
}

# es_ES 官方未本地化的角色名，保留中文源文本（msgstr == msgid）是允许的。
ES_UNTRANSLATED_OK = set()


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
                    if entry.msgid not in ES_UNTRANSLATED_OK:
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
