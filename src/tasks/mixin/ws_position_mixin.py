# -*- coding: utf-8 -*-
import asyncio
import json
import queue
import threading
from typing import Any

import websockets


class WsPositionMixin:
    """提供本地 WS 位置消息接收能力（服务端模式）。"""

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

    def _is_ws_position_server_enabled(self) -> bool:
        thread = self._ws_server_thread
        return bool(self._ws_enabled and thread and thread.is_alive())

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
                log_info(f"[WS] 客户端已连接")

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
                        log_info(f"[WS] 收到位置: mapId={map_id} pos=({px:.1f},{py:.1f},{pz:.1f})")
                except Exception as e:
                    if callable(log_error):
                        log_error(f"[WS] 处理消息异常: {e}")
                    continue
        except Exception as e:
            if callable(log_error):
                log_error(f"[WS handler] 异常: {e}")
        finally:
            if callable(log_info):
                log_info(f"[WS] 客户端已断开")

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
                    log_info(f"[WS] 服务器已关闭")

        self._ws_server_thread = threading.Thread(target=_runner, name="WsPositionServer", daemon=True)
        self._ws_server_thread.start()
        self._ws_enabled = True

    def _recv_ws_position_payload(self, timeout: float = 0.5):
        try:
            return self._ws_payload_queue.get(timeout=timeout)
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
            return self._ws_last_position_payload

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
