# -*- coding: utf-8 -*-
import argparse
import getpass
import hashlib
import hmac
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, parse, request


API_HOST = "https://zonai.skland.com"
HG_GRANT_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
HG_APP_CODE = "4ca99fa6b56cc2ba"
ORIGIN = "https://game.skland.com"
REFERER = "https://game.skland.com/map/endfield"


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req_headers = {
        "User-Agent": "Mozilla/5.0 ok-ef map dump script",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "Referer": REFERER,
    }
    if headers:
        req_headers.update(headers)
    req = request.Request(url, data=body, headers=req_headers, method="POST")
    return open_json(req)


def open_json(req: request.Request):
    try:
        with request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="ignore")[:1000]
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body_text}") from e
    if not text:
        return None
    return json.loads(text)


def exchange_content(content: str) -> dict[str, Any]:
    hg_token = parse.unquote(content.strip().strip('"'))
    grant_resp = post_json(HG_GRANT_URL, {
        "token": hg_token,
        "appCode": HG_APP_CODE,
        "type": 0,
    })
    if not isinstance(grant_resp, dict) or grant_resp.get("status") != 0:
        raise RuntimeError(f"HG grant failed: {grant_resp}")

    oauth_code = ((grant_resp.get("data") or {}).get("code") or "").strip()
    if not oauth_code:
        raise RuntimeError("HG grant did not return oauth code")

    cred_resp = post_json(
        f"{API_HOST}/web/v1/user/auth/generate_cred_by_code",
        {"kind": 1, "code": oauth_code},
    )
    if not isinstance(cred_resp, dict) or cred_resp.get("code") != 0:
        raise RuntimeError(f"generate_cred_by_code failed: {cred_resp}")

    data = cred_resp.get("data") or {}
    cred = str(data.get("cred") or "").strip()
    sign_token = str(data.get("token") or "").strip()
    if not cred or not sign_token:
        raise RuntimeError("generate_cred_by_code response missing cred/token")

    return {
        "cred": cred,
        "sign_token": sign_token,
        "user_id": str(data.get("userId") or "").strip(),
        "sign_time": {
            "clientTime": str(int(time.time())),
            "serverTime": str(cred_resp.get("timestamp") or int(time.time())),
        },
    }


class SignedClient:
    def __init__(self, auth: dict[str, Any]):
        self.cred = str(auth.get("cred") or "")
        self.sign_token = str(auth.get("sign_token") or "")
        self.sign_time = auth.get("sign_time") if isinstance(auth.get("sign_time"), dict) else {}
        self.device_id = str(auth.get("d_id") or "")

    def adjusted_timestamp(self) -> str:
        now = int(time.time())
        try:
            client_time = int(self.sign_time.get("clientTime") or 0)
            server_time = int(self.sign_time.get("serverTime") or 0)
        except Exception:
            client_time = 0
            server_time = 0
        if client_time and server_time:
            return str(server_time + (now - client_time))
        return str(now)

    def sign_headers(self, url: str, method: str = "GET", body: str = "") -> dict[str, str]:
        headers = {
            "platform": "3",
            "vName": "1.0.0",
            "timestamp": self.adjusted_timestamp(),
            "dId": self.device_id,
        }
        parsed = parse.urlsplit(url)
        payload = parsed.path
        payload += parsed.query if method.upper() == "GET" else body
        payload += headers["timestamp"]
        payload += json.dumps({
            "platform": headers["platform"],
            "timestamp": headers["timestamp"],
            "dId": headers["dId"],
            "vName": headers["vName"],
        }, separators=(",", ":"), ensure_ascii=False)
        digest = hmac.new(self.sign_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["sign"] = hashlib.md5(digest.encode("utf-8")).hexdigest()
        return headers

    def get(self, path: str, params: dict[str, Any] | None = None):
        query = f"?{parse.urlencode(params)}" if params else ""
        url = f"{API_HOST}{path}{query}"
        headers = {
            "User-Agent": "Mozilla/5.0 ok-ef map dump script",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "Referer": REFERER,
            "cred": self.cred,
        }
        headers.update(self.sign_headers(url, "GET"))
        req = request.Request(url, headers=headers, method="GET")
        return open_json(req)


def walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item)
    else:
        yield value


