# -*- coding: utf-8 -*-

"""抓取官方 Wiki（wiki.skport.com）catalog 多语言数据（真实浏览器）。

接口 ``zonai.skport.com/web/v1/wiki/item/catalog`` 的 sign 由前端自动生成
（免登录，但无法离线伪造），因此必须用真实浏览器访问页面，监听 API 响应。

语言由页面 UI 语言开关决定（切换会更新 SK_THEME_INFO 的 region 并刷新页面），
语言代码按抓取目录命名：en / ja / ko / ru / th / id / zh-Hant。

用法：
    python scripts/capture_wiki_catalog.py [--out DIR] [--proxy URL]

依赖：playwright + 系统 Chrome（channel="chrome"）；建议走代理。
产物：``tools/wiki_catalog/by_lang/<stamp>_<lang>/m<main>_s<sub>.json``，
随后运行 ``scripts/sync_wiki_item_langs.py`` 归类。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

PREFIX = "https://zonai.skport.com/web/v1/wiki/item/catalog"

# (标签, 语言代码, 是否首次语言)
LANGS = [
    ("English", "en", True),
    ("日本語", "ja", False),
    ("한국어", "ko", False),
    ("Pусский", "ru", False),
    ("ภาษาไทย", "th", False),
    ("Indonesia", "id", False),
    ("繁體中文", "zh-Hant", False),
]

# (mainTypeId, typeSubId)
COMBOS = [
    ("1", "1"), ("1", "2"), ("1", "6"), ("1", "16"), ("1", "5"),
    ("1", "4"), ("1", "15"), ("1", "3"), ("1", "17"),
    ("2", "10"), ("2", "9"), ("3", "13"), ("3", "14"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"tools/wiki_catalog/by_lang")
    ap.add_argument("--proxy", default=None, help="e.g. http://127.0.0.1:10808")
    ap.add_argument("--headless", action="store_true", default=True)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_dir = (root / args.out).resolve()
    if not out_dir.is_relative_to(root):
        ap.error(f"--out must be inside the repo root: {args.out}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    launch_kwargs = {}
    if not args.headless:
        launch_kwargs["headless"] = False
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    summary = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception as e:
            print("system chrome unavailable, using bundled chromium:", e, flush=True)
            browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)

        try:
            page.goto("https://wiki.skport.com/endfield/catalog?mainTypeId=1&typeSubId=1",
                      timeout=90000, wait_until="domcontentloaded")
        except Exception as e:
            print("init goto:", e, flush=True)
        page.wait_for_timeout(8000)

        for label, code, first in LANGS:
            lang_dir = out_dir / f"{stamp}_{code}"
            lang_dir.mkdir(parents=True, exist_ok=True)
            if not first:
                try:
                    page.click("#lang .HLang__TextBlock-bWHShx", timeout=10000)
                    page.wait_for_timeout(500)
                    page.click(f"#lang .HLang__LangOption-ftlMmG >> text='{label}'",
                               timeout=10000, force=True)
                    page.wait_for_load_state("domcontentloaded", timeout=30000)
                except Exception as e:
                    print(f"[{code}] switch fail: {e}", flush=True)
                page.wait_for_timeout(6000)
            print(f"== language {code} (html lang={page.evaluate('() => document.documentElement.lang')})",
                  flush=True)

            counts = {}
            for main_id, sub_id in COMBOS:
                url = (f"https://wiki.skport.com/endfield/catalog"
                       f"?mainTypeId={main_id}&typeSubId={sub_id}&filterIds=&header=0")
                try:
                    with page.expect_response(
                            lambda r, m=main_id, s=sub_id:
                            r.url.startswith(PREFIX)
                            and f"typeMainId={m}" in r.url
                            and f"typeSubId={s}" in r.url
                            and r.status == 200,
                            timeout=40000) as ri:
                        page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    body = ri.value.text()
                    d = json.loads(body)
                    subs = [s for c in d["data"]["catalog"] for s in c["typeSub"]]
                    items = [i for s in subs for i in s.get("items", [])]
                    counts[f"{main_id}/{sub_id}"] = len(items)
                    path = lang_dir / f"m{main_id}_s{sub_id}.json"
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                except Exception as e:
                    counts[f"{main_id}/{sub_id}"] = f"ERR {str(e)[:60]}"
                print(f"  m{main_id}/s{sub_id}: {counts[f'{main_id}/{sub_id}']}", flush=True)
            summary[code] = counts

        browser.close()

    meta = out_dir / f"{stamp}_summary.json"
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("summary:", meta, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
