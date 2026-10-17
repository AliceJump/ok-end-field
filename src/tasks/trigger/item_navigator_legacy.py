"""Legacy position source kept for existing item-navigation configurations."""

from __future__ import annotations

from src.localization.ws_position_mixin import WsPositionMixin


class ItemNavigatorLegacyMixin(WsPositionMixin):
    """Use the pre-runtime-state WS path without coupling it to the new task flow."""

    def _init_item_navigator_legacy(self) -> None:
        self._init_ws_position_mixin()

    def _stop_item_navigator_legacy_sources(self) -> None:
        self._stop_position_sources()

    def _read_item_navigator_legacy_position(self):
        """Return ``(map_id, x, y, z)`` and a status message when unavailable."""

        map_cred = self._get_account_map_content()
        self._ensure_ws_position_source(map_cred)

        try:
            payload = self._recv_ws_position_payload_or_cached(timeout=0.1)
        except Exception:
            return None, "无法读取WS位置"

        position, map_id, x, y, z = self._extract_position_payload(payload)
        if not position or not map_id:
            if map_cred:
                return None, "地图WS位置数据待接收..."
            return None, "WS位置数据待接收... (需要客户端连接到 ws://127.0.0.1:3001)"
        return (map_id, x, y, z), ""