def collect_mark_queries(tree_resp: Any) -> list[dict[str, str]]:
    data = tree_resp.get("data") if isinstance(tree_resp, dict) else {}
    maps = data.get("maps") if isinstance(data, dict) else []
    queries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for game_map in maps or []:
        if not isinstance(game_map, dict):
            continue
        map_id = str(game_map.get("id") or "").strip()
        if not map_id:
            continue

        key = (map_id, "")
        if key not in seen:
            seen.add(key)
            queries.append({"mapId": map_id})

        for level in game_map.get("levels") or []:
            if not isinstance(level, dict) or level.get("type") != 1:
                continue
            level_id = str(level.get("id") or "").strip()
            if not level_id:
                continue
            key = (map_id, level_id)
            if key not in seen:
                seen.add(key)
                queries.append({"mapId": map_id, "levelId": level_id})

    if queries:
        return queries

    # Fallback for schema changes: keep only top-level map IDs, never use level IDs as mapId.
    pattern = re.compile(r"^(base\d+|map\d+)$", re.I)
    for value in walk_values(tree_resp):
        if isinstance(value, str):
            map_id = value.strip()
            key = (map_id, "")
            if pattern.match(map_id) and key not in seen:
                seen.add(key)
                queries.append({"mapId": map_id})
    return queries


def collect_roles(binding_resp: Any) -> list[dict[str, str]]:
    data = binding_resp.get("data") if isinstance(binding_resp, dict) else {}
    entries = []
    game_map = data.get("gameMap") if isinstance(data, dict) else {}
    endfield = game_map.get("endfield") if isinstance(game_map, dict) else None
    if not isinstance(endfield, dict):
        for entry in data.get("list") or []:
            if isinstance(entry, dict) and entry.get("appCode") == "endfield":
                endfield = entry
                break
    if not isinstance(endfield, dict):
        return []

    for binding in endfield.get("bindingList") or []:
        if not isinstance(binding, dict):
            continue
        roles = []
        default_role = binding.get("defaultRole")
        if isinstance(default_role, dict):
            roles.append(default_role)
        roles.extend(role for role in binding.get("roles") or [] if isinstance(role, dict))
        for role in roles:
            role_id = role.get("roleId")
            server_id = role.get("serverId")
            if role_id is None or server_id is None:
                continue
            item = {"roleId": str(role_id), "serverId": str(server_id)}
            if item not in entries:
                entries.append(item)
    return entries


def write_json(path: Path, data: Any):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def main():
    parser = argparse.ArgumentParser(description="Dump Endfield map mark/list JSON by hg/check data.content")
    parser.add_argument("content", nargs="?", help="hg/check response data.content")
    parser.add_argument("--out", default="endfield_map_marks", help="output parent directory")
    args = parser.parse_args()

    content = args.content or getpass.getpass("hg/check data.content: ")
    if not content.strip():
        raise SystemExit("content is empty")

    out_dir = Path(args.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    auth = exchange_content(content)
    client = SignedClient(auth)

    user_resp = client.get("/web/v1/user")
    binding_resp = client.get("/api/v1/game/player/binding")
    tree_resp = client.get("/web/v1/game/endfield/map/tree")
    catalog_resp = client.get("/web/v1/game/endfield/map/catalog")

    write_json(out_dir / "user.json", user_resp)
    write_json(out_dir / "binding.json", binding_resp)
    write_json(out_dir / "map_tree.json", tree_resp)
    write_json(out_dir / "map_catalog.json", catalog_resp)

    roles = collect_roles(binding_resp)
    mark_queries = collect_mark_queries(tree_resp)
    if not roles:
        raise SystemExit("no endfield roleId/serverId found in binding response")
    if not mark_queries:
        raise SystemExit("no mark query found in map/tree response")

    index = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "markQueries": mark_queries,
        "roles": roles,
        "files": [],
    }

    for role in roles:
        for query in mark_queries:
            params = dict(query)
            params.update({
                "roleId": role["roleId"],
                "serverId": role["serverId"],
            })
            resp = client.get("/web/v1/game/endfield/map/mark/list", params)
            level_suffix = f"__level_{safe_name(params['levelId'])}" if params.get("levelId") else ""
            filename = (
                f"mark_list__server_{safe_name(role['serverId'])}"
                f"__role_{safe_name(role['roleId'])}"
                f"__map_{safe_name(params['mapId'])}{level_suffix}.json"
            )
            write_json(out_dir / filename, resp)
            index["files"].append({"params": params, "file": filename})
            print(f"saved {filename}")

    write_json(out_dir / "index.json", index)
    print(f"done: {out_dir}")


if __name__ == "__main__":
    main()
