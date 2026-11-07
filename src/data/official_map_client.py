"""Official map HTTP queries, separate from all positioning transports.

Authentication/signing is shared with the historical WS adapter. HTTP facilities
are optional navigation resources, never a player-position source.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib import error, parse, request

from src.core.map_device_id import clear_stored_device_id, ensure_map_device_id
from src.tasks.account.account_scope_store import get_account_map_content, resolve_account_id

ENDFIELD_MAP_API_HOST = "https://zonai.skland.com"
ENDFIELD_MAP_HG_GRANT_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
ENDFIELD_MAP_HG_APP_CODE = "4ca99fa6b56cc2ba"
ENDFIELD_MAP_ORIGIN = "https://game.skland.com"
ENDFIELD_MAP_REFERER = "https://game.skland.com/map/endfield"

_shumei_did_failed_at = 0.0
_SHUMEI_DID_RETRY_INTERVAL = 30.0


def _get_shumei_device_id() -> str:
    """获取可用的 dId：优先使用已自铸并持久化的设备ID，缺失时现场铸造。"""
    global _shumei_did_failed_at
    if time.time() - _shumei_did_failed_at < _SHUMEI_DID_RETRY_INTERVAL:
        return ""
    try:
        did = ensure_map_device_id()
    except Exception:
        _shumei_did_failed_at = time.time()
        return ""
    return did


class MapAuthError(RuntimeError):
    """地图认证链路失败（HG 授权 / cred 换取 / dId 铸造 / 认证请求网络异常）。"""


class OfficialMapApiMixin:
    def _invalidate_map_auth(self):
        # Stateless adapters have no cached credentials; caching clients override this hook.
        pass

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req_headers = {
            "User-Agent": "Mozilla/5.0 ok-ef map websocket client",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": ENDFIELD_MAP_ORIGIN,
            "Referer": ENDFIELD_MAP_REFERER,
        }
        if headers:
            req_headers.update(headers)
        req = request.Request(url, data=body, headers=req_headers, method="POST")
        try:
            with request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
        except error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="ignore")[:500]
            raise RuntimeError(f"HTTP {e.code} {e.reason}: {body_text}") from e
        if not text:
            return None
        return json.loads(text)

    def _request_hg_grant_code(self, hg_token: str) -> str:
        """调用 HG 授权接口换取 oauth code；异常时抛出 MapAuthError。"""
        try:
            grant_resp = self._post_json(
                ENDFIELD_MAP_HG_GRANT_URL,
                {
                    "token": hg_token,
                    "appCode": ENDFIELD_MAP_HG_APP_CODE,
                    "type": 0,
                },
            )
        except RuntimeError as e:
            raise MapAuthError(f"HG 授权接口请求失败: {e}") from e
        except (error.URLError, TimeoutError) as e:
            # DNS/连接/TLS 失败与超时同样属于可预期的认证失败
            raise MapAuthError(f"HG 授权接口网络异常: {e}") from e
        if not isinstance(grant_resp, dict) or grant_resp.get("status") != 0:
            raise MapAuthError(f"HG 授权接口返回异常: {grant_resp}")

        oauth_code = ((grant_resp.get("data") or {}).get("code") or "").strip()
        if not oauth_code:
            raise MapAuthError("HG 授权接口没有返回 oauth code")
        return oauth_code

    def _request_map_cred(self, oauth_code: str, device_id: str) -> dict[str, Any]:
        """用 oauth code 换取地图 cred；被风控拒绝(10001)时提示重新铸造 dId。

        返回完整 cred_resp（data 含 cred/token/userId，顶层含服务器 timestamp）。
        """
        try:
            cred_resp = self._post_json(
                f"{ENDFIELD_MAP_API_HOST}/web/v1/user/auth/generate_cred_by_code",
                {"kind": 1, "code": oauth_code},
                headers={
                    "platform": "3",
                    "vName": "1.0.0",
                    "timestamp": str(int(time.time())),
                    "dId": device_id,
                },
            )
        except RuntimeError as e:
            raise MapAuthError(f"地图 cred 换取接口请求失败: {e}") from e
        except (error.URLError, TimeoutError) as e:
            raise MapAuthError(f"地图 cred 换取接口网络异常: {e}") from e
        if not isinstance(cred_resp, dict) or cred_resp.get("code") != 0:
            hint = ""
            if isinstance(cred_resp, dict) and cred_resp.get("code") == 10001:
                # 已存 dId 被风控拒绝：清除进程缓存与持久化文件，并停掉仍在
                # 使用旧 dId 的地图WS客户端，下个触发周期自动重新铸造并重建
                clear_stored_device_id()
                self._invalidate_map_auth()
                hint = "（设备信息无效：已存 dId 可能被风控拒绝，已自动清除本地缓存，稍后将重新铸造重试）"
            raise MapAuthError(f"地图 cred 换取接口返回异常: {cred_resp}{hint}")

        data = cred_resp.get("data") or {}
        if not str(data.get("cred") or "").strip() or not str(data.get("token") or "").strip():
            raise MapAuthError("地图 cred 换取接口缺少 cred 或 token")
        return cred_resp

    def _exchange_hg_token_for_map_auth(self, hg_token: str) -> dict[str, Any]:
        oauth_code = self._request_hg_grant_code(hg_token)

        device_id = _get_shumei_device_id()
        if not device_id:
            raise MapAuthError(
                "地图设备ID(dId)暂不可用：自动铸造失败（需安装 Edge/Chrome 且可访问 "
                "fp-it.portal101.cn），30 秒后将自动重试"
            )

        data = self._request_map_cred(oauth_code, device_id) or {}
        cred_data = data.get("data") or {}
        return {
            "cred": str(cred_data.get("cred") or "").strip(),
            "sign_token": str(cred_data.get("token") or "").strip(),
            "user_id": str(cred_data.get("userId") or "").strip(),
            "d_id": device_id,
            "sign_time": {
                "clientTime": str(int(time.time())),
                "serverTime": str(data.get("timestamp") or int(time.time())),
            },
        }

    def _resolve_auth_bundle(self, raw_value: str | None) -> dict[str, Any]:
        hg_token = str(raw_value or "").strip().strip('"')
        if not hg_token:
            return {}
        return self._exchange_hg_token_for_map_auth(parse.unquote(hg_token))

    @staticmethod
    def _map_api_headers(cred: str) -> dict[str, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 ok-ef map websocket client",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": ENDFIELD_MAP_ORIGIN,
            "Referer": ENDFIELD_MAP_REFERER,
        }
        if cred:
            headers["cred"] = cred
        return headers

    def _get_adjusted_map_timestamp(self) -> str:
        now = int(time.time())
        sign_time = self._map_ws_sign_time if isinstance(self._map_ws_sign_time, dict) else {}
        try:
            client_time = int(sign_time.get("clientTime") or 0)
            server_time = int(sign_time.get("serverTime") or 0)
        except Exception:
            client_time = 0
            server_time = 0
        if client_time and server_time:
            return str(server_time + (now - client_time))
        return str(now)

    @staticmethod
    def _map_sign_payload(path: str, method: str, headers: dict[str, str], query: str, body: str) -> str:
        payload = path
        payload += query if method.upper() == "GET" else body
        timestamp = headers.get("timestamp")
        if timestamp:
            payload += str(timestamp)
        compact_headers = {
            "platform": headers.get("platform", ""),
            "timestamp": headers.get("timestamp", ""),
            "dId": headers.get("dId", ""),
            "vName": headers.get("vName", ""),
        }
        payload += json.dumps(compact_headers, separators=(",", ":"), ensure_ascii=False)
        return payload

    def _map_sign_headers(self, url: str, method: str = "GET", body: str = "") -> dict[str, str]:
        headers = {
            "platform": "3",
            "vName": "1.0.0",
            "timestamp": self._get_adjusted_map_timestamp(),
        }
        if self._map_ws_device_id:
            headers["dId"] = self._map_ws_device_id
        else:
            headers["dId"] = ""

        token = self._map_ws_sign_token
        if token:
            parsed = parse.urlsplit(url)
            query = parsed.query or ""
            sign_payload = self._map_sign_payload(parsed.path, method, headers, query, body)
            digest = hmac.new(token.encode("utf-8"), sign_payload.encode("utf-8"), hashlib.sha256).hexdigest()
            # MD5 仅用于协议输出格式兼容；实际安全校验由上一行的 HMAC-SHA256 保证。
            headers["sign"] = hashlib.md5(digest.encode("utf-8")).hexdigest()  # NOSONAR

        return headers

    def _map_api_get(self, path: str, params: dict[str, Any] | None = None):
        cred = self._map_ws_cred
        if not cred:
            return None

        query = ""
        if params:
            query = "?" + parse.urlencode(params)
        url = f"{ENDFIELD_MAP_API_HOST}{path}{query}"
        headers = self._map_api_headers(cred)
        headers.update(self._map_sign_headers(url, "GET"))
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
        except error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:500]
            raise RuntimeError(f"HTTP {e.code} {e.reason}: {body}") from e
        if not text:
            return None
        return json.loads(text)

    def _resolve_map_account_from_cred(self):
        user_resp = self._map_api_get("/web/v1/user")
        if not isinstance(user_resp, dict) or user_resp.get("code") != 0:
            raise RuntimeError(f"用户信息接口返回异常: {user_resp}")

        binding_resp = self._map_api_get("/api/v1/game/player/binding")
        if (not isinstance(binding_resp, dict) or binding_resp.get("code") != 0) and self._map_ws_user_id:
            binding_resp = self._map_api_get("/api/v1/game/player/binding", {"uid": self._map_ws_user_id})
        if not isinstance(binding_resp, dict) or binding_resp.get("code") != 0:
            raise RuntimeError(f"角色绑定接口返回异常: {binding_resp}")

        endfield = self._endfield_binding(binding_resp.get("data") or {})
        if not isinstance(endfield, dict):
            raise RuntimeError("当前账号没有终末地绑定角色")
        role = self._binding_role(endfield)
        role_id = role.get("roleId")
        server_id = role.get("serverId")
        if role_id is None or server_id is None:
            raise RuntimeError("终末地绑定数据缺少 roleId/serverId")
        return {"roleId": str(role_id), "serverId": str(server_id)}

    @staticmethod
    def _endfield_binding(data):
        game_map = data.get("gameMap") or {}
        endfield = game_map.get("endfield")
        if endfield is None:
            for entry in data.get("list") or []:
                if isinstance(entry, dict) and entry.get("appCode") == "endfield":
                    endfield = entry
                    break

        return endfield

    @staticmethod
    def _binding_role(endfield):
        binding_list = endfield.get("bindingList") or []
        selected = None
        for item in binding_list:
            if isinstance(item, dict) and item.get("isDefault"):
                selected = item
                break
        if selected is None and binding_list:
            selected = binding_list[0]
        if not isinstance(selected, dict):
            raise RuntimeError("终末地绑定数据为空")

        role = selected.get("defaultRole")
        roles = selected.get("roles") or []
        if not isinstance(role, dict) and roles:
            role = roles[0]
        if not isinstance(role, dict):
            raise RuntimeError("终末地绑定数据缺少 roleId/serverId")

        return role


def resolve_map_content(self) -> str:
    """按配置优先级解析地图同步 content，兼容旧任务配置与全局导航配置。"""

    direct_content = str(self.config.get("content") or "").strip()
    task_account = str(self.config.get("地图账号") or "").strip()

    if not direct_content:
        from src.core.global_config_store import NAV_CONFIG_NAME, get_global_config
        from src.core.NavConfig import NAV_CONTENT_KEY, NAV_WS_ACCOUNT_KEY

        nav_config = get_global_config(NAV_CONFIG_NAME)
        direct_content = str(nav_config.get(NAV_CONTENT_KEY) or "").strip()
        task_account = task_account or str(nav_config.get(NAV_WS_ACCOUNT_KEY) or "").strip()

    if direct_content:
        return direct_content

    selected_account = task_account
    if selected_account:
        account_id = resolve_account_id(selected_account, create_if_missing=False) or selected_account
        return get_account_map_content(account_id, account_name=selected_account)

    account_id, account_name = _current_map_account(self)
    return get_account_map_content(account_id or account_name, account_name=account_name)


def _current_map_account(self):
    account_id = str(getattr(self, "current_account_id", "") or "").strip()
    account_name = str(getattr(self, "current_user", "") or "").strip()

    executor = getattr(self, "executor", None)
    current_task = getattr(executor, "current_task", None) if executor is not None else None
    if current_task is not None and current_task is not self:
        account_id = account_id or str(getattr(current_task, "current_account_id", "") or "").strip()
        account_name = account_name or str(getattr(current_task, "current_user", "") or "").strip()

    return account_id, account_name


class OfficialMapClient(OfficialMapApiMixin):
    def __init__(self, consumer):
        self.consumer = consumer
        self._map_ws_cred = self._map_ws_sign_token = self._map_ws_device_id = self._map_ws_user_id = ""
        self._map_ws_sign_time = {}
        self._map_ws_account = None
        self._content = None
        self._retry_at = 0
        self.last_error = ""

    def _invalidate_map_auth(self):
        self._content = None
        self._map_ws_cred = ""
        self._map_ws_account = None

    def prepare(self):
        content = resolve_map_content(self.consumer)
        if not content:
            self._invalidate_map_auth()
            self.last_error = ""
            return False
        if content == self._content and self._map_ws_cred and self._map_ws_account:
            return True
        if time.monotonic() < self._retry_at:
            return False
        self._invalidate_map_auth()
        try:
            bundle = self._resolve_auth_bundle(content)
            self._map_ws_cred = bundle.get("cred", "")
            self._map_ws_sign_token = bundle.get("sign_token", "")
            self._map_ws_sign_time = bundle.get("sign_time", {})
            self._map_ws_device_id = bundle.get("d_id", "")
            self._map_ws_user_id = bundle.get("user_id", "")
            self._map_ws_account = self._resolve_map_account_from_cred()
        except (RuntimeError, OSError, ValueError) as exc:
            self._invalidate_map_auth()
            self._retry_at = time.monotonic() + 30
            # Authentication exceptions may embed response bodies or tokens.
            self.last_error = type(exc).__name__
            return False
        self._content = content
        self.last_error = ""
        return bool(self._map_ws_cred and self._map_ws_account)
