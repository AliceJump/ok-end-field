from __future__ import annotations

from src.data.lang.locales.zh_cn import DATA as ZH_CN_DATA


DATA = {
    **ZH_CN_DATA,
    "ocr": {
        "terms": {
            **ZH_CN_DATA["ocr"]["terms"],
            "storage_node": ["倉儲節點", "仓储节点"],
            "local_storage_node": ["本地倉儲節點", "本地仓储节点"],
            "delivery_list": ["運送委託列表", "运送委托列表"],
            "accept_delivery_entrust": ["接取運送委託", "接取运送委托"],
            "refresh_button": ["刷新"],
            "refresh_cooldown_hint": ["秒後可刷新", "秒后可刷新"],
            "board_zipline": ["登上滑索架"],
            "industrial_area": ["工業", "工业"],
            "pickup_goods": ["取貨", "取货"],
            "delivery_submit": ["交貨", "交货", "送達", "送达"],
            "delivery_fast_prompt": ["請盡快送達", "请尽快送达"],
        },
    },
    "parser": {
        "sequence": {
            "delimiters": ["，"],
        },
        "essence": {
            "essence_keywords": ["基質", "基质"],
            "affix_keywords": ["附加技能"],
            "gold_keywords": ["無瑕", "無暇", "无瑕", "无暇"],
            "name_regex": r"([\u4e00-\u9fff]+(?:基質|基质))\s*([\u4e00-\u9fff]+)?",
        },
    },
    "normalize": {
        "ocr_confusion_map": {
            "别": ["別"],
            "別": ["别"],
        },
        "punctuation_map": {
            **ZH_CN_DATA["normalize"]["punctuation_map"],
        },
        "traditional_to_simplified_map": {
            **ZH_CN_DATA["normalize"]["traditional_to_simplified_map"],
        },
    },
    "ocr_confusion_map": {
        "别": ["別"],
        "別": ["别"],
    },
    "auto_pick": {
        "white_list": [
            "採集", "螢殼蟲", "打開", "蕎花", "灰蘆麥", "灼殼蟲", "苦葉椒", "柱狀菌",
            "酮化灌木", "柑實", "觸碰", "激活", "芽針", "多齒葉", "砂葉",
        ],
        "black_list": ["協議核心", "激活箱子"],
    },
    "warehouse_transfer": {
        "locations": {
            "valley4": "四號谷地",
            "wuling": "武陵",
        },
        "current_location_rules": {
            "wuling": [["武陵倉庫"], ["武陵仓库"]],
            "valley4": [["谷地", "倉庫"], ["谷地", "仓库"]],
        },
        "ocr_patterns": {
            "switch_button": ["倉庫切換", "仓库切换"],
            "confirm": ["確認", "确认"],
            "connected": ["已連接", "已连接"],
            "store": ["存放"],
        },
    },
    "item_warehouse_category_en_by_name": {
        **ZH_CN_DATA["item_warehouse_category_en_by_name"],
        "礦物": "minerals",
        "植物": "plants",
        "產物": "products",
        "採集材料": "gathering_materials",
        "培養素材": "cultivation_materials",
        "可用道具": "consumables",
        "生產工具": "industrial_equipment",
    },
    "item_translation_map": {
        **ZH_CN_DATA["item_translation_map"],
        "藍鐵礦": "bluesteel_ore",
        "高容谷地電池": "high_capacity_valley_battery",
        "源礦": "source_ore",
    },
}
