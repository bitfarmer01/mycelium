"""Per-user voice preferences (feature 57): volume and speed persisted in
SurrealDB so the console re-applies them on every session load.

Clamping logic is pure and named; the only database access goes through
core.db.q (monkeypatchable, like every other core module). One row per user,
keyed by record id via type::record so repeated writes update in place.
"""
from __future__ import annotations

from typing import Any

from core import db

TABLE = "voice_prefs"

DEFAULT_VOLUME = 1.0
DEFAULT_SPEED = 1.0
VOLUME_RANGE = (0.0, 1.0)
SPEED_RANGE = (0.5, 2.0)

MAX_USER_LEN = 128


class PrefsError(ValueError):
    """Invalid preference input (bad user key, non-numeric value)."""


def clamp(value: Any, bounds: tuple[float, float], name: str) -> float:
    """Coerce to float and pin into bounds; non-numeric input is an error, not a default."""
    try:
        v = float(value)
    except (TypeError, ValueError) as e:
        raise PrefsError(f"{name} must be a number") from e
    lo, hi = bounds
    return max(lo, min(hi, v))


def user_key(user: str | None) -> str:
    """Validate and bound the user key; empty or whitespace-only users are rejected."""
    key = (user or "").strip()
    if not key:
        raise PrefsError("user must not be empty")
    return key[:MAX_USER_LEN]


async def get_prefs(user: str | None) -> dict:
    """Stored volume/speed for a user; documented defaults when nothing is stored."""
    key = user_key(user)
    # DEFINE first: a fresh database has no voice_prefs table yet — SurrealDB 4
    # errors on a SELECT against a missing table instead of returning empty rows.
    rows = await db.q(
        f"DEFINE TABLE IF NOT EXISTS {TABLE} SCHEMALESS;"
        f"SELECT * FROM {TABLE} WHERE user = $user LIMIT 1",
        {"user": key},
    )
    if not rows:
        return {"user": key, "volume": DEFAULT_VOLUME, "speed": DEFAULT_SPEED, "stored": False}
    row = rows[0] or {}
    # A partial create can leave a field as NONE in the row: fall back to defaults.
    return {
        "user": key,
        "volume": clamp(row.get("volume") if row.get("volume") is not None else DEFAULT_VOLUME, VOLUME_RANGE, "volume"),
        "speed": clamp(row.get("speed") if row.get("speed") is not None else DEFAULT_SPEED, SPEED_RANGE, "speed"),
        "stored": True,
    }


async def set_prefs(user: str | None, volume: Any = None, speed: Any = None) -> dict:
    """Upsert volume/speed. A None field is omitted from the wire body; the
    coalescing UPSERT keeps the stored value for it, so two rapid partial PUTs
    can never lose an update through a stale read-modify-write race."""
    key = user_key(user)
    v = clamp(volume, VOLUME_RANGE, "volume") if volume is not None else None
    s = clamp(speed, SPEED_RANGE, "speed") if speed is not None else None
    rows = await db.q(
        f"DEFINE TABLE IF NOT EXISTS {TABLE} SCHEMALESS;"
        f'UPSERT type::record("{TABLE}", $user) SET user = $user,'
        " volume = $volume ?? volume, speed = $speed ?? speed, updated = time::now();"
        f"SELECT * FROM {TABLE} WHERE user = $user LIMIT 1;",
        {"user": key, "volume": v, "speed": s},
    )
    row = rows[0] if rows else {}
    return {
        "user": key,
        "volume": clamp(row.get("volume", v), VOLUME_RANGE, "volume"),
        "speed": clamp(row.get("speed", s), SPEED_RANGE, "speed"),
        "stored": True,
    }
