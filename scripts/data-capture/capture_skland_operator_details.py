"""抓取森空岛《终末地》WIKI 的完整干员详情数据。

官方详情接口 ``zonai.skland.com/web/v1/wiki/item/info`` 需要前端生成签名，
因此使用真实浏览器打开 WIKI 页面并监听已签名的响应。抓取内容包括：

- 最新干员目录；
- 每名干员的完整 ``item/info`` 原始 JSON；
- 详情页引用物品的 ``item/list`` 原始 JSON；
- 全局干员池、武器池数据；
- 详情页渲染后的纯文本，便于人工核对技能、倍率、属性、潜能等；
- 索引与抓取清单。

用法：
    python scripts/data-capture/capture_skland_operator_details.py
    python scripts/data-capture/capture_skland_operator_details.py --proxy http://127.0.0.1:10808

产物默认写入 ``tools/wiki_catalog/operator_details/<timestamp>/``。该目录已由
``tools/wiki_catalog/`` 的 gitignore 规则排除，不会误提交大量原始数据。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Response, sync_playwright

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PAGE = "https://wiki.skland.com/endfield/catalog?mainTypeId=1&typeSubId=1&header=0"
CATALOG_API = "https://zonai.skland.com/web/v1/wiki/item/catalog"
DETAIL_API = "https://zonai.skland.com/web/v1/wiki/item/info"
WIKI_API_PREFIX = "https://zonai.skland.com/web/v1/wiki/"

_CAPTURE_PATHS = {
    "/web/v1/wiki/item/list",
    "/web/v1/wiki/char-pool",
    "/web/v1/wiki/weapon-pool",
}
_GLOBAL_FILES = {
    "/web/v1/wiki/char-pool": "char_pool.json",
    "/web/v1/wiki/weapon-pool": "weapon_pool.json",
}


def _safe_name(name: str) -> str:
    """生成 Windows 可用且稳定的文件名片段。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return value or "unknown"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _catalog_items(payload: dict) -> list[dict]:
    """从 catalog 响应中取出干员条目并按 itemId 去重。"""
    result: list[dict] = []
    seen: set[str] = set()
    for catalog in payload.get("data", {}).get("catalog", []):
        for type_sub in catalog.get("typeSub", []):
            if str(type_sub.get("id")) != "1":
                continue
            for item in type_sub.get("items", []):
                item_id = str(item.get("itemId", ""))
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    result.append(item)
    return result


