"""Request middleware (workstream A): request IDs, one JSON log line per request.

Every HTTP request gets a request id (honors an inbound X-Request-ID), returned
three ways so any consumer can correlate: the X-Request-ID response header, a
`request_id` field injected into JSON response bodies, and exactly one
structured JSON log line emitted at request completion.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("mycelium.request")

_JSON_BODY = "application/json"

# Routes whose JSON response body carries request_id (feature 1's acceptance
# surface). Every route still gets the X-Request-ID header and the log line.
REQUEST_ID_BODY_PATHS = {"/ask"}


def request_log_record(
    request_id: str, method: str, path: str, status: int, duration_ms: float, ts: str
) -> dict:
    """Pure builder for the one-log-line-per-request payload."""
    return {
        "ts": ts,
        "level": "INFO",
        "request_id": request_id,
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 2),
    }


def with_request_id(body: bytes, request_id: str) -> bytes:
    """Inject `request_id` into a JSON object body; leave anything else untouched."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict) or "request_id" in payload:
        return body
    payload["request_id"] = request_id
    return json.dumps(payload).encode()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        # Register this request's task so shutdown can drain in-flight writes.
        pending = getattr(request.app.state, "pending", None)
        task = asyncio.current_task()
        if pending is not None and task is not None:
            pending.add(task)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._log(request, request_id, 500, (time.perf_counter() - start) * 1000)
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        if request.url.path in REQUEST_ID_BODY_PATHS and response.headers.get("content-type", "").startswith(_JSON_BODY):
            response = await _reinject_body(response, request_id)
        self._log(request, request_id, response.status_code, duration_ms)
        return response

    def _log(self, request: Request, request_id: str, status: int, duration_ms: float) -> None:
        record = request_log_record(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=duration_ms,
            ts=dt.datetime.now(dt.UTC).isoformat(),
        )
        log.info(json.dumps(record, separators=(",", ":")))


async def _reinject_body(response: Response, request_id: str) -> Response:
    """Rebuild a JSON response with request_id in its body, headers intact."""
    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = dict(response.headers)
    headers.pop("content-length", None)  # Response recomputes it
    return Response(
        content=with_request_id(body, request_id),
        status_code=response.status_code,
        headers=headers,
        media_type=_JSON_BODY,
    )
