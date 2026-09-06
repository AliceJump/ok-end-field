# -*- coding: utf-8 -*-
# ruff: noqa: UP009
import asyncio
import hashlib
import hmac
import json
import queue
import random
import threading
import time
from typing import Any
from urllib import error, parse, request

import websockets

ENDFIELD_MAP_WS_URL = "wss://ws.skland.com/ws/v1/game/endfield/map"
ENDFIELD_MAP_API_HOST = "https://zonai.skland.com"
ENDFIELD_MAP_HG_GRANT_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
ENDFIELD_MAP_HG_APP_CODE = "4ca99fa6b56cc2ba"
ENDFIELD_MAP_ORIGIN = "https://game.skland.com"
ENDFIELD_MAP_REFERER = "https://game.skland.com/map/endfield"

# zonai 接口要求 dId 为数美(SMSdk)真实签发的设备指纹：
# 纯随机串会返回 10001 设备信息无效（已在真实浏览器内用原生 fetch 复现验证，
# 可排除 TLS 指纹因素），因此不能伪造。
# 但官方 SDK 注册流程自铸的全新设备ID可以从 Python 直接使用、与账号无关且可长期复用；
# 自铸流程见 src/core/map_device_id.py：临时浏览器配置匿名访问官方地图页（无需登录）
# 完成注册，并持久化到 configs/map_device_id.json。
from src.core.map_device_id import clear_stored_device_id, ensure_map_device_id

_shumei_did_failed_at = 0.0
_SHUMEI_DID_RETRY_INTERVAL = 30.0


def _get_shumei_device_id() -> str:
    """获取可用的 dId：优先使用已自铸并持久化的设备ID，缺失时现场铸造。"""
    global _shumei_did_failed_at
    if time.time() - _shumei_did_failed_at < _SHUMEI_DID_RETRY_INTERVAL:
        return ""
    try:
        did = ensure_map_device_id()
    except Exception as exc:
        _shumei_did_failed_at = time.time()
        del exc
        return ""
    return did


def _make_msg_id() -> str:
    """生成协议消息 ID，仅用于请求-响应匹配，非安全用途。"""  # NOSONAR (python:S2245)
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(random.choice(alphabet) for _ in range(8))  # NOSONAR


class MapAuthError(RuntimeError):
    """地图认证链路失败（HG 授权 / cred 换取 / dId 铸造 / 认证请求网络异常）。"""


