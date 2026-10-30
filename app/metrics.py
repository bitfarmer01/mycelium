"""Request metrics store (feature 64): p50/p95 latency and error rate.

An in-process, bounded sample store fed by MetricsMiddleware and consumed by
the /vitals endpoint and the /events WebSocket feed. Everything heavy is in
pure functions so the math is pinned by tests without touching the ASGI app.
"""
from __future__ import annotations

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Bounded sample window: enough for live percentiles on the demo timescale,
# small enough that latency never grows with traffic.
MAX_SAMPLES = 500

# A request counts as an error when the server failed (5xx). 4xx are client
# behavior, not system health.
_ERROR_MIN_STATUS = 500

_samples: deque[tuple[int, float]] = deque(maxlen=MAX_SAMPLES)  # (status, duration_ms)


def percentile(sorted_values: list[float], p: float) -> float | None:
    """Nearest-rank percentile on a pre-sorted list; None for an empty list."""
    if not sorted_values:
        return None
    rank = max(1, round(p * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def error_rate(statuses: list[int]) -> float | None:
    """Share of statuses >= 500; None when there are no samples."""
    if not statuses:
        return None
    return sum(1 for s in statuses if s >= _ERROR_MIN_STATUS) / len(statuses)


def snapshot() -> dict:
    """The /vitals `metrics` block: schema contract consumed by the console panel."""
    durations = sorted(d for _, d in _samples)
    statuses = [s for s, _ in _samples]
    return {
        "latency_ms_p50": percentile(durations, 0.5),
        "latency_ms_p95": percentile(durations, 0.95),
        "error_rate": error_rate(statuses),
        "sample_count": len(_samples),
    }


def record(status: int, duration_ms: float) -> None:
    _samples.append((status, duration_ms))


def reset() -> None:
    """Test isolation only: the store is process-global by design."""
    _samples.clear()


def vitals_feed_msg() -> dict:
    """The /events vitals message type (spec contract: A publishes, G subscribes)."""
    return {"table": "vitals", "action": "METRICS", "row": {"metrics": snapshot()}}


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records one latency/error sample per HTTP request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        try:
            response = await call_next(request)
            record(response.status_code, (time.perf_counter() - start) * 1000)
            return response
        except Exception:
            # An unhandled exception is the strongest error signal there is.
            record(_ERROR_MIN_STATUS, (time.perf_counter() - start) * 1000)
            raise
