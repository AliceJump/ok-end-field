# -*- coding: utf-8 -*-
"""动态配置键声明表。

「动态配置」= 值由**用户输入**载入的配置项（而非项目资源），典型如账号列表
衍生出的下拉选项。这类值不应参与翻译（gettext 按 msgid 精确查询，用户输入
永远无法命中，只会污染 i18n 收集池）。

声明方式：把 config key 加入下方集合；GUI 渲染层（dynamic_config_patch）
据此跳过 tr，直接显示原值。

不要把项目资源类的下拉加进来（如干员列表 characters.json、物品导航
「选择物品」的 get_supported_item_names）——它们是合法待翻译文案。
"""

# 用户输入载入的下拉配置 key（跨任务全局唯一；新增动态配置在此登记）
DYNAMIC_DROPDOWN_KEYS = frozenset({
    '地图账号',       # 物品导航：选项来自账号页账号列表（用户输入）
})


def is_dynamic_dropdown_key(key) -> bool:
    """判断配置 key 是否已声明为动态配置（不参与翻译）。"""
    return key in DYNAMIC_DROPDOWN_KEYS
