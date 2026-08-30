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

from _wiki_utils import extract_text_from_document, parse_skills_from_item_info

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = ROOT / "tmp_all_characters_full.json"


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
    
    print("\n===== 完成 =====")
    print(f"保存到 {OUTPUT_FILE}")
    print(f"共 {len(all_characters)} 个角色")


if __name__ == "__main__":
    main()
