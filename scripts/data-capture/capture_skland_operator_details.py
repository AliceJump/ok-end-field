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
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from playwright.sync_api import Response

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
_REQUIRED_GLOBAL_FILES = frozenset({"catalog.json", *_GLOBAL_FILES.values()})


def _safe_name(name: str) -> str:
    """生成 Windows 可用且稳定的文件名片段。"""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return value or "unknown"


def _write_text(path: Path, text: str) -> None:
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
    with open(real, "w", encoding="utf-8") as f:
        f.write(text)


def _create_snapshot_dir(out_root: Path, stamp: str) -> Path:
    real = os.path.realpath(out_root)
    root_real = os.path.realpath(ROOT)
    if not real.startswith(root_real + os.sep):
        raise ValueError(f"拒绝写入仓库外路径：{out_root}")
    Path(real).mkdir(parents=True, exist_ok=True)
    snapshot_dir = Path(real) / stamp
    snap_real = os.path.realpath(snapshot_dir)
    if not snap_real.startswith(root_real + os.sep):
        raise ValueError(f"拒绝写入仓库外路径：{snapshot_dir}")
    try:
        Path(snap_real).mkdir()
    except FileExistsError as exc:
        raise FileExistsError(f"快照目录已存在，可能有同秒并发抓取：{snapshot_dir}") from exc
    return snapshot_dir


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


def _has_expected_item_id(response_url: str, expected_item_id: str) -> bool:
    """检查详情响应查询参数中的唯一 id 是否与预期干员一致。"""
    return parse_qs(urlsplit(response_url).query).get("id") == [expected_item_id]


def _response_body(response: Response) -> str | None:
    try:
        return response.text()
    except Exception as exc:
        print(f"  response read failed: {response.url}: {exc}", flush=True)
        return None


def _save_global_responses(snapshot_dir: Path, responses: list[Response], saved: set[str]) -> None:
    for response in responses:
        filename = _GLOBAL_FILES.get(urlsplit(response.url).path)
        if filename is None or filename in saved:
            continue
        body = _response_body(response)
        if body is None:
            continue
        _write_text(snapshot_dir / filename, body)
        saved.add(filename)


@contextmanager
def _record_capture_errors(errors: list[dict]):
    try:
        yield
    except Exception as exc:
        errors.append({"stage": "capture", "error": str(exc)})
        print(f"capture aborted: {exc}", flush=True)


