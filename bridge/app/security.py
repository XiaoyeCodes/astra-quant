"""Request guard for the local cockpit (CSRF and DNS rebinding).

The bridge has no login: it relies on listening on 127.0.0.1 only. That alone does not
stop a web page in the user's browser from talking to it, so every request must:

* use an allowed Host header (blocks DNS rebinding, where evil.com resolves to 127.0.0.1);
* for anything that changes state (POST, PUT, PATCH, DELETE) and for WebSockets, come
  from the cockpit's own origin when the browser sends an Origin header;
* send state changes as application/json, which a cross-site page cannot do without a
  CORS preflight that this server never approves.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _host_name(value: str) -> str:
    """'localhost:8080' -> 'localhost', '[::1]:8080' -> '::1'."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


class LocalOnlyGuard:
    def __init__(self, app, allowed_hosts: set[str]) -> None:
        self.app = app
        self.allowed = {h.strip().lower().strip("[]") for h in allowed_hosts if h.strip()}

    def _origin_ok(self, origin: str | None) -> bool:
        if origin is None:
            return True  # not a browser cross-site request (curl, health checks, tests)
        if origin == "null":
            return False  # sandboxed iframes, file:// pages
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and (parts.hostname or "") in self.allowed

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = None
        if _host_name(headers.get("host", "")) not in self.allowed:
            reason = "host not allowed"
        elif scope["type"] == "websocket" or scope.get("method") in UNSAFE_METHODS:
            if not self._origin_ok(headers.get("origin")):
                reason = "cross-origin request refused"
            elif scope["type"] == "http":
                ctype = headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    reason = "state changes must be sent as application/json"
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # websocket.connect; refusing before accept answers HTTP 403
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"ok": False, "error": f"请求被拒绝：{reason}"}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