def _response_body(response: Response) -> str | None:
    try:
        return response.text()
    except Exception as exc:
        print(f"  response read failed: {response.url}: {exc}", flush=True)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=r"tools/wiki_catalog/operator_details")
    parser.add_argument("--proxy", default=None, help="例如 http://127.0.0.1:10808")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--settle-ms", type=int, default=1500, help="详情响应后等待关联请求完成")
    parser.add_argument("--limit", type=int, default=0, help="仅抓前 N 名；0 表示全部")
    args = parser.parse_args()

    out_root = (ROOT / args.out).resolve()
    if not out_root.is_relative_to(ROOT):
        parser.error(f"--out 必须位于仓库内：{args.out}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    snapshot_dir = out_root / stamp
    details_dir = snapshot_dir / "details"
    related_dir = snapshot_dir / "related_items"
    rendered_dir = snapshot_dir / "rendered_text"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict = {"headless": not args.headed}
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    index: list[dict] = []
    failures: list[dict] = []
    global_saved: set[str] = set()
    current_responses: list[Response] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception as exc:
            print(f"system chrome unavailable, using bundled chromium: {exc}", flush=True)
            browser = playwright.chromium.launch(**launch_kwargs)

        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.set_default_timeout(60000)

        def capture_response(response: Response) -> None:
            if response.status != 200 or not response.url.startswith(WIKI_API_PREFIX):
                return
            path = urlsplit(response.url).path
            if path not in _CAPTURE_PATHS:
                return
            current_responses.append(response)

        page.on("response", capture_response)

        # 首次打开用于初始化前端签名模块；随后再次导航并精确捕获 200 响应。
        try:
            page.goto(CATALOG_PAGE, timeout=90000, wait_until="domcontentloaded")
        except Exception as exc:
            print(f"catalog warm-up navigation: {exc}", flush=True)
        page.wait_for_timeout(8000)

        with page.expect_response(
            lambda response: (
                response.url.startswith(CATALOG_API)
                and "typeMainId=1" in response.url
                and "typeSubId=1" in response.url
                and response.status == 200
            ),
            timeout=60000,
        ) as catalog_info:
            page.goto(CATALOG_PAGE, timeout=90000, wait_until="domcontentloaded")

        catalog_body = catalog_info.value.text()
        catalog_payload = json.loads(catalog_body)
        _write_text(snapshot_dir / "catalog.json", catalog_body)
        operators = _catalog_items(catalog_payload)
        if args.limit > 0:
            operators = operators[: args.limit]

        print(f"operator catalog: {len(operators)} entries", flush=True)

        for position, operator in enumerate(operators, start=1):
            item_id = str(operator["itemId"])
            name = str(operator.get("name") or f"operator_{item_id}")
            stem = f"{item_id}_{_safe_name(name)}"
            detail_url = (
                "https://wiki.skland.com/endfield/detail"
                f"?mainTypeId=1&subTypeId=1&gameEntryId={item_id}&header=0"
            )
            current_responses.clear()
            print(f"[{position:02d}/{len(operators):02d}] {name} ({item_id})", flush=True)

            try:
                with page.expect_response(
                    lambda response, expected=item_id: (
                        response.url.startswith(DETAIL_API)
                        and f"id={expected}" in response.url
                        and response.status == 200
                    ),
                    timeout=60000,
                ) as detail_info:
                    page.goto(detail_url, timeout=90000, wait_until="domcontentloaded")

                detail_body = detail_info.value.text()
                detail_payload = json.loads(detail_body)
                page.wait_for_timeout(max(args.settle_ms, 0))

                detail_path = details_dir / f"{stem}.json"
                _write_text(detail_path, detail_body)

                rendered_text = page.locator("body").inner_text(timeout=30000)
                rendered_path = rendered_dir / f"{stem}.txt"
                _write_text(rendered_path, rendered_text)

                related_paths: list[str] = []
                related_index = 0
                for response in current_responses:
                    response_url = response.url
                    response_body = _response_body(response)
                    if response_body is None:
                        continue
                    path = urlsplit(response_url).path
                    if path in _GLOBAL_FILES:
                        filename = _GLOBAL_FILES[path]
                        if filename not in global_saved:
                            _write_text(snapshot_dir / filename, response_body)
                            global_saved.add(filename)
                        continue
                    if path == "/web/v1/wiki/item/list":
                        related_index += 1
                        suffix = "" if related_index == 1 else f"_{related_index}"
                        related_path = related_dir / f"{stem}{suffix}.json"
                        _write_text(related_path, response_body)
                        related_paths.append(related_path.relative_to(snapshot_dir).as_posix())

                item = detail_payload.get("data", {}).get("item", {})
                document = item.get("document") or {}
                entry = {
                    "position": position,
                    "item_id": item_id,
                    "name": name,
                    "lang": item.get("lang"),
                    "published_at_ts": item.get("publishedAtTs"),
                    "last_audit_passed_at": item.get("lastAuditPassedAt"),
                    "tag_ids": item.get("tagIds") or [],
                    "associate": (item.get("brief") or {}).get("associate"),
                    "detail_url": detail_url,
                    "detail_file": detail_path.relative_to(snapshot_dir).as_posix(),
                    "rendered_text_file": rendered_path.relative_to(snapshot_dir).as_posix(),
                    "related_item_files": related_paths,
                    "detail_bytes": len(detail_body.encode("utf-8")),
                    "rendered_text_chars": len(rendered_text),
                    "document_count": len(document.get("documentMap") or {}),
                    "chapter_group_count": len(document.get("chapterGroup") or {}),
                    "widget_count": len(document.get("widgetCommonMap") or {}),
                }
                index.append(entry)
                print(
                    f"  saved {entry['detail_bytes']} bytes, "
                    f"{entry['rendered_text_chars']} text chars, "
                    f"{len(related_paths)} related payload(s)",
                    flush=True,
                )
            except Exception as exc:
                failure = {
                    "position": position,
                    "item_id": item_id,
                    "name": name,
                    "detail_url": detail_url,
                    "error": str(exc),
                }
                failures.append(failure)
                print(f"  ERROR: {exc}", flush=True)

        browser.close()

    manifest = {
        "source": CATALOG_PAGE,
        "captured_at": stamp,
        "operator_count": len(operators),
        "success_count": len(index),
        "failure_count": len(failures),
        "total_detail_bytes": sum(item["detail_bytes"] for item in index),
        "total_rendered_text_chars": sum(item["rendered_text_chars"] for item in index),
        "global_files": sorted(global_saved),
        "operators": index,
        "failures": failures,
    }
    _write_text(
        snapshot_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
    _write_text(
        out_root / "latest.json",
        json.dumps(
            {
                "snapshot": stamp,
                "manifest": f"{stamp}/manifest.json",
                "success_count": len(index),
                "failure_count": len(failures),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    print(f"snapshot: {snapshot_dir}", flush=True)
    print(f"success: {len(index)}/{len(operators)}, failures: {len(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
