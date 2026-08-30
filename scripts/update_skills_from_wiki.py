# -*- coding: utf-8 -*-
"""
完整脚本：无头浏览器爬取Wiki技能数据并更新角色JSON文件
用法: uv run python scripts/update_skills_from_wiki.py
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

from playwright.sync_api import sync_playwright

from _wiki_utils import extract_text_from_document, parse_skills_from_item_info

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets" / "data" / "character_skills"
OUTPUT_FILE = ROOT / "tmp" / "wiki_skills_full.json"
OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)


def crawl_wiki() -> list:
    """无头浏览器爬取Wiki"""
    print("启动无头浏览器...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)
        
        # 获取角色列表
        print("获取角色列表...")
        catalog_data = None
        
        def on_catalog_response(response):
            nonlocal catalog_data
            if "zonai.skland.com/web/v1/wiki/item/catalog" in response.url and "typeSubId=1" in response.url:
                try:
                    data = json.loads(response.text())
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
        
        # 逐个抓取并更新
        all_characters = []
        
        for i, char in enumerate(characters):
            item_id = char['itemId']
            name = char['name']
            print(f"[{i+1}/{len(characters)}] {name}", end="")
            
            item_info = None
            
            def on_item_response(response):
                nonlocal item_info
                if f"zonai.skland.com/web/v1/wiki/item/info?id={item_id}" in response.url:
                    try:
                        data = json.loads(response.text())
                        if data.get('code') == 0:
                            item_info = data.get('data', {})
                    except:
                        pass
            
            page.on("response", on_item_response)
            
            try:
                page.goto(f"https://wiki.skland.com/endfield/detail?mainTypeId=1&subTypeId=1&gameEntryId={item_id}&header=0",
                          timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            except Exception as e:
                print(f" - 失败: {e}")
                page.remove_listener("response", on_item_response)
                continue
            
            page.remove_listener("response", on_item_response)
            
            skills = parse_skills_from_item_info(item_info) if item_info else []
            all_characters.append({'itemId': item_id, 'name': name, 'skills': skills})
            
            # 立即更新单个角色文件（用名字匹配）
            if skills:
                new_skills_map = {s['skill_type']: s for s in skills}
                
                # 找到匹配的文件
                for f in ASSETS_DIR.glob("*.json"):
                    with open(f, 'r', encoding='utf-8') as fh:
                        old_data = json.load(fh)
                    
                    file_name = old_data.get('name', '')
                    # 支持精确匹配或包含匹配（处理"管理员"匹配"管理员 (男)"）
                    if file_name == name or name.startswith(file_name) or file_name.startswith(name):
                        changed = False
                        for skill in old_data.get("skills", []):
                            skill_type = skill.get("skill_type", "")
                            if skill_type in new_skills_map:
                                new_desc = new_skills_map[skill_type].get('description', '').strip()
                                old_desc = skill.get('description', '')
                                if old_desc != new_desc:
                                    skill['description'] = new_desc
                                    changed = True
                        
                        if changed:
                            with open(f, 'w', encoding='utf-8') as fh:
                                json.dump(old_data, fh, ensure_ascii=False, indent=2)
                            print(f" - {len(skills)}个技能 [已更新]")
                        else:
                            print(f" - {len(skills)}个技能 [无变化]")
                        break
                else:
                    print(f" - {len(skills)}个技能 [文件不存在]")
            else:
                print(f" - {len(skills)}个技能")
            
            # 保存爬取进度
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_characters, f, ensure_ascii=False, indent=2)
            
            time.sleep(0.5)
        
        browser.close()
    
    return all_characters


def main():
    # 爬取并更新
    crawl_wiki()
    
    print("\n完成!")


if __name__ == "__main__":
    main()
