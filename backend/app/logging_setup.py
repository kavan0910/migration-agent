"""Structured logging and request-id middleware.

Logging policy: never log request bodies, Authorization headers, API keys, source content, or file names from
archives. Log request id, method, path (no query string), status, duration, user id.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
             "logger": record.name, "msg": record.getMessage(), "request_id": request_id_var.get(),
             "run_id": run_id_var.get()}
        for k in ("method", "path", "status", "duration_ms", "user_id", "event"):
            if hasattr(record, k):
                d[k] = getattr(record, k)
        if record.exc_info:
            d["exc"] = record.exc_info[0].__name__ if record.exc_info[0] else ""  # type only, no message/trace content
        return json.dumps(d, default=str)


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_ma_backend", False):
            root.removeHandler(h)
    h = logging.StreamHandler()
    h._ma_backend = True  # type: ignore[attr-defined]
    h.setFormatter(JsonFormatter() if as_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(h)
    root.setLevel(level.upper())


class RequestIdMiddleware:
    """Pure ASGI middleware (does not buffer streaming responses such as SSE)."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self.log = logging.getLogger("app.access")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = None
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                cand = v.decode("latin-1")[:64]
                if cand.replace("-", "").replace("_", "").isalnum():
                    rid = cand
        rid = rid or uuid.uuid4().hex
        tok = request_id_var.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        t0 = time.perf_counter()
        status = {"v": 500}

        async def _send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["v"] = msg["status"]
                msg.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            self.log.info("request", extra={"method": scope["method"], "path": scope["path"], "status": status["v"],
                                            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                                            "user_id": scope.get("state", {}).get("user_id", "-")})
            request_id_var.reset(tok)


class BodyLimitMiddleware:
    """Rejects oversized request bodies early (Content-Length) and while streaming (chunked bodies)."""

    def __init__(self, app: ASGIApp, default_limit: int, upload_limit: int):
        self.app, self.default_limit, self.upload_limit = app, default_limit, upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = self.upload_limit if path.endswith(("/source", "/user-docs")) else self.default_limit
        cl = dict(scope.get("headers", [])).get(b"content-length")
        too_big = False
        try:
            too_big = cl is not None and int(cl) > limit
        except ValueError:
            too_big = True
        if too_big:
            return await self._reject(send)
        seen = {"n": 0}

        async def _receive() -> Message:
            msg = await receive()
            if msg["type"] == "http.request":
                seen["n"] += len(msg.get("body", b""))
                if seen["n"] > limit:
                    from .errors import ApiError
                    raise ApiError(413, "payload_too_large", "request body too large")
            return msg

        await self.app(scope, _receive, send)

    async def _reject(self, send: Send) -> None:
        body = b'{"error":{"code":"payload_too_large","message":"request body too large"}}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
