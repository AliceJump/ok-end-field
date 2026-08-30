# -*- coding: utf-8 -*-
"""
Wiki 技能数据解析共享工具函数。

供 fetch_wiki_skills_browser.py 和 update_skills_from_wiki.py 共用，
避免相同代码在两个脚本中重复。
"""


def extract_text_from_document(doc: dict) -> str:
    """从文档中提取纯文本 - 按blockIds顺序"""
    texts = []
    block_ids = doc.get('blockIds', [])
    block_map = doc.get('blockMap', {})

    for block_id in block_ids:
        block = block_map.get(block_id)
        if not block:
            continue
        if block.get('kind') == 'text':
            inline_elements = (block.get('text') or {}).get('inlineElements', [])
            for elem in inline_elements:
                if elem.get('kind') == 'text':
                    text = (elem.get('text') or {}).get('text', '')
                    if text:
                        texts.append(text)
    return ''.join(texts)


def parse_skills_from_item_info(item_info: dict) -> list:
    """从item/info响应中解析技能数据"""
    skills = []

    item = item_info.get('item', {})
    doc = item.get('document', {})
    widget_common_map = doc.get('widgetCommonMap', {})
    document_map = doc.get('documentMap', {})

    for widget_id, widget in widget_common_map.items():
        tab_data_map = widget.get('tabDataMap', {})
        if not tab_data_map:
            continue

        for tab_key, tab in tab_data_map.items():
            intro = tab.get('intro') or {}
            name = intro.get('name', '')
            skill_type = intro.get('type', '')
            doc_id = intro.get('description', '')

            if skill_type in ['普通攻击', '战技', '连携技', '终结技']:
                description = ''
                if doc_id and doc_id in document_map:
                    description = extract_text_from_document(document_map[doc_id])

                skills.append({
                    'name': name,
                    'skill_type': skill_type,
                    'description': description,
                })

    return skills