def _snapshot_file_sets(snapshot_dir: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for key, directory, pattern in (
        ("details", "details", "*.json"),
        ("rendered_text", "rendered_text", "*.txt"),
        ("related_items", "related_items", "*.json"),
    ):
        result[key] = {
            path.relative_to(snapshot_dir).as_posix()
            for path in (snapshot_dir / directory).glob(pattern)
            if path.is_file()
        }
    return result


def _snapshot_incomplete_reasons(snapshot_dir: Path, manifest: dict) -> list[str]:
    reasons: list[str] = []
    operators = manifest.get("operators") or []
    failures = manifest.get("failures") or []
    capture_errors = manifest.get("capture_errors") or []
    catalog_count = int(manifest.get("catalog_operator_count") or 0)
    requested_count = int(manifest.get("operator_count") or 0)
    success_count = int(manifest.get("success_count") or 0)
    failure_count = int(manifest.get("failure_count") or 0)

    if catalog_count <= 0:
        reasons.append("empty_catalog")
    if requested_count != catalog_count:
        reasons.append(f"catalog_subset: requested {requested_count} of {catalog_count}")
    if failure_count or failures:
        reasons.append(f"operator_failures: {max(failure_count, len(failures))}")
    if capture_errors:
        reasons.append(f"capture_errors: {len(capture_errors)}")
    if success_count != len(operators):
        reasons.append(f"success_count_mismatch: manifest {success_count}, entries {len(operators)}")
    if failure_count != len(failures):
        reasons.append(f"failure_count_mismatch: manifest {failure_count}, entries {len(failures)}")
    if requested_count != success_count + failure_count:
        reasons.append(
            f"operator_count_mismatch: requested {requested_count}, success {success_count}, failure {failure_count}"
        )

    catalog_path = snapshot_dir / "catalog.json"
    if catalog_path.is_file():
        try:
            raw_catalog_count = len(_catalog_items(json.loads(catalog_path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            reasons.append(f"invalid_catalog: {exc}")
        else:
            if raw_catalog_count != catalog_count:
                reasons.append(f"catalog_count_mismatch: manifest {catalog_count}, file {raw_catalog_count}")

    missing_global_files = sorted(
        filename for filename in _REQUIRED_GLOBAL_FILES if not (snapshot_dir / filename).is_file()
    )
    if missing_global_files:
        reasons.append(f"missing_global_files: {', '.join(missing_global_files)}")

    actual_global_files = {filename for filename in _GLOBAL_FILES.values() if (snapshot_dir / filename).is_file()}
    manifest_global_files = {str(filename) for filename in manifest.get("global_files") or []}
    if manifest_global_files != actual_global_files:
        reasons.append(
            "global_file_manifest_mismatch: "
            f"manifest {sorted(manifest_global_files)}, files {sorted(actual_global_files)}"
        )

    file_sets = _snapshot_file_sets(snapshot_dir)
    actual_counts = {key: len(paths) for key, paths in file_sets.items()}
    if manifest.get("file_counts") != actual_counts:
        reasons.append(f"file_count_manifest_mismatch: manifest {manifest.get('file_counts')}, files {actual_counts}")

    detail_files = [str(entry.get("detail_file") or "") for entry in operators]
    rendered_files = [str(entry.get("rendered_text_file") or "") for entry in operators]
    related_files = [str(path) for entry in operators for path in entry.get("related_item_files") or []]
    for label, listed, expected_count in (
        ("details", detail_files, success_count),
        ("rendered_text", rendered_files, success_count),
        ("related_items", related_files, len(related_files)),
    ):
        listed_set = set(listed)
        if (
            "" in listed_set
            or len(listed) != len(listed_set)
            or len(listed) != expected_count
            or listed_set != file_sets[label]
        ):
            reasons.append(
                f"{label}_file_mismatch: manifest {len(listed)} entries, "
                f"{len(listed_set)} unique, files {len(file_sets[label])}"
            )
    return reasons


def _finalize_snapshot(snapshot_dir: Path, out_root: Path, manifest: dict) -> dict:
    manifest["required_global_files"] = sorted(_REQUIRED_GLOBAL_FILES)
    manifest["file_counts"] = {key: len(paths) for key, paths in _snapshot_file_sets(snapshot_dir).items()}
    reasons = _snapshot_incomplete_reasons(snapshot_dir, manifest)
    manifest["complete"] = not reasons
    manifest["incomplete_reasons"] = reasons
    _write_text(
        snapshot_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
    if manifest["complete"]:
        snapshot = snapshot_dir.name
        _write_text(
            out_root / "latest.json",
            json.dumps(
                {
                    "snapshot": snapshot,
                    "manifest": f"{snapshot}/manifest.json",
                    "catalog_operator_count": manifest["catalog_operator_count"],
                    "success_count": manifest["success_count"],
                    "failure_count": manifest["failure_count"],
                    "complete": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=r"tools/wiki_catalog/operator_details")
    parser.add_argument("--proxy", default=None, help="例如 http://127.0.0.1:10808")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--settle-ms", type=int, default=1500, help="详情响应后等待关联请求完成")
    parser.add_argument("--limit", type=int, default=0, help="仅抓前 N 名；0 表示全部")
    args = parser.parse_args()

    out_root = (ROOT / args.out).resolve()
    if not out_root.is_relative_to(ROOT):
        parser.error(f"--out 必须位于仓库内：{args.out}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        parser.error(f"无法导入 Playwright；请先运行 uv sync --locked --group dev：{exc}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        snapshot_dir = _create_snapshot_dir(out_root, stamp)
    except FileExistsError as exc:
        parser.error(str(exc))
    details_dir = snapshot_dir / "details"
    related_dir = snapshot_dir / "related_items"
    rendered_dir = snapshot_dir / "rendered_text"

    launch_kwargs: dict = {"headless": not args.headed}
    if args.proxy:
        launch_kwargs["proxy"] = {"server": args.proxy}

    index: list[dict] = []
    failures: list[dict] = []
    capture_errors: list[dict] = []
    global_saved: set[str] = set()
    global_responses: list[Response] = []
    operator_responses: list[Response] = []
    catalog_operators: list[dict] = []
    operators: list[dict] = []

    with _record_capture_errors(capture_errors), sync_playwright() as playwright:
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
            if path in _GLOBAL_FILES:
                global_responses.append(response)
            else:
                operator_responses.append(response)

        page.on("response", capture_response)

        # 首次打开用于初始化前端签名模块；随后再次导航并精确捕获 200 响应。
        try:
            page.goto(CATALOG_PAGE, timeout=90000, wait_until="domcontentloaded")
        except Exception as exc:
            print(f"catalog warm-up navigation: {exc}", flush=True)
        page.wait_for_timeout(8000)
        _save_global_responses(snapshot_dir, global_responses, global_saved)
        global_responses.clear()
        operator_responses.clear()

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
        catalog_operators = _catalog_items(catalog_payload)
        operators = catalog_operators
        if args.limit > 0:
            operators = operators[: args.limit]
        _save_global_responses(snapshot_dir, global_responses, global_saved)
        global_responses.clear()
        operator_responses.clear()

        print(f"operator catalog: {len(operators)}/{len(catalog_operators)} entries", flush=True)

        for position, operator in enumerate(operators, start=1):
            item_id = str(operator["itemId"])
            name = str(operator.get("name") or f"operator_{item_id}")
            stem = f"{item_id}_{_safe_name(name)}"
            detail_url = (
                f"https://wiki.skland.com/endfield/detail?mainTypeId=1&subTypeId=1&gameEntryId={item_id}&header=0"
            )
            operator_responses.clear()
            print(f"[{position:02d}/{len(operators):02d}] {name} ({item_id})", flush=True)

            try:
                with page.expect_response(
                    lambda response, expected=item_id: (
                        response.url.startswith(DETAIL_API)
                        and _has_expected_item_id(response.url, expected)
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
                for response in operator_responses:
                    response_url = response.url
                    response_body = _response_body(response)
                    if response_body is None:
                        continue
                    path = urlsplit(response_url).path
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
            finally:
                _save_global_responses(snapshot_dir, global_responses, global_saved)
                global_responses.clear()

        _save_global_responses(snapshot_dir, global_responses, global_saved)
        global_responses.clear()
        browser.close()

    manifest = {
        "source": CATALOG_PAGE,
        "captured_at": stamp,
        "catalog_operator_count": len(catalog_operators),
        "capture_limit": args.limit,
        "operator_count": len(operators),
        "success_count": len(index),
        "failure_count": len(failures),
        "total_detail_bytes": sum(item["detail_bytes"] for item in index),
        "total_rendered_text_chars": sum(item["rendered_text_chars"] for item in index),
        "global_files": sorted(global_saved),
        "operators": index,
        "failures": failures,
        "capture_errors": capture_errors,
    }
    manifest = _finalize_snapshot(snapshot_dir, out_root, manifest)

    print(f"snapshot: {snapshot_dir}", flush=True)
    print(f"success: {len(index)}/{len(operators)}, failures: {len(failures)}", flush=True)
    if not manifest["complete"]:
        print(f"incomplete snapshot: {'; '.join(manifest['incomplete_reasons'])}", flush=True)
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
