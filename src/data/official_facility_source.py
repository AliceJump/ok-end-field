"""Nonblocking optional facility queries, isolated from live navigation state."""

import threading
import time
from concurrent.futures import Future
from contextlib import suppress
from types import SimpleNamespace

from src.data import map_mark_query, user_map_mark_store
from src.data.official_map_client import OfficialMapClient

OFFICIAL_MARKS_CACHE_TTL = 30.0


class OfficialFacilitySource:
    """Run at most one HTTP job; publish only the caller's current snapshot."""

    def __init__(self, *, client_factory=OfficialMapClient, clock=time.monotonic):
        self._factory, self._clock = client_factory, clock
        self._client = self._client_content = None
        self._context = self._pending = None
        self._points = {}
        self._next_at = 0.0
        self._generation = 0
        self.last_error = ""
        self.error_sequence = 0

    def invalidate(self):
        """Discard results without waiting for, or multiplying, an active job."""
        self._generation += 1
        self._context = None
        self._points = {}
        self._next_at = 0.0
        self.last_error = ""

    def poll(self, map_id, content, *, account_id=""):
        """Return ready points or an immediate empty fallback; never wait on HTTP."""
        context = (str(map_id), str(content), str(account_id))
        if context != self._context:
            self.invalidate()
            self._context = context
        now = self._clock()
        if self._pending is not None:
            key, generation, future = self._pending
            if not future.done():
                return {}
            self._pending = None
            if key == context and generation == self._generation:
                self._points, self.last_error, completed_at = future.result()
                self._next_at = completed_at + OFFICIAL_MARKS_CACHE_TTL
                if self.last_error:
                    self.error_sequence += 1
        if not content:
            return {}
        if now >= self._next_at:
            future = Future()
            self._pending = (context, self._generation, future)
            threading.Thread(target=self._run, args=(future, context), name="OfficialFacilities", daemon=True).start()
            return {}
        return self._points

    def _run(self, future, context):
        """Complete a detached result; never mutate the caller's cache or logs."""
        try:
            result = self._fetch(context)
        except Exception as exc:
            # API exceptions can embed response credentials; retain only a type.
            result = ({}, type(exc).__name__)
        future.set_result((*result, self._clock()))

    def _fetch(self, context):
        """Authenticate, query and persist using immutable account/map inputs."""
        map_id, content, account_id = context
        if self._client is None or content != self._client_content:
            self._client = self._factory(SimpleNamespace(config={"content": content}))
            self._client_content = content
        client = self._client
        if not client.prepare():
            return {}, client.last_error
        account = client._map_ws_account
        response = client._map_api_get("/web/v1/game/endfield/map/mark/list", {"mapId": map_id, **account})
        if not isinstance(response, dict) or response.get("code") not in (None, 0):
            return {}, "invalid_facility_response"
        points = map_mark_query.extract_mark_points(response, map_id=map_id)
        # A diagnostic snapshot cannot invalidate otherwise usable points.
        with suppress(OSError):
            user_map_mark_store.persist_user_marks(
                response,
                map_id=map_id,
                account_id=account_id,
                map_user_id=str(getattr(client, "_map_ws_user_id", "") or ""),
                role_id=str(account.get("roleId") or ""),
                server_id=str(account.get("serverId") or ""),
            )
        return points, ""
