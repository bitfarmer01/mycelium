"""Idempotency keys for /ask (feature 9, workstream A).

A client retrying with the same Idempotency-Key header gets the first answer
back — one answer, one ledger event — instead of a second model call and a
second ledger write. Bounded LRU: keys beyond IDEMPOTENCY_CACHE_MAX are
forgotten oldest-first, so the table cannot grow without limit.
"""
from __future__ import annotations

from collections import OrderedDict

IDEMPOTENCY_CACHE_MAX = 256
_cache: OrderedDict[str, dict] = OrderedDict()


def cached_answer(key: str) -> dict | None:
    """The stored answer for `key` (most recently used), or None."""
    if key not in _cache:
        return None
    _cache.move_to_end(key)
    return _cache[key]


def remember_answer(key: str, answer: dict) -> None:
    _cache[key] = answer
    _cache.move_to_end(key)
    while len(_cache) > IDEMPOTENCY_CACHE_MAX:
        _cache.popitem(last=False)


def clear() -> None:
    """Drop all state — test isolation only."""
    _cache.clear()
