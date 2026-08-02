# -*- coding: utf-8 -*-

"""抓取国服官方 Wiki（wiki.skland.com）catalog 简中数据（真实浏览器）。

接口 ``zonai.skland.com/web/v1/wiki/item/catalog`` 需前端签名（401），
且不响应 ``sk-language`` 头（只出简中），因此用真实浏览器打开页面监听响应。

skland 有 15 个类型子类（skport 只有 13 类，缺武器基质/任务/活动/
系统蓝图/装扮/能量淤积点/蚀刻章/档案库），全部抓取以覆盖简中全量。

用法：
    python scripts/capture_wiki_zhcn.py [--out DIR] [--proxy URL]

产物：``tools/wiki_catalog/zh_cn/<stamp>/m1_s<sub>.json``，
随后运行 ``scripts/sync_wiki_item_langs.py`` 合并 zh_CN 节点。
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

PREFIX = "https://zonai.skland.com/web/v1/wiki/item/catalog"

# skland 15 类 typeSubId（mainTypeId 恒为 1）
SUBS = ["1", "2", "4", "7", "20", "3", "5", "6", "19", "16", "18", "8", "9", "21", "22"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"tools/wiki_catalog/zh_cn")
    ap.add_argument("--proxy", default=None, help="e.g. http://127.0.0.1:10808")
    ap.add_argument("--headless", action="store_true", default=True)
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out_dir = (root / args.out).resolve()
    if not out_dir.is_relative_to(root):
        ap.error(f"--out must be inside the repo root: {args.out}")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    lang_dir = out_dir / stamp
    lang_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs = {}
    if not args.headless:
        launch_kwargs["headless"] = False
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    counts = {}
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
            page.goto("https://wiki.skland.com/endfield/catalog?mainTypeId=1&typeSubId=1",
                      timeout=90000, wait_until="domcontentloaded")
        except Exception as e:
            print("init goto:", e, flush=True)
        page.wait_for_timeout(8000)

        for sub in SUBS:
            url = (f"https://wiki.skland.com/endfield/catalog"
                   f"?mainTypeId=1&typeSubId={sub}&filterIds=&header=0")
            try:
                with page.expect_response(
                        lambda r, s=sub:
                        r.url.startswith(PREFIX)
                        and f"typeMainId=1" in r.url
                        and f"typeSubId={s}" in r.url
                        and r.status == 200,
                        timeout=40000) as ri:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                body = ri.value.text()
                d = json.loads(body)
                subs = [s for c in d["data"]["catalog"] for s in c["typeSub"]]
                items = [i for s in subs for i in s.get("items", [])]
                counts[sub] = len(items)
                path = lang_dir / f"m1_s{sub}.json"
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
            except Exception as e:
                counts[sub] = f"ERR {str(e)[:60]}"
            print(f"  m1/s{sub}: {counts[sub]}", flush=True)

        browser.close()

    meta = out_dir / f"{stamp}_summary.json"
    with open(meta, "w", encoding="utf-8") as f:
        json.dump(counts, f, ensure_ascii=False, indent=2)
    print("summary:", meta, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