class WsPositionMixin:
    """提供位置消息接收能力：本地 WS 服务端模式 + 终末地地图 WS 客户端模式。"""

    def _init_ws_position_mixin(self):
        ws_host = getattr(self, "_ws_host", None)
        ws_port = getattr(self, "_ws_port", None)
        self._ws_host = ws_host if ws_host is not None else "127.0.0.1"
        if ws_port is None:
            self._ws_port = 3001
        else:
            try:
                self._ws_port = int(ws_port)
            except (TypeError, ValueError):
                self._ws_port = 3001
        self._ws_payload_queue = queue.Queue(maxsize=1)
        self._ws_server_thread = None
        self._ws_loop = None
        self._ws_stop_event = None
        self._ws_enabled = False
        # 缓存最后接收的位置数据，用于在没有新数据时返回旧值
        self._ws_last_position_payload = None
        self._ws_position_lock = threading.Lock()
        self._map_ws_thread = None
        self._map_ws_loop = None
        self._map_ws_stop_event = None
        self._map_ws_enabled = False
        self._map_ws_cred = ""
        self._map_ws_sign_token = ""
        self._map_ws_sign_time = {}
        self._map_ws_device_id = ""
        self._map_ws_user_id = ""
        self._map_ws_auth_source = ""
        self._map_ws_account = None
        self._map_ws_last_error_at = 0.0
        self._map_ws_last_consume_at = 0.0
        self._map_ws_consumer_idle_timeout = 10.0

    def _is_ws_position_server_enabled(self) -> bool:
        thread = self._ws_server_thread
        return bool(self._ws_enabled and thread and thread.is_alive())

    def _is_map_ws_client_enabled(self) -> bool:
        thread = self._map_ws_thread
        return bool(self._map_ws_enabled and thread and thread.is_alive())

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
                self._stop_map_ws_client()
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
            # NOSONAR (python:S4790)
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

        data = binding_resp.get("data") or {}
        game_map = data.get("gameMap") or {}
        endfield = game_map.get("endfield")
        if endfield is None:
            for entry in data.get("list") or []:
                if isinstance(entry, dict) and entry.get("appCode") == "endfield":
                    endfield = entry
                    break

        if not isinstance(endfield, dict):
            raise RuntimeError("当前账号没有终末地绑定角色")

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

        role_id = role.get("roleId")
        server_id = role.get("serverId")
        if role_id is None or server_id is None:
            raise RuntimeError("终末地绑定数据缺少 roleId/serverId")

        return {"roleId": str(role_id), "serverId": str(server_id)}

    async def _map_ws_send(self, ws, msg_type: int, data: dict[str, Any] | None = None, msg_id: str | None = None):
        await ws.send(json.dumps({"type": msg_type, "data": data or {}, "msgId": msg_id or _make_msg_id()}))

    def _map_ws_should_stop_for_game_window(self) -> bool:
        is_alive = getattr(self, "_is_game_window_alive", None)
        if not callable(is_alive):
            return False

        try:
            if is_alive():
                return False
        except Exception:
            return False

        clear_arrows = getattr(self, "clear_window_arrows", None)
        if callable(clear_arrows):
            clear_arrows()

        if not getattr(self, "_navigator_window_missing_logged", False):
            log_info = getattr(self, "log_info", None)
            if callable(log_info):
                log_info("物品导航：游戏窗口不存在或不可见，地图WS客户端停止")
            self._navigator_window_missing_logged = True

        info_set = getattr(self, "info_set", None)
        if callable(info_set):
            info_set("导航", "游戏窗口不存在，地图WS已停止")
        return True

    def _map_ws_should_stop_for_idle_consumer(self) -> bool:
        last_consume_at = float(getattr(self, "_map_ws_last_consume_at", 0.0) or 0.0)
        timeout = float(getattr(self, "_map_ws_consumer_idle_timeout", 30.0) or 30.0)
        if last_consume_at <= 0 or time.time() - last_consume_at < timeout:
            return False

        log_info = getattr(self, "log_info", None)
        if callable(log_info):
            log_info(f"[地图WS] {timeout:.0f}秒内没有任务读取位置，客户端自动停止")

        info_set = getattr(self, "info_set", None)
        if callable(info_set):
            info_set("导航", "地图WS已停止：长时间无任务读取位置")
        return True

    async def _map_ws_client_main(self):
        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        while not self._map_ws_stop_event.is_set():
            try:
                if await asyncio.to_thread(self._map_ws_should_stop_for_game_window):
                    return

                account = await asyncio.to_thread(self._resolve_map_account_from_cred)
                self._map_ws_account = account
                if callable(log_info):
                    log_info(f"[地图WS] 已解析角色: serverId={account['serverId']} roleId={account['roleId']}")

                token_resp = await asyncio.to_thread(self._map_api_get, "/api/v1/websocket/token")
                if not isinstance(token_resp, dict) or token_resp.get("code") != 0:
                    raise RuntimeError(f"获取 websocket token 失败: {token_resp}")
                token = ((token_resp.get("data") or {}).get("token") or "").strip()
                if not token:
                    raise RuntimeError("websocket token 为空")

                headers = self._map_api_headers(self._map_ws_cred)
                async with websockets.connect(
                    ENDFIELD_MAP_WS_URL,
                    additional_headers=headers,
                    open_timeout=15,
                    ping_interval=None,
                ) as ws:
                    if callable(log_info):
                        log_info(f"[地图WS] 已连接: {ENDFIELD_MAP_WS_URL}")

                    await self._map_ws_send(ws, 1, {"token": token})
                    auth_ok = False
                    last_init_at = 0.0
                    last_heartbeat_at = 0.0

                    while not self._map_ws_stop_event.is_set():
                        if await asyncio.to_thread(self._map_ws_should_stop_for_game_window):
                            return
                        if await asyncio.to_thread(self._map_ws_should_stop_for_idle_consumer):
                            return

                        now = time.time()
                        if auth_ok and now - last_heartbeat_at >= 10.0:
                            await self._map_ws_send(ws, 3, {})
                            last_heartbeat_at = now
                        if auth_ok and now - last_init_at >= 5.0:
                            await self._map_ws_send(ws, 1011, account)
                            last_init_at = now

                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        if isinstance(msg, (bytes, bytearray)):
                            msg = msg.decode("utf-8", errors="ignore")
                        if not isinstance(msg, str) or not msg.strip().startswith("{"):
                            continue

                        payload = json.loads(msg)
                        msg_type = payload.get("type")
                        if msg_type == 2:
                            auth_ok = True
                            last_init_at = 0.0
                            continue
                        if msg_type == 6 and (payload.get("data") or {}).get("code") == 10002:
                            token_resp = await asyncio.to_thread(self._map_api_get, "/api/v1/websocket/token")
                            token = ((token_resp.get("data") or {}).get("token") or "").strip()
                            if token:
                                auth_ok = False
                                await self._map_ws_send(ws, 1, {"token": token})
                            continue
                        if msg_type == 1012:
                            self._push_ws_payload(payload)
                            pos, map_id, px, py, pz = self._extract_position_payload(payload)
                            if pos is not None and map_id is not None and callable(log_info):
                                log_info(f"[地图WS] 收到位置: mapId={map_id} pos=({px:.3f},{py:.3f},{pz:.3f})")
            except Exception as e:
                if self._map_ws_stop_event.is_set():
                    break
                self._map_ws_last_error_at = time.time()
                if callable(log_error):
                    log_error(f"[地图WS] 客户端异常，30秒后重试: {e}")
                try:
                    await asyncio.wait_for(self._map_ws_stop_event.wait(), timeout=30.0)
                except TimeoutError:
                    pass

    def _start_map_ws_client(self, raw_cred: str | None):
        try:
            auth_bundle = self._resolve_auth_bundle(raw_cred)
        except MapAuthError as e:
            # 仅捕获可预期的认证失败：不抛出阻断导航，记录一次后由后续
            # 触发周期重试（map_device_id 内部有 30 秒冷却，不会频繁铸造）
            if not getattr(self, "_map_ws_auth_failed_logged", False):
                self._map_ws_auth_failed_logged = True
                log_error = getattr(self, "log_error", None)
                if callable(log_error):
                    log_error(f"[地图WS] 认证失败，稍后将自动重试: {e}")
            return False
        self._map_ws_auth_failed_logged = False
        cred = str(auth_bundle.get("cred") or "")
        sign_token = str(auth_bundle.get("sign_token") or "")
        sign_time = auth_bundle.get("sign_time") if isinstance(auth_bundle.get("sign_time"), dict) else {}
        device_id = str(auth_bundle.get("d_id") or "")
        user_id = str(auth_bundle.get("user_id") or "")
        auth_source = str(raw_cred or "")
        if not cred:
            return False
        if (
            self._is_map_ws_client_enabled()
            and self._map_ws_cred == cred
            and self._map_ws_sign_token == sign_token
            and self._map_ws_user_id == user_id
            and self._map_ws_auth_source == auth_source
            and self._map_ws_device_id == device_id  # dId 变化（如10001后重新铸造）时不复用旧客户端
        ):
            return True

        self._stop_map_ws_client()
        self._map_ws_cred = cred
        self._map_ws_sign_token = sign_token
        self._map_ws_sign_time = sign_time
        self._map_ws_device_id = device_id
        self._map_ws_user_id = user_id
        self._map_ws_auth_source = auth_source
        self._map_ws_last_consume_at = time.time()

        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        def _runner():
            loop = asyncio.new_event_loop()
            self._map_ws_loop = loop
            self._map_ws_stop_event = asyncio.Event()
            asyncio.set_event_loop(loop)
            try:
                if callable(log_info):
                    log_info("[地图WS] 客户端启动")
                loop.run_until_complete(self._map_ws_client_main())
            except Exception as e:
                if callable(log_error):
                    log_error(f"[地图WS] 客户端启动异常: {e}")
            finally:
                try:
                    loop.stop()
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass
                if callable(log_info):
                    log_info("[地图WS] 客户端已关闭")
                self._map_ws_enabled = False

        self._map_ws_thread = threading.Thread(target=_runner, name="EndfieldMapWsClient", daemon=True)
        self._map_ws_thread.start()
        self._map_ws_enabled = True
        return True

    @staticmethod
    def _extract_position_payload(payload: dict[str, Any] | None):
        if not isinstance(payload, dict):
            return None, None, None, None, None

        data = payload.get("data")
        if isinstance(data, dict):
            pos = data.get("pos")
            if isinstance(pos, dict):
                x = pos.get("x")
                y = pos.get("y")
                z = pos.get("z")
                if x is not None and y is not None and z is not None:
                    map_id = data.get("mapId") or data.get("levelId") or payload.get("type")
                    if map_id is None:
                        return None, None, None, None, None
                    return pos, str(map_id), float(x), float(y), float(z)

            if all(k in data for k in ("x", "y", "z")):
                map_id = data.get("mapId") or data.get("levelId")
                if map_id is None:
                    return None, None, None, None, None
                return data, str(map_id), float(data["x"]), float(data["y"]), float(data["z"])

        if all(k in payload for k in ("x", "y", "z")):
            map_id = payload.get("mapId") or payload.get("levelId")
            if map_id is None:
                return None, None, None, None, None
            return payload, str(map_id), float(payload["x"]), float(payload["y"]), float(payload["z"])

        return None, None, None, None, None

    def _push_ws_payload(self, payload: dict[str, Any]):
        try:
            # 缓存有效的位置数据
            pos, map_id, px, py, pz = self._extract_position_payload(payload)
            if pos is not None and map_id is not None:
                with self._ws_position_lock:
                    self._ws_last_position_payload = payload
            # 放入队列
            self._ws_payload_queue.put_nowait(payload)
        except queue.Full:
            # 队列已满，弹出旧数据后重试
            try:
                self._ws_payload_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._ws_payload_queue.put_nowait(payload)
            except queue.Full:
                # 仍然满，放弃此消息
                pass

    async def _ws_handler(self, ws):
        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        try:
            if callable(log_info):
                log_info("[WS] 客户端已连接")

            async for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    msg = msg.decode("utf-8", errors="ignore")

                if not isinstance(msg, str) or not msg.strip().startswith("{"):
                    continue

                try:
                    payload = json.loads(msg)
                    self._push_ws_payload(payload)
                    # 仅在有效位置数据时记录（避免过多日志）
                    pos, map_id, px, py, pz = self._extract_position_payload(payload)
                    if pos is not None and map_id is not None and callable(log_info):
                        log_info(f"[WS] 收到位置: mapId={map_id} pos=({px:.3f},{py:.3f},{pz:.3f})")
                except Exception as e:
                    if callable(log_error):
                        log_error(f"[WS] 处理消息异常: {e}")
                    continue
        except Exception as e:
            if callable(log_error):
                log_error(f"[WS handler] 异常: {e}")
        finally:
            if callable(log_info):
                log_info("[WS] 客户端已断开")

    async def _ws_server_main(self):
        log_info = getattr(self, "log_info", None)
        if callable(log_info):
            log_info(f"[WS] 监听启动: ws://{self._ws_host}:{self._ws_port}")

        async with websockets.serve(self._ws_handler, self._ws_host, self._ws_port):
            await self._ws_stop_event.wait()

    def _start_ws_position_server(self, host: str | None = None, port: int | None = None):
        if host:
            self._ws_host = host
        if port:
            self._ws_port = int(port)

        if self._is_ws_position_server_enabled():
            return

        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        def _runner():
            loop = asyncio.new_event_loop()
            self._ws_loop = loop
            self._ws_stop_event = asyncio.Event()
            asyncio.set_event_loop(loop)
            try:
                if callable(log_info):
                    log_info(f"[WS] 服务器启动: ws://{self._ws_host}:{self._ws_port}")
                loop.run_until_complete(self._ws_server_main())
            except Exception as e:
                if callable(log_error):
                    log_error(f"[WS] 服务器异常: {e}")
            finally:
                try:
                    loop.stop()
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass
                if callable(log_info):
                    log_info("[WS] 服务器已关闭")

        self._ws_server_thread = threading.Thread(target=_runner, name="WsPositionServer", daemon=True)
        self._ws_server_thread.start()
        self._ws_enabled = True

    def _recv_ws_position_payload(self, timeout: float = 0.5):
        try:
            payload = self._ws_payload_queue.get(timeout=timeout)
            self._map_ws_last_consume_at = time.time()
            return payload
        except queue.Empty:
            return None

    def _recv_ws_position_payload_or_cached(self, timeout: float = 0.5):
        """获取最新的位置数据，如果没有新数据则返回缓存的上一次数据。

        返回：
            - 新的位置数据（从队列获取）
            - 或缓存的位置数据（如果队列为空）
            - 或 None（如果从未接收过数据）
        """
        payload = self._recv_ws_position_payload(timeout=timeout)
        if payload is not None:
            return payload
        # 队列为空，返回缓存的最后位置
        with self._ws_position_lock:
            payload = self._ws_last_position_payload
        if payload is not None:
            self._map_ws_last_consume_at = time.time()
        return payload

    def _stop_ws_position_server(self):
        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        try:
            if self._ws_loop and self._ws_stop_event:
                self._ws_loop.call_soon_threadsafe(self._ws_stop_event.set)

            if self._ws_server_thread and self._ws_server_thread.is_alive():
                self._ws_server_thread.join(timeout=2.0)

            if callable(log_info):
                log_info("[WS] 服务器已停止")
        except Exception as e:
            if callable(log_error):
                log_error(f"[WS] 停止服务异常: {e}")
        finally:
            self._ws_server_thread = None
            self._ws_loop = None
            self._ws_stop_event = None
            self._ws_enabled = False

    def _stop_map_ws_client(self):
        log_info = getattr(self, "log_info", None)
        log_error = getattr(self, "log_error", None)

        try:
            if self._map_ws_loop and not self._map_ws_loop.is_closed() and self._map_ws_stop_event:
                self._map_ws_loop.call_soon_threadsafe(self._map_ws_stop_event.set)

            if self._map_ws_thread and self._map_ws_thread.is_alive():
                self._map_ws_thread.join(timeout=2.0)

            if callable(log_info):
                log_info("[地图WS] 客户端已停止")
        except Exception as e:
            if callable(log_error):
                log_error(f"[地图WS] 停止客户端异常: {e}")
        finally:
            self._map_ws_thread = None
            self._map_ws_loop = None
            self._map_ws_stop_event = None
            self._map_ws_enabled = False
            self._map_ws_account = None
            self._map_ws_sign_token = ""
            self._map_ws_sign_time = {}
            self._map_ws_device_id = ""
            self._map_ws_user_id = ""
            self._map_ws_auth_source = ""
            self._map_ws_last_consume_at = 0.0
