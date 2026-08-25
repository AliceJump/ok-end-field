# -*- coding: utf-8 -*-

"""干员（Operator）canonical 数据（剥离自内嵌字典，见 assets/data/characters.json）。

数据在 assets/data/characters.json（JSON），本模块仅作薄加载器，
保持原有导入路径兼容：``from src.data.characters import characters, all_list``。

字段说明：
- ``zh``：简中 canonical 名（用于 OCR 匹配与本地化回退）
- ``en``：内部 ID（用于 FeatureList 的 ``xxx_contact`` 枚举匹配，非官方英文名）
- ``stars``：稀有度（4/5/6）

6 语言显示名见 assets/lang/characters.json；官方多语言同步见
scripts/i18n/sync_character_langs.py（数据源 endfield.wiki.gg）。
"""

import json
from pathlib import Path

characters = json.loads(
    (Path(__file__).resolve().parent.parent.parent / "assets" / "data" / "characters.json")
    .read_text(encoding="utf-8")
)
all_list = [i["zh"] for i in characters.values()]
