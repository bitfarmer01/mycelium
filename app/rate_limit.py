"""Per-session rate limiting for the ask surface (feature 5, workstream A).

The limit is reused from the model gateway's bucket config (`core.llm.BUCKET_LIMITS`):
if the gateway defines an "ask" bucket, sessions share its (max calls, per seconds)
numbers; otherwise the gateway's default bucket applies. One place to tune.

Unlike the gateway's own throttle (which sleeps until a slot frees), an HTTP
request must fail fast: over-limit sessions get an immediate 429 with Retry-After.
"""
from __future__ import annotations

import time

from core.llm import BUCKET_LIMITS

# Sessions draw from the gateway's "ask" bucket when defined, else its default bucket.
ASK_SESSION_LIMIT: tuple[int, float] = BUCKET_LIMITS.get("ask", BUCKET_LIMITS.get("growth", (10, 60.0)))


def active_window(window_hits: list[float], now: float, window_s: float) -> list[float]:
    """Hits still inside the window — the pure core of the limiter."""
    return [t for t in window_hits if now - t < window_s]


class SessionRateLimiter:
    """Fixed-window per-key counter. check() records a hit and says whether it is allowed."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, now: float | None = None) -> bool:
        """Record a hit for `key`; True if within the limit, False when the caller must 429."""
        now = time.monotonic() if now is None else now
        hist = active_window(self._hits.get(key, []), now, self.window_s)
        allowed = len(hist) < self.limit
        if allowed:
            hist.append(now)
        self._hits[key] = hist
        return allowed

    def retry_after(self, key: str, now: float | None = None) -> float:
        """Seconds until the oldest hit leaves the window (lower bound on when a slot frees)."""
        now = time.monotonic() if now is None else now
        hist = active_window(self._hits.get(key, []), now, self.window_s)
        if not hist:
            return 0.0
        return max(0.0, self.window_s - (now - hist[0]))

    def reset(self) -> None:
        """Drop all state — test isolation only."""
        self._hits.clear()


limiter = SessionRateLimiter(*ASK_SESSION_LIMIT)
