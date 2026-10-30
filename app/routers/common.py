"""Shared request plumbing: lazy core imports, JSON-safe conversion, spoken-answer URLs.

Moved verbatim from app/server.py when the monolith split into segment routers;
importable from every router without circular imports.
"""
from __future__ import annotations

import asyncio
import dataclasses
import importlib
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "app" / "static"


def _mod(name: str):
    """Import core.<name> or raise 503."""
    try:
        return importlib.import_module(f"core.{name}")
    except Exception as e:  # missing or broken sibling module
        raise HTTPException(status_code=503, detail=f"core.{name} unavailable: {type(e).__name__}")


def _jsonable(v):
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return dataclasses.asdict(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        from core.db import plain

        return plain(v)
    except Exception:
        return str(v)


async def _call(fn, *args):
    res = fn(*args)
    if asyncio.iscoroutine(res):
        res = await res
    return res


async def _run(name: str, attr: str, *args):
    mod = _mod(name)
    fn = getattr(mod, attr, None)
    if fn is None:
        raise HTTPException(status_code=503, detail=f"core.{name}.{attr} unavailable")
    return _jsonable(await _call(fn, *args))


def _audio_url(spoken) -> str | None:
    if not spoken or not isinstance(spoken, str):
        return None
    return "/tts?text=" + quote(spoken, safe="")
