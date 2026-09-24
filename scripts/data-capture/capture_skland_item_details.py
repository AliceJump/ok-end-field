"""抓取森空岛《终末地》WIKI 的武器/装备/武器基质等物品详情数据。

复用 capture_skland_operator_details 的浏览器拦截模式：官方详情接口
``zonai.skland.com/web/v1/wiki/item/info`` 需要前端签名，因此用真实浏览器
打开 WIKI 页面并监听已签名的响应。

用法：
    python scripts/data-capture/capture_skland_item_details.py
    python scripts/data-capture/capture_skland_item_details.py --subtypes 2 --limit 3

产物默认写入 ``tools/wiki_catalog/item_details/<timestamp>/``（gitignore 排除）：
- ``<sub>_<itemId>_<name>.json``  完整 item/info 原始 JSON
- ``rendered_text/<sub>_<itemId>_<name>.txt``  详情页渲染文本
- ``manifest.json``  索引与抓取清单
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import Response

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
CATALOG_API = "https://zonai.skland.com/web/v1/wiki/item/catalog"
DETAIL_API = "https://zonai.skland.com/web/v1/wiki/item/info"
RELATED_API = "https://zonai.skland.com/web/v1/wiki/item/list"

# mainTypeId 恒为 1；typeSubId → 简称
SUBTYPES = {
    "2": "weapon",   # 武器
    "4": "equip",    # 装备
    "7": "matrix",   # 武器基质
}


def _catalog_items(payload: dict, sub_id: str) -> list[dict]:
    """从 catalog 响应中取出指定子类条目并按 itemId 去重。"""
    result: list[dict] = []
    seen: set[str] = set()
    for catalog in payload.get("data", {}).get("catalog", []):
        for type_sub in catalog.get("typeSub", []):
            if str(type_sub.get("id")) != sub_id:
                continue
            for item in type_sub.get("items", []):
                item_id = str(item.get("itemId", ""))
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    result.append(item)
    return result


def _safe_name(name: str) -> str:
    import re

    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return value or "unknown"


def _write_text(path: Path, text: str) -> None:
    import os

    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        normalized = os.path.relpath(normalized, ROOT)
    if normalized.startswith(".." + os.sep):
        raise ValueError(f"路径包含越界片段：{path}")
    real = os.path.realpath(path)
    root_real = os.path.realpath(ROOT)
    if not real.startswith(root_real + os.sep):
        raise ValueError(f"拒绝写入仓库外路径：{path}")
    Path(real).parent.mkdir(parents=True, exist_ok=True)
    with open(real, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=r"tools/wiki_catalog/item_details")
    parser.add_argument("--subtypes", default="2,4,7",
                        help="逗号分隔的 typeSubId（2=武器 4=装备 7=武器基质）")
    parser.add_argument("--proxy", default=None, help="例如 http://127.0.0.1:10808")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--settle-ms", type=int, default=1200, help="详情响应后等待关联请求完成")
    parser.add_argument("--limit", type=int, default=0, help="每个子类仅抓前 N 条；0 表示全部")
    args = parser.parse_args()

    sub_ids = [s.strip() for s in args.subtypes.split(",") if s.strip()]
    unknown = [s for s in sub_ids if s not in SUBTYPES]
    if unknown:
        parser.error(f"未知 typeSubId：{unknown}；支持 {sorted(SUBTYPES)}")

    out_root = (ROOT / args.out).resolve()
    if not out_root.is_relative_to(ROOT):
        parser.error(f"--out 必须位于仓库内：{args.out}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        parser.error(f"无法导入 Playwright；请先运行 uv sync --locked --group dev：{exc}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    snapshot_dir = out_root / stamp
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    rendered_dir = snapshot_dir / "rendered_text"

    launch_kwargs: dict = {"headless": not args.headed}
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    index: list[dict] = []
    failures: list[dict] = []
    catalog_counts: dict[str, int] = {}

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception as exc:
            print(f"system chrome unavailable, using bundled chromium: {exc}", flush=True)
            browser = playwright.chromium.launch(**launch_kwargs)

        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)

        # 预热前端签名模块
        warmup = f"https://wiki.skland.com/endfield/catalog?mainTypeId=1&typeSubId={sub_ids[0]}&header=0"
        try:
            page.goto(warmup, timeout=90000, wait_until="domcontentloaded")
        except Exception as exc:
            print(f"catalog warm-up navigation: {exc}", flush=True)
        page.wait_for_timeout(8000)

        for sub_id in sub_ids:
            kind = SUBTYPES[sub_id]
            catalog_url = (
                f"https://wiki.skland.com/endfield/catalog?mainTypeId=1&typeSubId={sub_id}&header=0"
            )
            try:
                with page.expect_response(
                    lambda r, s=sub_id: (
                        r.url.startswith(CATALOG_API)
                        and "typeMainId=1" in r.url
                        and f"typeSubId={s}" in r.url
                        and r.status == 200
                    ),
                    timeout=60000,
                ) as catalog_info:
                    page.goto(catalog_url, timeout=90000, wait_until="domcontentloaded")
                catalog_payload = json.loads(catalog_info.value.text())
            except Exception as exc:
                failures.append({"subtype": sub_id, "stage": "catalog", "error": str(exc)})
                print(f"[{kind}] catalog FAILED: {exc}", flush=True)
                continue

            items = _catalog_items(catalog_payload, sub_id)
            if args.limit > 0:
                items = items[: args.limit]
            catalog_counts[sub_id] = len(items)
            print(f"[{kind}] catalog: {len(items)} entries", flush=True)

            for position, item in enumerate(items, start=1):
                item_id = str(item["itemId"])
                name = str(item.get("name") or f"{kind}_{item_id}").strip()
                stem = f"{kind}_{item_id}_{_safe_name(name)}"
                detail_url = (
                    f"https://wiki.skland.com/endfield/detail"
                    f"?mainTypeId=1&subTypeId={sub_id}&gameEntryId={item_id}&header=0"
                )
                related: list[Response] = []
                print(f"[{kind} {position:03d}/{len(items):03d}] {name} ({item_id})", flush=True)

                def on_response(response: Response) -> None:
                    if response.status == 200 and response.url.startswith(RELATED_API):
                        related.append(response)

                try:
                    page.on("response", on_response)
                    with page.expect_response(
                        lambda r, exp=item_id: (
                            r.url.startswith(DETAIL_API)
                            and urlsplit(r.url).query == f"id={exp}"
                            and r.status == 200
                        ),
                        timeout=60000,
                    ) as detail_info:
                        page.goto(detail_url, timeout=90000, wait_until="domcontentloaded")

                    detail_body = detail_info.value.text()
                    page.wait_for_timeout(max(args.settle_ms, 0))
                    _write_text(snapshot_dir / f"{stem}.json", detail_body)

                    rendered = page.locator("body").inner_text(timeout=30000)
                    _write_text(rendered_dir / f"{stem}.txt", rendered)

                    related_files: list[str] = []
                    for idx, resp in enumerate(related, start=1):
                        try:
                            body = resp.text()
                        except Exception:
                            continue
                        suffix = "" if idx == 1 else f"_{idx}"
                        rel_path = snapshot_dir / f"{stem}_related{suffix}.json"
                        _write_text(rel_path, body)
                        related_files.append(rel_path.name)

                    entry = {
                        "subtype": sub_id,
                        "kind": kind,
                        "position": position,
                        "item_id": item_id,
                        "name": name,
                        "detail_url": detail_url,
                        "detail_file": f"{stem}.json",
                        "rendered_text_file": f"rendered_text/{stem}.txt",
                        "related_item_files": related_files,
                        "detail_bytes": len(detail_body.encode("utf-8")),
                        "rendered_text_chars": len(rendered),
                    }
                    index.append(entry)
                except Exception as exc:
                    failures.append({
                        "subtype": sub_id, "item_id": item_id, "name": name,
                        "detail_url": detail_url, "error": str(exc),
                    })
                    print(f"  ERROR: {exc}", flush=True)
                finally:
                    page.remove_listener("response", on_response)

        browser.close()

    manifest = {
        "source": "https://wiki.skland.com/endfield/catalog",
        "captured_at": stamp,
        "subtypes": sub_ids,
        "catalog_counts": catalog_counts,
        "success_count": len(index),
        "failure_count": len(failures),
        "items": index,
        "failures": failures,
    }
    _write_text(snapshot_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"done: {len(index)} ok, {len(failures)} failed -> {snapshot_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
