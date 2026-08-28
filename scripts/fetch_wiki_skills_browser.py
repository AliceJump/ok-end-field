# -*- coding: utf-8 -*-
"""
用实际浏览器抓取所有角色技能数据
数据结构：widgetCommonMap.xxx.tabDataMap.tab_xxx.intro 包含技能信息
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = ROOT / "tmp_all_characters_full.json"


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
    
    # 遍历所有widget
    for widget_id, widget in widget_common_map.items():
        tab_data_map = widget.get('tabDataMap', {})
        if not tab_data_map:
            continue
        
        # 遍历所有tab
        for tab_key, tab in tab_data_map.items():
            intro = tab.get('intro') or {}
            name = intro.get('name', '')
            skill_type = intro.get('type', '')
            doc_id = intro.get('description', '')
            
            # 只处理技能类型
            if skill_type in ['普通攻击', '战技', '连携技', '终结技']:
                # 获取文档描述
                description = ''
                if doc_id and doc_id in document_map:
                    description = extract_text_from_document(document_map[doc_id])
                
                skills.append({
                    'name': name,
                    'skill_type': skill_type,
                    'description': description,
                })
    
    return skills


def main():
    print("启动浏览器...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)
        
        # ========== 第一步：获取角色列表 ==========
        print("\n===== 获取角色列表 =====")
        
        catalog_data = None
        
        def on_catalog_response(response):
            nonlocal catalog_data
            url = response.url
            if "zonai.skland.com/web/v1/wiki/item/catalog" in url and "typeSubId=1" in url:
                try:
                    body = response.text()
                    data = json.loads(body)
                    if data.get('code') == 0:
                        catalog_data = data.get('data', {})
                except:
                    pass
        
        page.on("response", on_catalog_response)
        
        page.goto("https://wiki.skland.com/endfield/catalog?mainTypeId=1&typeSubId=1&filterIds=&header=0",
                  timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        
        page.remove_listener("response", on_catalog_response)
        
        characters = []
        if catalog_data:
            for cat in catalog_data.get('catalog', []):
                for sub in cat.get('typeSub', []):
                    for item in sub.get('items', []):
                        item_id = item.get('itemId', '')
                        name = item.get('name', '')
                        if item_id and name:
                            characters.append({'itemId': item_id, 'name': name})
        
        print(f"找到 {len(characters)} 个角色")
        
        # ========== 第二步：逐个抓取技能数据 ==========
        print("\n===== 获取技能数据 =====")
        
        all_characters = []
        
        for i, char in enumerate(characters):
            item_id = char['itemId']
            name = char['name']
            print(f"\n[{i+1}/{len(characters)}] {name} (itemId: {item_id})")
            
            # 捕获的API数据
            item_info = None
            
            def on_item_response(response):
                nonlocal item_info
                url = response.url
                if f"zonai.skland.com/web/v1/wiki/item/info?id={item_id}" in url:
                    try:
                        body = response.text()
                        data = json.loads(body)
                        if data.get('code') == 0:
                            item_info = data.get('data', {})
                    except:
                        pass
            
            page.on("response", on_item_response)
            
            # 导航到角色详情页
            detail_url = f"https://wiki.skland.com/endfield/detail?mainTypeId=1&subTypeId=1&gameEntryId={item_id}&header=0"
            try:
                page.goto(detail_url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            except Exception as e:
                print(f"  导航失败: {e}")
                page.remove_listener("response", on_item_response)
                continue
            
            page.remove_listener("response", on_item_response)
            
            # 解析技能
            skills = []
            if item_info:
                skills = parse_skills_from_item_info(item_info)
            
            all_characters.append({
                'itemId': item_id,
                'name': name,
                'skills': skills,
            })
            
            skill_names = [f"{s['name']}({s['skill_type']})" for s in skills]
            print(f"  技能: {skill_names}")
            
            time.sleep(0.5)
        
        browser.close()
    
    # 保存结果
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_characters, f, ensure_ascii=False, indent=2)
    
    print(f"\n===== 完成 =====")
    print(f"保存到 {OUTPUT_FILE}")
    print(f"共 {len(all_characters)} 个角色")


if __name__ == "__main__":
    main()
